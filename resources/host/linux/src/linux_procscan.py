"""Restricted Linux rental provider for the existing nine-method PostMQ SDK.

No systemd/cgroup prerequisite. Native ancestry and pidfds cover supervised
work; device-wide observations remain a separate cleanup condition. Unified
memory is experimental and can never produce verified_clean in this version.
No broker calls, reservation lock, SDK journal or cached clean receipts.
"""

from dataclasses import asdict
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

if __package__:
    from .evidence import Baseline, BOOT, GPU, claim_identity, integer, memory_returned, utc_now
    from .probe import command, ProbeError
    from .rental_native import NativeUnknown, peer, process, receive, send, require_support, job_environment, STOP_DRAIN_FLOOR
else:
    from evidence import Baseline, BOOT, GPU, claim_identity, integer, memory_returned, utc_now
    from probe import command, ProbeError
    from rental_native import NativeUnknown, peer, process, receive, send, require_support, job_environment, STOP_DRAIN_FLOOR


REF = re.compile(r"procscan:([0-9A-HJKMNP-TV-Z]{26}):([1-9][0-9]{0,18}):"
                 r"([0-9a-f-]{36}):([1-9][0-9]{0,9}):([1-9][0-9]{0,19}):([1-9][0-9]{0,19})\Z")
PROVIDER = "linux-procscan-v1"
RPC_SECONDS = 2.0
MAX_DRAIN_SECONDS = 10.0
# Linux UAPI include/uapi/linux/nsfs.h: PID_NS_INIT_INO. Non-init namespaces
# cannot interpret device-wide host PID reports as local process ownership.
INITIAL_PID_NAMESPACE = "pid:[" + str(0xEFFFFFFC) + "]"


class RentalError(RuntimeError):
    pass


def parts(ref):
    m = REF.fullmatch(ref) if isinstance(ref, str) and len(ref) <= 200 else None
    if not m or not BOOT.fullmatch(m[3]):
        raise RentalError("invalid rental native reference")
    claim_identity(m[1], int(m[2]))
    if int(m[2]) > 2**63 - 1 or int(m[4]) >= 2**31:
        raise RentalError("native identity exceeds platform bounds")
    return m[1], int(m[2]), m[3], int(m[4]), int(m[5]), int(m[6])


def clean_verdict(baseline, inspection, sample, *, memory_qualified):
    if inspection.get("state") == "alive":
        return "cleanup_failed", "supervised job descendants remain"
    if inspection.get("state") != "gone" or inspection.get("job_ended") is not True:
        return "unknown", "native job identity or terminal state unavailable"
    if sample.get("unknown_gpu_pids") is None:
        return "unknown", "device process inventory unavailable"
    if sample["unknown_gpu_pids"]:
        return "cleanup_failed", "unclassified device occupancy prevents clean proof"
    if not memory_qualified:
        return "unknown", "unified-memory cleanup observation is not qualified"
    if sample.get("inventory_namespace_qualified") is not True:
        return "unknown", "device PID namespace is not qualified for clean proof"
    returned = memory_returned(baseline, sample)
    if returned is None:
        return "unknown", "device memory identity or observation unavailable"
    if not returned:
        return "residue", "device memory has not returned to its recorded baseline"
    return "verified_clean", "supervised descendants gone; device memory at baseline; no job-local cgroup counter"


class RentalDeviceProbe:
    """Current device-wide readings; no ownership inferred from a bare PID.

    Known baseline occupants are optional owner-authored, birth-bound records.
    They are observed, never controlled; changes refuse clean proof. Graphics
    inventory and hidden host processes remain explicitly unavailable.
    """
    def __init__(self, gpu_uuid, *, mode="discrete", library=None, runner=command,
                 baseline_occupants=(), proc=Path("/proc")):
        if not GPU.fullmatch(gpu_uuid) or mode not in ("discrete", "unified"):
            raise ValueError("explicit device and memory mode required")
        self.gpu, self.mode, self.runner, self.proc = gpu_uuid, mode, runner, Path(proc)
        if library and not Path(library).is_absolute():
            raise ValueError("driver library must be absolute")
        self.library = Path(library).resolve(strict=True) if library else None
        if mode == "unified" and (self.library is None or not self.library.is_absolute()):
            raise ValueError("unified observer needs an explicit driver library")
        self.library_hash = hashlib.sha256(self.library.read_bytes()).hexdigest() if self.library else None
        self.baseline_occupants = {}
        for row in baseline_occupants:
            if (not isinstance(row, dict) or set(row) != {"pid", "start", "used_mib"}
                    or any(type(row[k]) is not int or row[k] < 0 for k in row)
                    or row["pid"] == 0 or row["start"] == 0 or row["pid"] in self.baseline_occupants):
                raise ValueError("baseline occupant needs unique pid, birth and charge")
            self.baseline_occupants[row["pid"]] = dict(row)

    def memory_snapshot(self):
        observed_at = utc_now()  # Conservative start of this potentially slow read.
        boot = (self.proc / "sys/kernel/random/boot_id").read_text().strip()
        if self.mode == "discrete":
            row = self.runner(["nvidia-smi", "-i", self.gpu, "--query-gpu=uuid,memory.free,memory.total",
                               "--format=csv,noheader,nounits"]).strip().split(",")
            if len(row) != 3 or row[0].strip() != self.gpu:
                raise ProbeError("device identity unavailable")
            free, total = (int(x.strip()) * 1024**2 for x in row[1:])
            measurement = "nvidia-smi-discrete"
        else:
            worker = Path(__file__).with_name("cuda_driver_probe.py")
            before_hash = hashlib.sha256(worker.read_bytes()).hexdigest()
            if hashlib.sha256(self.library.read_bytes()).hexdigest() != self.library_hash:
                raise ProbeError("driver library changed")
            r = subprocess.run([sys.executable, "-I", str(worker), "--library", str(self.library),
                                "--gpu-uuid", self.gpu], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=4, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                                       "CUDA_VISIBLE_DEVICES": self.gpu})
            if r.returncode or hashlib.sha256(worker.read_bytes()).hexdigest() != before_hash:
                raise ProbeError("experimental observer failed")
            answer = json.loads(r.stdout)
            if (answer.get("boot_id") != boot or answer.get("gpu_uuid") != self.gpu
                    or answer.get("owned_primary_context_released") is not True
                    or answer.get("allocation_bytes_each") != 0
                    or answer.get("library_sha256") != self.library_hash):
                raise ProbeError("experimental observer identity or release unavailable")
            free, total = answer["free_bytes"], answer["total_bytes"]
            measurement = "experimental-cuMemGetInfo-unified; not cleanup qualified"
        if type(free) is not int or type(total) is not int or not 0 <= free <= total or total == 0:
            raise ProbeError("device memory counters invalid")
        return {"observed_at": observed_at, "boot_id": boot, "gpu_uuid": self.gpu,
                "free_bytes": free, "total_bytes": total, "measurement": measurement}

    def compute_snapshot(self, owned=()):
        observed_at = utc_now()
        boot = (self.proc / "sys/kernel/random/boot_id").read_text().strip()
        owned = {x["pid"]: x["start"] for x in owned}
        apps = self.runner(["nvidia-smi", "-i", self.gpu,
                            "--query-compute-apps=pid,gpu_uuid,used_gpu_memory",
                            "--format=csv,noheader,nounits"])
        unknown = []; seen_baseline = set()
        for line in apps.splitlines():
            row = [x.strip() for x in line.split(",")]
            if len(row) != 3 or row[1] != self.gpu or not row[0].isdecimal() or not row[2].isdecimal():
                raise ProbeError("device process inventory ambiguous")
            pid, charge = int(row[0]), int(row[2])
            current = process(pid, self.proc)
            known = self.baseline_occupants.get(pid)
            if owned.get(pid) == current["start"]:
                continue
            if known and known["start"] == current["start"] and charge == known["used_mib"]:
                seen_baseline.add(pid)
                continue
            unknown.append(pid)
        if seen_baseline != set(self.baseline_occupants):
            raise ProbeError("baseline device occupants changed or disappeared")
        return {"observed_at": observed_at, "boot_id": boot, "gpu_uuid": self.gpu,
                "unknown_gpu_pids": sorted(unknown), "graphics_process_inventory": "unavailable",
                "inventory_namespace_qualified": os.readlink(self.proc / "self/ns/pid") == INITIAL_PID_NAMESPACE}

    def snapshot(self, owned=()):
        # Preserve the existing all-or-nothing receipt/preflight contract.
        memory = self.memory_snapshot()
        compute = self.compute_snapshot(owned)
        if compute["boot_id"] != memory["boot_id"]:
            raise ProbeError("host boot changed during observation")
        return {**memory, **compute, "observed_at": memory["observed_at"]}


class ProcscanProvider:
    def __init__(self, baseline, *, state_root, required_bytes, reserve_bytes,
                 probe=None, memory_mode="discrete", provider="owned", instance_id=None,
                 drain_seconds=8.0, allowed_job_env=()):
        self.baseline = baseline
        self.root = Path(state_root)
        if (not self.root.is_absolute() or self.root.resolve() != self.root
                or len(os.fsencode(self.root)) > 45):
            raise ValueError("short canonical absolute native state root required")
        self.required = integer(required_bytes, "required_bytes", 1)
        self.reserve = integer(reserve_bytes, "reserve_bytes", 1)
        if memory_mode not in ("discrete", "unified") or provider not in ("owned", "vast"):
            raise ValueError("explicit native profile required")
        if (type(drain_seconds) not in (int, float) or not math.isfinite(drain_seconds)
                or not 0 <= drain_seconds <= MAX_DRAIN_SECONDS):
            raise ValueError("native drain exceeds SDK budget")
        if provider == "vast" and (not isinstance(instance_id, str) or not instance_id.isdecimal()):
            raise ValueError("Vast instance id required")
        self.mode, self.provider, self.instance_id = memory_mode, provider, instance_id
        self.probe = probe or RentalDeviceProbe(baseline.gpu_uuid, mode=memory_mode)
        self.memory_qualified = memory_mode == "discrete"  # Configuration cannot certify unified-memory cleanup.
        self.drain_seconds = drain_seconds
        job_environment({}, allowed_job_env)
        self.allowed_job_env = tuple(allowed_job_env)
        self.created, self.children = set(), {}
        self.failed_children = {}
        self.lock = threading.RLock()

    def parse_ref(self, ref):
        return parts(ref)

    def native_config(self):
        return {}

    def native_environment(self):
        return {}

    def cleanup_sample(self, inspection, ref):
        return self.probe.snapshot(inspection["members"])

    def cleanup_verdict(self, inspection, sample):
        return clean_verdict(self.baseline, inspection, sample, memory_qualified=self.memory_qualified)

    def _host(self):
        require_support()
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != self.baseline.boot_id:
            raise RentalError("host boot differs")
        if self.provider == "vast" and os.environ.get("CONTAINER_ID") != self.instance_id:
            raise RentalError("provider instance differs")

    def declare(self):
        return {"receipt_provider": PROVIDER,
                "provider_host_key": self.baseline.gpu_uuid,
                "identity_kind": "device_uuid", "identity_key": self.baseline.gpu_uuid,
                "enforcement": {"ownership": "advisory", "descendant_supervision": "advisory",
                                "revocation": "advisory", "memory_accounting": "advisory"},
                "capabilities": {"backend": "cuda", "memory_mode": self.mode,
                                 "job_local_cgroup_limits": "unavailable", "native_provider": PROVIDER,
                                 "cleanup_memory_qualified": self.memory_qualified,
                                 "process_control": "birth-bound-pidfd/subreaper",
                                 "process_accounting": "supervised descendants only",
                                 "graphics_inventory": "unavailable", "host_hidden_processes": "unavailable",
                                 "job_environment": "allowlist; same-UID /proc environment access not contained",
                                 "job_stdio": "discarded"}}

    def preflight(self, claim):
        try:
            claim_identity(claim["claim_id"], claim["fence"])
            if claim.get("state") != "granted":
                raise RentalError("claim is not granted")
            self._host()
            sample = self.probe.snapshot()
            if (sample.get("boot_id"), sample.get("gpu_uuid"), sample.get("total_bytes")) != (
                    self.baseline.boot_id, self.baseline.gpu_uuid, self.baseline.total_bytes):
                raise RentalError("device identity differs")
            if not isinstance(sample.get("unknown_gpu_pids"), list):
                raise RentalError("device occupancy unavailable")
            if sample.get("unknown_gpu_pids"):
                return {"result": "refused", "reason": "unclassified GPU occupancy", "cleanup": "pristine", "health": "degraded"}
            if self.memory_qualified and memory_returned(self.baseline, sample) is False:
                return {"result": "refused", "reason": "device below recorded baseline", "cleanup": "residue", "health": "unhealthy"}
            if sample["free_bytes"] < self.required + self.reserve:
                return {"result": "refused", "reason": "insufficient observed device headroom", "cleanup": "pristine"}
            return {"result": "admitted"}
        except Exception:
            return {"result": "refused", "reason": "native capacity observation unavailable", "cleanup": "pristine", "health": "unknown"}

    def _directory(self, claim, fence):
        claim_identity(claim, fence)
        if fence > 2**63 - 1:
            raise RentalError("fence exceeds native range")
        return self.root / (claim + "-f" + str(fence))

    def journal_ref(self, handle):
        if not isinstance(handle, dict):
            raise RentalError("native handle required")
        value = self.parse_ref(handle.get("ref"))
        if value[:2] != (handle.get("claim_id"), handle.get("fence")) or type(handle.get("fence")) is not int:
            raise RentalError("native handle claim or fence differs")
        return handle["ref"]

    def _rpc(self, ref, op="inspect", **values):
        claim, fence, boot, pid, start, inode = self.parse_ref(ref)
        self._host()
        if boot != self.baseline.boot_id:
            raise RentalError("reference boot differs")
        directory = self._directory(claim, fence)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            s = os.fstat(fd)
            if s.st_ino != inode or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
                raise RentalError("native directory identity differs")
            if process(pid)["start"] != start:
                raise RentalError("native guard birth differs")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(RPC_SECONDS + max(values.get("grace", 0), STOP_DRAIN_FLOOR) + 1
                                      if op == "stop" else RPC_SECONDS)
                connection.connect(str(directory / "guard.sock"))
                if peer(connection)[:2] != (pid, os.getuid()) or process(pid)["start"] != start:
                    raise RentalError("native server identity differs")
                send(connection, {"ref": ref, "op": op, **values})
                result = receive(connection)
            if result.get("ref") != ref or result.get("error"):
                raise RentalError("native result unverified")
            return result
        finally:
            os.close(fd)

    def container(self, claim_id, fence, argv, env, release_fd):
        self._host(); claim_identity(claim_id, fence)
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            raise RentalError("native child wait status must be retained")
        if (type(release_fd) is not int or release_fd < 3 or not stat.S_ISFIFO(os.fstat(release_fd).st_mode)
                or fcntl.fcntl(release_fd, fcntl.F_GETFL) & (os.O_ACCMODE | os.O_NONBLOCK) != os.O_RDONLY):
            raise RentalError("SDK read-side release pipe required")
        if (not argv or any(not isinstance(x, str) or "\0" in x for x in argv)
                or not argv[0] or not isinstance(env, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v or "=" in k for k, v in env.items())):
            raise RentalError("explicit native argv/environment required")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        s = self.root.stat()
        if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise RentalError("native state root must be private")
        directory = self._directory(claim_id, fence); directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        if len(os.fsencode(directory / "guard.sock")) >= 108:
            raise RentalError("native socket path exceeds limit")
        data_r, data_w = os.pipe(); ready_r, ready_w = os.pipe(); pub_r, pub_w = os.pipe()
        opened = {data_r, data_w, ready_r, ready_w, pub_r, pub_w}
        child = None; published = False; ref = None
        try:
            job_env = job_environment(env, self.allowed_job_env)
            job_env["CUDA_VISIBLE_DEVICES"] = self.baseline.gpu_uuid
            payload = json.dumps({"claim_id": claim_id, "fence": fence, "argv": list(argv), "env": job_env,
                                  "allowed_job_env": self.allowed_job_env, **self.native_config()}).encode()
            if len(payload) > 1048576:
                raise RentalError("native launch configuration exceeds bound")
            child = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).with_name("rental_guard.py")),
                                      str(data_r), str(release_fd), str(ready_w), str(pub_r), str(directory)],
                                     pass_fds=(data_r, release_fd, ready_w, pub_r), start_new_session=True,
                                     env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **self.native_environment()},
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for fd in (data_r, ready_w, pub_r):
                os.close(fd); opened.remove(fd)
            while payload:
                n = os.write(data_w, payload)
                if n <= 0:
                    raise RentalError("native configuration pipe failed")
                payload = payload[n:]
            os.close(data_w); opened.remove(data_w)
            deadline = time.monotonic() + 10; raw = b""
            while not raw.endswith(b"\n"):
                ready, _, _ = select.select([ready_r], [], [], max(0, deadline - time.monotonic()))
                if not ready or len(raw) >= 4096:
                    raise RentalError("native supervisor did not prepare")
                chunk = os.read(ready_r, 4096 - len(raw))
                if not chunk:
                    raise RentalError("native supervisor ready channel ended")
                raw += chunk
            metadata = json.loads(raw)
            publication_deadline = time.monotonic() + 6
            ref = metadata["ref"]; value = self.parse_ref(ref)
            if value[:3] != (claim_id, fence, self.baseline.boot_id) or value[3] != child.pid or value[5] != directory.stat().st_ino:
                raise RentalError("native ready identity differs")
            current = self._rpc(ref)
            prepared = process(metadata["prepared_pid"])
            if (prepared["start"] != metadata["prepared_start"] or prepared["ppid"] != child.pid
                    or not any(x["pid"] == prepared["pid"] and x["start"] == prepared["start"] for x in current["members"])):
                raise RentalError("prepared child identity differs")
            if time.monotonic() >= publication_deadline:
                raise RentalError("native publication observation expired")
            with self.lock:
                self.children[ref] = child; self.created.add(ref)
            os.write(pub_w, b"x")  # Last step before returning to the SDK's journal/release path.
            published = True
            return {"ref": ref, "claim_id": claim_id, "fence": fence}
        except BaseException:
            for fd in opened:
                os.close(fd)
            opened.clear()
            if child is not None and not published:
                with self.lock:
                    self.created.discard(ref); self.children.pop(ref, None)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with self.lock:
                        self.failed_children[str(directory)] = child
            if child is None and directory.exists():
                directory.rmdir()  # Exclusive epoch, no helper was started.
            raise
        finally:
            for fd in opened:
                os.close(fd)
            # SDK owns release_fd. EOF on publication abandons an unpublished child.

    def inspect(self, handle_or_ref):
        try:
            ref = handle_or_ref if isinstance(handle_or_ref, str) else self.journal_ref(handle_or_ref)
            return self._rpc(ref)
        except Exception:
            return {"state": "unverifiable", "job_exit": None, "job_signal": None,
                    "observed_at": utc_now(), "reason": "native rental evidence unavailable"}

    def stop(self, handle, grace_seconds):
        ref = self.journal_ref(handle)
        with self.lock:
            if ref not in self.created:
                raise RentalError("stop requires matching creation or journal authority")
        if type(grace_seconds) not in (int, float) or not math.isfinite(grace_seconds) or not 0 <= grace_seconds <= 10:
            raise RentalError("stop drain bound invalid")
        return self._rpc(ref, "stop", grace=grace_seconds)

    def _verdict(self, ref):
        tail = []; stable = 0; deadline = time.monotonic() + self.drain_seconds
        outcome, detail = "unknown", "native cleanup observation unavailable"
        while True:
            try:
                inspection = self._rpc(ref)
                sample = self.cleanup_sample(inspection, ref)
                outcome, detail = self.cleanup_verdict(inspection, sample)
                tail.append({"observed_at": utc_now(), "sample_observed_at": sample.get("observed_at"),
                             "summary": outcome + ": " + detail})
                stable = stable + 1 if outcome == "verified_clean" else 0
                if stable >= 3:
                    dismissed = self._rpc(ref, "dismiss")
                    if dismissed.get("dismissed") is not True:
                        raise RentalError("native dismissal unverified")
                    child = self.children.get(ref)
                    if child is not None:
                        if child.wait(timeout=2) != 0:
                            raise RentalError("native supervisor teardown failed")
                    closing = time.monotonic() + 2
                    native_pid, native_start = self.parse_ref(ref)[3:5]
                    while True:
                        try:
                            current = process(native_pid)
                            live = current["start"] == native_start and current["state"] != "Z"
                        except FileNotFoundError:
                            live = False
                        if not live and not self._directory(*self.parse_ref(ref)[:2]).exists():
                            break
                        if time.monotonic() >= closing:
                            raise RentalError("native supervisor collection unproven")
                        time.sleep(0.05)
                    with self.lock:
                        self.created.discard(ref); self.children.pop(ref, None)
                    return outcome, detail, tail[-4:]
            except Exception:
                stable = 0
                outcome, detail = "unknown", "native cleanup observation unavailable"
            if time.monotonic() >= deadline:
                if outcome == "verified_clean":
                    outcome, detail = "unknown", "stable device return window incomplete"
                return outcome, detail, tail[-4:]
            time.sleep(0.1)

    def release_receipt(self, handle, execution):
        outcome, detail, tail = self._verdict(self.journal_ref(handle))
        return {"cleanup": outcome, "detail": detail, "telemetry_tail": tail}

    def assess_container(self, handle_or_ref, *, limits_probe=None):
        """Observational proposed-profile facts, never a release/admission verdict.

        No dismiss, stop, SDK journal/creation-authority change or broker call.
        The nine-method protocol and unified-memory unknown receipt stay intact.
        The unified memory read uses a transient primary CUDA context even at
        zero diagnostic allocation; this is not a zero-device-impact operation.
        Malformed caller handles/refs raise. Poll no faster than once per two
        seconds and avoid concurrent assessment during stop/dismiss: native
        inspection shares the guard's serial socket and membership lock.
        """
        if __package__:
            from .container_assessment import LocalBinding, assess, visible_limits
        else:
            from container_assessment import LocalBinding, assess, visible_limits
        ref = handle_or_ref if isinstance(handle_or_ref, str) else self.journal_ref(handle_or_ref)
        binding = LocalBinding(*self.parse_ref(ref), self.baseline.gpu_uuid, self.baseline.total_bytes)
        inspection, sample, limits, compute = {}, {}, {}, {}
        try:
            self._host()
            if binding.boot_id != self.baseline.boot_id:
                raise RentalError("reference boot differs")
        except Exception:
            return assess(binding, inspection, sample, limits,
                          memory_mode=self.mode, required_bytes=self.required, reserve_bytes=self.reserve)
        try:
            at = utc_now()
            inspection = {**self._rpc(ref), "observed_at": at}
        except Exception:
            pass
        try:
            sample = self.probe.memory_snapshot()
        except Exception:
            pass
        try:
            compute = self.probe.compute_snapshot(inspection.get("members", ()))
        except Exception:
            pass
        try:
            limits = (limits_probe or visible_limits)()
        except Exception:
            pass
        return assess(binding, inspection, sample, limits,
                      compute_sample=compute, memory_mode=self.mode,
                      required_bytes=self.required, reserve_bytes=self.reserve)

    def reconcile_verdict(self, entries, claim_id, fence):
        matches = [e for e in entries if getattr(e, "claim", None) == claim_id and getattr(e, "fence", None) == fence
                   and getattr(e, "kind", None) == "launched"]
        if not matches or len({getattr(e, "ref", None) for e in matches}) != 1:
            return "unknown", "no unique native rental reference in SDK journal", None
        ref = matches[-1].ref
        try:
            if self.parse_ref(ref)[:2] != (claim_id, fence):
                raise RentalError("journal native identity differs")
            with self.lock:
                self.created.add(ref)
            self.stop({"ref": ref, "claim_id": claim_id, "fence": fence}, 0)
            return self._verdict(ref)
        except Exception:
            return "unknown", "native rental reconciliation unavailable", None

    def telemetry(self):
        try:
            self._host()
            sample = self.probe.snapshot()
            health = "unknown" if not self.memory_qualified else "degraded" if sample["unknown_gpu_pids"] else "healthy"
            return {**sample, "profile": PROVIDER, "memory_qualified": self.memory_qualified,
                    "baseline": asdict(self.baseline)}, health
        except Exception:
            return {"observed_at": utc_now(), "profile": PROVIDER, "observation": "unavailable"}, "unknown"


def factory():
    from .evidence import selected_config
    config = json.loads(Path(selected_config(os.environ)).read_text())
    baseline = Baseline(**config["baseline"])
    probe = RentalDeviceProbe(baseline.gpu_uuid, mode=config["memory_mode"], library=config.get("cuda_library"),
                              baseline_occupants=config.get("baseline_occupants", ()))
    return ProcscanProvider(baseline, state_root=config["native_state_root"], probe=probe,
                           memory_mode=config["memory_mode"], provider=config.get("provider", "owned"),
                           instance_id=config.get("instance_id"), required_bytes=config["required_bytes"],
                           reserve_bytes=config["reserve_bytes"], drain_seconds=config.get("drain_seconds", 8),
                           allowed_job_env=config.get("allowed_job_env", ()))
