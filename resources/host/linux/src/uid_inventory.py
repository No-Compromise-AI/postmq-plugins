"""Finite read-only /proc descriptor collector under the target's existing UID.

No device is opened, no process is signalled, and no global permission changes.
The parent owns the deadline, complete PID set and source binding. This helper
exists only during one scan and is collected before the final PID-set check.
"""
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

MAX_TARGETS = 512
MAX_FDS = 8192
MAX_FRAME = 131072


class InventoryUnknown(RuntimeError):
    pass


def integer(value, minimum=0, maximum=2**31-1):
    return type(value) is int and minimum <= value <= maximum


def identity(pid, proc=Path("/proc")):
    if not integer(pid, 1):
        raise InventoryUnknown("invalid target PID")
    folder = Path(proc) / str(pid)
    first = (folder / "stat").read_text()
    fields = first[first.rfind(")") + 2:].split()
    if not first.startswith(str(pid) + " (") or len(fields) < 20:
        raise InventoryUnknown("invalid process birth")
    start = int(fields[19])
    result = {"pid": pid, "start": start}
    for name in ("Uid", "Gid"):
        rows = [line.split()[1:] for line in (folder / "status").read_text().splitlines()
                if line.startswith(name + ":")]
        if len(rows) != 1 or len(rows[0]) != 4 or any(not x.isdecimal() for x in rows[0]):
            raise InventoryUnknown("process credentials unavailable")
        result[name.lower()] = list(map(int, rows[0]))
    if (folder / "stat").read_text() != first:
        # CPU counters legitimately change: only recheck the birth field.
        second = (folder / "stat").read_text()
        tail = second[second.rfind(")") + 2:].split()
        if not second.startswith(str(pid) + " (") or len(tail) < 20 or int(tail[19]) != start:
            raise InventoryUnknown("process birth changed")
    return result


def validate(plan):
    if not isinstance(plan, dict) or set(plan) != {"uid", "gid", "targets", "devices", "deadline"}:
        raise InventoryUnknown("invalid inventory plan")
    uid, gid, targets, devices, deadline = (plan[k] for k in ("uid", "gid", "targets", "devices", "deadline"))
    if not integer(uid) or not integer(gid) or not isinstance(targets, list) or not 0 < len(targets) <= MAX_TARGETS:
        raise InventoryUnknown("invalid inventory credentials or target count")
    seen = set()
    for row in targets:
        if (not isinstance(row, dict) or set(row) != {"pid", "start", "uid", "gid"}
                or not integer(row["pid"], 1) or not integer(row["start"], 1, 2**64-1)
                or row["pid"] in seen or row["uid"] != [uid]*4 or row["gid"] != [gid]*4
                or any(type(x) is not int for key in ("uid", "gid") for x in row[key])):
            raise InventoryUnknown("ambiguous target identity")
        seen.add(row["pid"])
    if (not isinstance(devices, list) or not 0 < len(devices) <= 256
            or any(not isinstance(pair, list) or len(pair) != 2
                   or any(not integer(x, 0, 2**32-1) for x in pair) for pair in devices)
            or len({tuple(pair) for pair in devices}) != len(devices)):
        raise InventoryUnknown("invalid device inventory")
    if type(deadline) not in (int, float) or not 0 < deadline - time.monotonic() <= 1.0:
        raise InventoryUnknown("inventory deadline unavailable")
    return plan


def protect(uid, gid):
    """Drop only this child's credentials and capabilities, then verify them."""
    lib = ctypes.CDLL(None, use_errno=True)
    def prctl(option, value):
        if lib.prctl(option, value, 0, 0, 0) != 0:
            raise InventoryUnknown("collector protection unavailable")
    prctl(38, 1)  # PR_SET_NO_NEW_PRIVS; cannot subsequently gain privileges.
    prctl(4, 0)   # PR_SET_DUMPABLE; set again after credential changes.
    if os.geteuid() == 0:
        os.setgroups([])
        os.setresgid(gid, gid, gid)
        os.setresuid(uid, uid, uid)
    elif os.getresuid() != (uid,)*3 or os.getresgid() != (gid,)*3:
        raise InventoryUnknown("collector cannot select target credentials")
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]
    if lib.capset(ctypes.byref(Header(0x20080522, 0)), ctypes.byref((Data*2)())) != 0:
        raise InventoryUnknown("collector capability drop failed")
    prctl(4, 0)
    status = Path("/proc/self/status").read_text().splitlines()
    values = dict(line.split(":", 1) for line in status if ":" in line)
    if (os.getresuid() != (uid,)*3 or os.getresgid() != (gid,)*3
            or values["Uid"].split() != [str(uid)]*4 or values["Gid"].split() != [str(gid)]*4
            or any(int(values[key], 16) for key in ("CapInh", "CapPrm", "CapEff", "CapAmb"))
            or int(values["NoNewPrivs"]) != 1 or lib.prctl(3, 0, 0, 0, 0) != 0):
        raise InventoryUnknown("collector protection did not apply")


def inherited():
    # Popen uses private pipes and close_fds=True. Some Python/libffi builds
    # retain a regular library FD during import: close it before changing UID.
    # Any device or other unexpected descriptor refuses the whole observation.
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        try:
            info = os.fstat(fd)
        except OSError as error:
            if fd > 2 and error.errno == errno.EBADF:
                continue  # listdir's own, already closed directory descriptor.
            raise
        if fd > 2 and stat.S_ISREG(info.st_mode):
            os.close(fd)
        elif fd > 2 or not stat.S_ISFIFO(info.st_mode):
            raise InventoryUnknown("unexpected inherited collector descriptor")


def observe(plan):
    numbers = {tuple(pair) for pair in plan["devices"]}
    result = []
    for expected in plan["targets"]:
        if time.monotonic() >= plan["deadline"]:
            raise InventoryUnknown("descriptor deadline exceeded")
        pid = expected["pid"]
        if identity(pid) != expected:
            raise InventoryUnknown("target identity changed")
        folder = Path("/proc") / str(pid) / "fd"
        names = list(folder.iterdir())
        if len(names) > MAX_FDS:
            raise InventoryUnknown("descriptor count exceeds bound")
        holder = False
        for path in names:
            if time.monotonic() >= plan["deadline"]:
                raise InventoryUnknown("descriptor deadline exceeded")
            if not path.name.isdecimal():
                raise InventoryUnknown("invalid descriptor name")
            try:
                info = path.stat()
            except FileNotFoundError:
                continue  # closed descriptor; identity is still required below.
            if stat.S_ISCHR(info.st_mode) and (os.major(info.st_rdev), os.minor(info.st_rdev)) in numbers:
                holder = True
        if identity(pid) != expected:
            raise InventoryUnknown("target identity changed")
        result.append({"identity": expected, "holder": holder})
    return result


def collect(uid, gid, targets, devices, deadline):
    """Parent API. Numeric metadata only; no credential or environment handoff."""
    plan = validate({"uid": uid, "gid": gid, "targets": targets, "devices": devices, "deadline": deadline})
    payload = json.dumps(plan, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(payload) > MAX_FRAME:
        raise InventoryUnknown("inventory plan exceeds bound")
    answer = subprocess.run([sys.executable, "-I", "-B", "-S", str(Path(__file__).resolve())],
                            input=payload, capture_output=True, env={}, cwd="/", close_fds=True,
                            timeout=max(0.001, deadline-time.monotonic()), check=False)
    if (answer.returncode != 0 or answer.stderr or len(answer.stdout) > MAX_FRAME
            or answer.stdout.count(b"\n") != 1 or not answer.stdout.endswith(b"\n")):
        raise InventoryUnknown("collector incomplete or failed")
    rows = json.loads(answer.stdout)
    if not isinstance(rows, list) or len(rows) != len(targets):
        raise InventoryUnknown("collector response shape changed")
    for row, expected in zip(rows, targets):
        if (not isinstance(row, dict) or set(row) != {"identity", "holder"}
                or row["identity"] != expected or type(row["holder"]) is not bool):
            raise InventoryUnknown("collector response identity changed")
        validate({**plan, "targets": [row["identity"]]})
    if time.monotonic() >= deadline or any(identity(row["pid"]) != row for row in targets):
        raise InventoryUnknown("target changed before collector completion")
    return [row["identity"]["pid"] for row in rows if row["holder"]]


def main():
    try:
        inherited()
        raw = sys.stdin.buffer.read(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
            raise InventoryUnknown("invalid inventory frame")
        plan = validate(json.loads(raw))
        protect(plan["uid"], plan["gid"])
        result = observe(plan)
        sys.stdout.buffer.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Never emit partial rows, target contents or a traceback with metadata.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
