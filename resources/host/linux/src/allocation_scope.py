"""Read-only allocation identity and visible device-handle inventory.

This is deliberately not physical-host inventory. It never translates NVML
host PIDs into a container PID namespace, controls processes, or opens a CUDA
context. Inaccessible/changing evidence refuses the observation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import time

if __package__:
    from . import rental_native as native
else:
    # The native guard runs with Python -I, without this directory on sys.path.
    # Load the fixed sibling by path, just as the guard loads this helper.
    spec = importlib.util.spec_from_file_location("allocation_scope_native", Path(__file__).with_name("rental_native.py"))
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)

PROVIDER = "linux-procscan-v2"
MAX_PROCESSES = 4096
MAX_FDS = 8192
SOURCE_FILES = ("allocation_scope.py", "managed_allocation.py", "linux_procscan.py",
                "rental_guard.py", "rental_native.py", "guard.py", "evidence.py",
                "container_assessment.py", "probe.py", "cuda_driver_probe.py", "sdk_provider.py", "__init__.py")


def devices(root=Path("/dev")):
    root = Path(root)
    paths = list(root.glob("nvidia*")) + list((root / "nvidia-caps").glob("*")) + list((root / "dri").glob("*"))
    result = {}
    for path in paths:
        info = path.stat()
        if stat.S_ISCHR(info.st_mode):
            result[str(path.relative_to(root))] = [os.major(info.st_rdev), os.minor(info.st_rdev)]
    if sum(bool(re.fullmatch(r"nvidia[0-9]+", name)) for name in result) != 1:
        raise native.NativeUnknown("exactly one visible NVIDIA GPU required")
    return dict(sorted(result.items()))


def binding(profile, *, proc=Path("/proc"), dev=Path("/dev"), cgroup=Path("/sys/fs/cgroup")):
    if (not isinstance(profile, dict) or set(profile) != {"provider", "instance_id", "gpu_uuid"}
            or profile["provider"] != "vast" or not isinstance(profile["instance_id"], str)
            or not profile["instance_id"].isdecimal()):
        raise native.NativeUnknown("explicit Vast allocation profile required")
    if os.environ.get("CONTAINER_ID") != profile["instance_id"]:
        raise native.NativeUnknown("Vast allocation identity changed")
    proc, cgroup = Path(proc), Path(cgroup)
    pidns = os.readlink(proc / "self/ns/pid")
    if pidns != os.readlink(proc / "1/ns/pid"):
        raise native.NativeUnknown("proc mount does not describe the allocation PID namespace")
    mounts = []
    for line in (proc / "self/mountinfo").read_text().splitlines():
        first, separator, last = line.partition(" - ")
        fields, tail = first.split(), last.split()
        if separator and len(fields) >= 6 and len(tail) >= 3 and fields[4] == str(proc) and tail[0] == "proc":
            options = fields[5].split(",") + tail[2].split(",")
            if any(x.startswith("hidepid=") and x not in ("hidepid=0", "hidepid=off") for x in options):
                raise native.NativeUnknown("proc visibility is restricted")
            mounts.append(line)
    if len(mounts) != 1:
        raise native.NativeUnknown("unique proc mount unavailable")
    if (proc / "self/cgroup").read_text().strip() != "0::/":
        raise native.NativeUnknown("visible cgroup namespace-root profile required")
    sources = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    value = {**profile, "receipt_provider": PROVIDER,
             "boot_id": (proc / "sys/kernel/random/boot_id").read_text().strip(),
             "pid_namespace": pidns, "mount_namespace": os.readlink(proc / "self/ns/mnt"),
             "namespace_init_start": native.process(1, proc)["start"], "uid": os.getuid(),
             "cgroup_inode": cgroup.stat().st_ino, "proc_mount": mounts[0],
             "devices": devices(dev), "source_sha256": sources}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def visible_holders(device_nodes, *, guard=None, proc=Path("/proc")):
    """Complete visible /proc FD scan, birth-bound on both sides.

    Only the exact authenticated, source-bound supervisor can be excluded: it
    verifies before becoming non-dumpable that it inherited no GPU handles and
    never loads a GPU runtime. Every other inaccessible process is unknown.
    Exits, births and PID-set changes invalidate a scan. Descriptor opens after
    a process was scanned are not atomic with this observation; the next stable
    scan catches a persistent holder. This is advisory, not hostile containment.
    No numeric PID is signalled.
    """
    proc = Path(proc)
    deadline = time.monotonic() + 1.0
    numbers = {(major, minor) for major, minor in device_nodes.values()}
    before = sorted(int(p.name) for p in proc.iterdir() if p.name.isdecimal())
    if not before or len(before) > MAX_PROCESSES:
        raise native.NativeUnknown("visible process inventory exceeds bound")
    unknown = []
    for pid in before:
        if time.monotonic() >= deadline:
            raise native.NativeUnknown("visible inventory time budget exceeded")
        row = native.process(pid, proc)
        if guard is not None and (pid, row["start"]) == guard:
            continue
        folder = proc / str(pid) / "fd"
        descriptors = list(folder.iterdir())
        if len(descriptors) > MAX_FDS:
            raise native.NativeUnknown("visible descriptor inventory exceeds bound")
        for path in descriptors:
            if time.monotonic() >= deadline:
                raise native.NativeUnknown("visible descriptor time budget exceeded")
            # stat follows the FD magic link; aliases cannot hide a device's rdev.
            try:
                info = path.stat()
            except FileNotFoundError:
                # A closed descriptor (including this scan's directory FD)
                # no longer holds a device. Process birth is checked below.
                if not path.exists():
                    continue
                raise
            if stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) in numbers:
                unknown.append(pid)
                break
        if native.process(pid, proc)["start"] != row["start"]:
            raise native.NativeUnknown("visible process birth changed")
    if before != sorted(int(p.name) for p in proc.iterdir() if p.name.isdecimal()):
        raise native.NativeUnknown("visible process set changed during scan")
    return sorted(set(unknown))


def supervisor_gpu_free(device_nodes):
    if visible_own_holders(device_nodes):
        raise native.NativeUnknown("supervisor inherited a device handle")


def visible_own_holders(device_nodes):
    numbers = {tuple(value) for value in device_nodes.values()}
    # Opening this directory does not add a device FD; sockets/pipes are allowed.
    for path in Path("/proc/self/fd").iterdir():
        try:
            info = path.stat()
        except FileNotFoundError:
            # iterdir's own directory descriptor can already have closed.
            if not path.exists():
                continue
            raise
        if stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) in numbers:
            return True
    return False
