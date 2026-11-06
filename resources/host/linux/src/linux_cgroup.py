"""Native provider for the PostMQ SDK provider protocol.

No PostMQ calls, locks, journal writes, credentials or SSH are implemented here.
The SDK owns admission, the host gate, journal fsync and the release pipe.
"""

from dataclasses import asdict
from pathlib import Path, PurePosixPath
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time

if __package__:
    from .evidence import (BOOT, Baseline, claim_identity, cleanup_verdict, integer,
                           memory_returned, positive_seconds, utc_now)
    from .probe import NvidiaProbe, ProbeError, command
elif __name__ == "linux_cgroup":  # Direct source-test import only.
    from evidence import (BOOT, Baseline, claim_identity, cleanup_verdict, integer,
                          memory_returned, positive_seconds, utc_now)
    from probe import NvidiaProbe, ProbeError, command
else:
    raise ImportError("load this provider through sdk_provider.py:factory")


class ProviderError(RuntimeError):
    pass


REF = re.compile(r"scope:([0-9A-HJKMNP-TV-Z]{26}):([1-9][0-9]*):([0-9a-f-]{36}):([1-9][0-9]*)\Z")
MAX_DRAIN_SECONDS = 20.0
TELEMETRY_SCOPE_SECONDS = 3.0


class LinuxCgroupProvider:
    def __init__(self, baseline, *, required_bytes, reserve_bytes, charge_tolerance=1048576,
                 stable_samples=3, sample_interval=1.0, drain_seconds=15.0,
                 cgroup_root=Path("/sys/fs/cgroup"), proc_root=Path("/proc"),
                 runner=command, probe=None, popen=subprocess.Popen, clock=time.monotonic,
                 sleep=time.sleep, uid=None, memory_max=None):
        self.baseline = baseline
        self.required = integer(required_bytes, "required_bytes", 1)
        self.reserve = integer(reserve_bytes, "reserve_bytes")
        self.charge_tolerance = integer(charge_tolerance, "charge_tolerance")
        self.stable_samples = integer(stable_samples, "stable_samples", 2)
        self.interval = positive_seconds(sample_interval)
        self.drain_seconds = positive_seconds(drain_seconds)
        if self.drain_seconds > MAX_DRAIN_SECONDS:
            raise ValueError("drain window exceeds native SDK budget margin")
        if self.drain_seconds < self.interval * (self.stable_samples - 1):
            raise ValueError("drain window cannot fit the required stable samples")
        self.root, self.proc = Path(cgroup_root).resolve(), Path(proc_root).resolve()
        self.runner, self.popen, self.clock, self.sleep = runner, popen, clock, sleep
        self.uid = os.getuid() if uid is None else integer(uid, "uid")
        self.probe = probe or NvidiaProbe(baseline.gpu_uuid, proc_root=self.proc)
        self.memory_max = None if memory_max is None else integer(memory_max, "memory_max", 1)
        self.children = {}  # process objects never cross the provider/SDK interface
        self.owned = set()  # stop never accepts an arbitrary caller-authored handle
        self.created = {}  # authorization survives pruning of observed occupancy
        self.jobs = {}  # native ref -> the exact Popen, never a caller-supplied pid
        self.completed = {}  # bounded native-ref completion metadata
        self._process_lock = threading.RLock()

    def declare(self):
        return {"receipt_provider": "linux-cgroup", "identity_kind": "device_uuid",
                "identity_key": self.baseline.gpu_uuid, "provider_host_key": self.baseline.gpu_uuid,
                "enforcement": {"ownership": "advisory", "descendant_supervision": "advisory",
                                "memory_accounting": "advisory", "revocation": "advisory"},
                "capabilities": {"backend": "cuda", "os": "linux", "cgroup": "v2",
                                 "memory_measurement": "nvidia-smi-discrete",
                                 "graphics_process_inventory": "unavailable"}}

    def _boot(self):
        value = (self.proc / "sys/kernel/random/boot_id").read_text().strip()
        if not BOOT.fullmatch(value):
            raise ProviderError("host boot identity unavailable")
        return value

    @staticmethod
    def _unit(claim_id):
        return "postmq-claim-" + claim_id + ".scope"

    def _scope_path(self, claim_id):
        unit = self._unit(claim_id)
        return self._scope_path_for_unit(unit)

    def _scope_path_for_unit(self, unit, expected_description=None):
        out = self.runner(["systemctl", "--user", "show", unit,
                           "--property=ControlGroup", "--property=LoadState", "--property=Description"])
        properties = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if properties.get("LoadState") == "not-found" and properties.get("ControlGroup", "") == "":
            return None
        if properties.get("LoadState") != "loaded" or "ControlGroup" not in properties:
            raise ProviderError("service-manager scope observation unavailable")
        if expected_description is not None and properties.get("Description") != expected_description:
            raise ProviderError("scope belongs to a different device")
        raw = properties["ControlGroup"].strip()
        if not raw:
            return None
        # Only this user's app slice, an exact unit basename, and no symlinks.
        expected = ("/user.slice/user-" + str(self.uid) + ".slice/user@" + str(self.uid) + ".service/")
        rel = PurePosixPath(raw)
        if (not raw.startswith(expected) or rel.name != unit or ".." in rel.parts or "\n" in raw):
            raise ProviderError("scope is outside this user's delegated subtree")
        path = self.root / raw.lstrip("/")
        if path.resolve() != path.absolute():
            raise ProviderError("scope path contains a symlink")
        return path

    def _path_from_ref(self, ref):
        match = REF.fullmatch(ref) if isinstance(ref, str) else None
        if not match:
            raise ProviderError("invalid native journal reference")
        claim_id, fence, boot, inode = match.groups()
        claim_identity(claim_id, int(fence))
        if boot != self._boot() or boot != self.baseline.boot_id:
            raise ProviderError("journal reference belongs to another boot")
        path = self._scope_path(claim_id)
        if path is None or not path.exists():
            return None, None
        if path.stat().st_ino != int(inode):
            raise ProviderError("scope identity changed")
        return path, "/" + str(path.relative_to(self.root))

    def preflight(self, claim):
        try:
            claim_identity(claim["claim_id"], claim["fence"])
            if claim.get("state") != "granted" or self._boot() != self.baseline.boot_id:
                raise ProviderError("claim or boot is not current")
            if not (self.root / "cgroup.controllers").is_file():
                raise ProviderError("cgroup v2 is unavailable")
            self.runner(["systemctl", "--user", "show", "--property=Version"])
            sample = self.probe.snapshot()
            returned = memory_returned(self.baseline, sample)
            if returned is None or sample.get("unknown_gpu_pids") is None:
                raise ProviderError("device observation is unknown")
            if sample["unknown_gpu_pids"]:
                return {"result": "refused", "cleanup": "pristine", "reason": "unknown_gpu_occupancy",
                        "health": "degraded"}
            if not returned:
                return {"result": "refused", "cleanup": "residue", "reason": "device_below_baseline",
                        "health": "unhealthy"}
            if sample["free_bytes"] < self.required + self.reserve:
                return {"result": "refused", "cleanup": "pristine", "reason": "insufficient_headroom"}
            return {"result": "admitted"}
        except (KeyError, ValueError, OSError, ProbeError, ProviderError):
            return {"result": "refused", "cleanup": "pristine", "reason": "preflight_unknown",
                    "health": "unknown"}

    def container(self, claim_id, fence, argv, env, release_fd):
        claim_identity(claim_id, fence)
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            raise ProviderError("SIGCHLD must retain child wait status")
        if self._boot() != self.baseline.boot_id:
            raise ProviderError("baseline belongs to another boot")
        if (not argv or not all(isinstance(x, str) and "\0" not in x for x in argv)
                or not isinstance(env, dict)):
            raise ProviderError("argv and environment must be explicit")
        integer(release_fd, "release_fd", 3)
        unit = self._unit(claim_id)
        # Refuse an existing unit; do not let systemd attach to or alter an incumbent.
        if self._scope_path(claim_id) is not None:
            raise ProviderError("claim scope already exists")
        child_env = {k: v for k, v in env.items() if not k.startswith("POSTMQ_")}
        child_env["CUDA_VISIBLE_DEVICES"] = self.baseline.gpu_uuid
        runtime = "/run/user/" + str(self.uid)
        child_env["XDG_RUNTIME_DIR"] = runtime
        # In --scope mode systemd-run execs in place, preserving release_fd.
        args = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--unit", unit,
                "--property=MemoryAccounting=yes"]
        if self.memory_max is not None:
            args.append("--property=MemoryMax=" + str(self.memory_max))
        args += [sys.executable, "-I", str(Path(__file__).with_name("trampoline.py")), str(release_fd)] + list(argv)
        child = self.popen(args, pass_fds=(release_fd,), env=child_env,
                           stdin=subprocess.DEVNULL, start_new_session=True)
        with self._process_lock:
            self.children[child.pid] = child
        deadline = self.clock() + self.drain_seconds
        while self.clock() < deadline:
            try:
                path = self._scope_path(claim_id)
                if path is not None and path.is_dir():
                    members = self._members(path)
                    if child.pid in members:
                        if not (path / "cgroup.kill").is_file() or not os.access(path / "cgroup.kill", os.W_OK):
                            raise ProviderError("owned scope lacks its required cgroup.kill control")
                        ref = "scope:" + claim_id + ":" + str(fence) + ":" + self._boot() + ":" + str(path.stat().st_ino)
                        self._counters(path / "memory.events")
                        integer(int((path / "memory.current").read_text()), "memory.current")
                        with self._process_lock:
                            self.owned.add(ref)
                            self.created[ref] = None
                            self.jobs[ref] = child
                            self._prune_created()
                        return {"ref": ref, "claim_id": claim_id, "fence": fence, "pid": child.pid}
                if child.poll() is not None:
                    break
            except FileNotFoundError:
                pass
            self.sleep(0.02)
        # The SDK owns release_fd's write end and closes it on this exception.
        # Never signal an unverified process or manufacture a launched reference.
        raise ProviderError("trampoline was not verified inside its claim scope")

    def journal_ref(self, handle):
        if not isinstance(handle, dict):
            raise ProviderError("handle must be an object")
        try:
            claim_identity(handle.get("claim_id"), handle.get("fence"))
        except (TypeError, ValueError):
            raise ProviderError("handle claim identity is invalid") from None
        ref = handle.get("ref")
        if not isinstance(ref, str) or not REF.fullmatch(ref) or len(ref) > 200:
            raise ProviderError("invalid handle reference")
        match = REF.fullmatch(ref)
        if (match[1], int(match[2])) != (handle.get("claim_id"), handle.get("fence")):
            raise ProviderError("handle fence or claim mismatch")
        return ref

    @staticmethod
    def _members(path):
        members = []
        def unreadable(error):
            raise error
        for current, dirs, _files in os.walk(path, followlinks=False, onerror=unreadable):
            if any((Path(current) / d).is_symlink() for d in dirs):
                raise ProviderError("cgroup subtree contains symlinks")
            for pid in (Path(current) / "cgroup.procs").read_text().split():
                members.append(integer(int(pid), "pid", 1))
        return sorted(set(members))

    def inspect(self, handle_or_ref):
        self._reap()
        job = {"job_exit": None, "job_signal": None}
        try:
            ref = handle_or_ref if isinstance(handle_or_ref, str) else self.journal_ref(handle_or_ref)
            with self._process_lock:
                code = self.completed.get(ref)
            # The original trampoline execs in place. Its exact Popen exit is
            # the job's exit; a future persistent guard must use its status pipe.
            if code is not None:
                job = {"job_exit": code if code >= 0 else None,
                       "job_signal": -code if code < 0 else None}
            path, relative = self._path_from_ref(ref)
            if path is None:
                # A collected scope cannot supply its final memory counter. It
                # is not a fabricated zero; a native live test must qualify this.
                with self._process_lock:
                    self.owned.discard(ref)
                    self._prune_created()
                return {**job, "state": "unverifiable", "observed_at": utc_now(), "reason": "scope_collected"}
            pids = self._members(path)
            events = self._counters(path / "cgroup.events")
            # A known live child dominates missing accounting evidence.
            if pids or events.get("populated") == 1:
                return {**job, "state": "alive", "pids": pids, "cgroup_path": relative,
                        "observed_at": utc_now()}
            memory_events = self._counters(path / "memory.events")
            current = integer(int((path / "memory.current").read_text()), "memory.current")
            peak_path = path / "memory.peak"
            peak = integer(int(peak_path.read_text()), "memory.peak") if peak_path.exists() else None
            populated = events.get("populated")
            if populated not in (0, 1) or "oom_kill" not in memory_events:
                raise ProviderError("required cgroup counters unavailable")
            return {**job, "state": "alive" if pids or populated else "gone", "pids": pids,
                    "memory_current": current, "memory_peak": peak,
                    "oom_kill": memory_events["oom_kill"], "cgroup_path": relative,
                    "observed_at": utc_now()}
        except (KeyError, TypeError, ValueError, OSError, ProbeError, ProviderError):
            return {**job, "state": "unverifiable", "observed_at": utc_now(), "reason": "native_evidence_unavailable"}

    @staticmethod
    def _counters(path):
        pairs = [line.split() for line in path.read_text().splitlines()]
        result = {}
        for fields in pairs:
            if len(fields) != 2 or fields[0] in result:
                raise ProviderError("invalid cgroup counters")
            result[fields[0]] = integer(int(fields[1]), fields[0])
        return result

    def stop(self, handle, grace_seconds):
        """Forced native stop; grace_seconds bounds the post-kill drain.

        This fixed SDK signature does not promise a pre-kill SIGTERM phase or
        an end-to-end wall-clock deadline. Scope validation and the final native
        observation each have a separate command timeout of up to two seconds.
        cgroup.kill is used only for an explicitly stopped/lost owned job.
        A normal job exits through its own parent path, without calling stop.
        """
        if (isinstance(grace_seconds, bool) or not isinstance(grace_seconds, (int, float))
                or not math.isfinite(grace_seconds) or grace_seconds < 0):
            raise ValueError("post-kill grace must be finite and nonnegative")
        ref = self.journal_ref(handle)
        # Authorization is independent of occupancy pruning. All native work
        # runs outside the metadata lock; the inode-bound fd still prevents a
        # replacement path from redirecting a kill to a peer.
        try:
            path, _ = self._path_from_ref(ref)
            if path is None:
                return self.inspect(ref)
            with self._process_lock:
                authorized = ref in self.created
            if not authorized:
                observed = self.inspect(ref)
                if observed.get("reason") == "scope_collected":
                    return observed
                raise ProviderError("stop refused: this provider instance did not create the scope")
            self._kill_bound(path, int(REF.fullmatch(ref)[4]))
        except OSError as error:
            import errno
            if error.errno not in (errno.ENOENT, errno.ENODEV):
                raise
            observed = self.inspect(ref)
            if observed["state"] == "alive":
                raise ProviderError("scope still populated after control disappeared") from None
            return observed
        deadline = self.clock() + grace_seconds
        while True:
            inspection = self.inspect(ref)
            if inspection["state"] != "alive" or self.clock() >= deadline:
                return inspection
            self.sleep(min(0.1, max(0, deadline - self.clock())))

    @staticmethod
    def _kill_bound(path, expected):
        # Open the bound directory, then kill relative to that fd: a path rename
        # between validation and write cannot redirect the action to a peer.
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(directory).st_ino != expected:
                raise ProviderError("scope changed before stop")
            fd = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                os.write(fd, b"1")
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def _reap(self):
        # SDK telemetry and per-claim inspection can run on separate threads.
        # Poll under one lock, and bind completion to ref rather than a reused pid.
        with self._process_lock:
            for pid, child in list(self.children.items()):
                if child.poll() is not None:
                    self.children.pop(pid, None)
            for ref, child in list(self.jobs.items()):
                code = child.poll()  # waitpid(WNOHANG), or Popen's cached status
                if code is not None:
                    self.completed[ref] = code
                    self.jobs.pop(ref, None)
            while len(self.completed) > 64:
                del self.completed[next(iter(self.completed))]
            self._prune_created()

    def _prune_created(self):
        """Called with the metadata lock held. Keep live refs plus 64 tombstones."""
        inactive = [ref for ref in self.created if ref not in self.owned and ref not in self.jobs]
        for ref in inactive[:-64]:
            del self.created[ref]

    def _receipt(self, handle_or_ref, *, inspector=None):
        inspector = self.inspect if inspector is None else inspector
        tail, stable = [], 0
        deadline = self.clock() + self.drain_seconds
        outcome, detail = "unknown", "no complete observation"
        while True:
            inspection = inspector(handle_or_ref)
            try:
                sample = (self.probe.snapshot(inspection.get("cgroup_path"))
                          if inspection.get("state") == "gone" else {})
                outcome, detail = cleanup_verdict(self.baseline, inspection, sample, self.charge_tolerance)
            except (OSError, ValueError, ProbeError):
                outcome, detail = "unknown", "device observation unavailable"
            tail.append({"observed_at": utc_now(), "summary": outcome + ": " + detail})
            stable = stable + 1 if outcome == "verified_clean" else 0
            if stable >= self.stable_samples:
                return outcome, detail, tail[-64:]
            if self.clock() >= deadline:
                if outcome == "verified_clean":
                    return "unknown", "insufficient consecutive clean observations", tail[-64:]
                return outcome, detail, tail[-64:]
            self.sleep(min(self.interval, max(0, deadline - self.clock())))

    def release_receipt(self, handle, execution):
        if execution not in {"completed", "failed", "aborted", "unknown"}:
            raise ValueError("invalid execution outcome")
        self.journal_ref(handle)
        outcome, detail, tail = self._receipt(handle)
        if outcome == "verified_clean":
            with self._process_lock:
                self.owned.discard(self.journal_ref(handle))
        return {"cleanup": outcome, "detail": detail, "telemetry_tail": tail}

    def reconcile_verdict(self, entries, claim_id, fence):
        claim_identity(claim_id, fence)
        # SDK entries may be dicts or parsed objects. Do not infer ownership of a
        # scope from its name alone. A native launched ref must match this epoch.
        for entry in entries:
            get = entry.get if isinstance(entry, dict) else lambda k, default=None: getattr(entry, k, default)
            if get("kind") == "launched" and get("claim", get("claim_id")) == claim_id and get("fence") == fence:
                ref = get("journal_ref", get("ref"))
                match = REF.fullmatch(ref) if isinstance(ref, str) else None
                if match and (match[1], int(match[2])) == (claim_id, fence):
                    return self._receipt(ref)
                return "unknown", "journal has no verifiable native reference", []
        # Process evidence dominates missing journal evidence, without granting
        # permission to kill an unowned scope. No 'never existed' proof is made
        # from an empty or vacuumed service-manager journal.
        try:
            path = self._scope_path(claim_id)
            if path and (self._members(path) or self._counters(path / "cgroup.events").get("populated")):
                return "cleanup_failed", "live claim scope exists without an owned journal reference", []
        except (OSError, ValueError, ProbeError, ProviderError):
            pass
        return "unknown", "no complete scope-history, device-baseline and GPU-inventory proof", []

    def telemetry(self):
        self._reap()
        try:
            active = []
            deadline = self.clock() + TELEMETRY_SCOPE_SECONDS
            with self._process_lock:
                references = list(self.owned)
            for ref in references:
                if self.clock() >= deadline:
                    return {"observed_at": utc_now(), "occupancy": "unknown"}, "unknown"
                inspection = self.inspect(ref)
                if inspection["state"] == "alive":
                    active.append(inspection.get("cgroup_path"))
                elif inspection["state"] == "unverifiable" and inspection.get("reason") != "scope_collected":
                    return {"observed_at": utc_now(), "occupancy": "unknown"}, "unknown"
            if self.clock() >= deadline:
                return {"observed_at": utc_now(), "occupancy": "unknown"}, "unknown"
            if len(active) > 1:
                return {"observed_at": utc_now(), "occupancy": "multiple_owned_scopes"}, "unknown"
            sample = self.probe.snapshot(active[0] if active else None)
            if active:
                # Legitimate owned allocations are not a failed return check.
                # Resource occupancy and health remain separate dimensions.
                return {**sample, "occupancy": "owned", "baseline": asdict(self.baseline)}, "unknown"
            returned = memory_returned(self.baseline, sample)
            health = ("unknown" if returned is None else "unhealthy" if not returned else
                      "degraded" if sample.get("unknown_gpu_pids") else "healthy")
            return {**sample, "baseline": asdict(self.baseline)}, health
        except (OSError, ValueError, ProbeError):
            return {"observed_at": utc_now(), "observation": "unavailable"}, "unknown"


def factory():
    """Called via `postmq-claim.py --provider sdk_provider.py:factory`.

    Configuration is an owner-authored file, never a receipt or a wake payload.
    This provider deliberately refuses unified-memory mode until its probe is
    qualified. No CUDA context is created merely by importing the factory.
    """
    if not __package__:
        raise ProviderError("use sdk_provider.py:factory")
    from .evidence import selected_config
    config = json.loads(Path(selected_config(os.environ)).read_text())
    if config.get("memory_mode") != "discrete":
        raise ProviderError("unified-memory probe is not qualified; no provider started")
    from .guarded_cgroup import GuardedCgroupProvider
    return GuardedCgroupProvider(Baseline(**config["baseline"]),
                               required_bytes=config["required_bytes"],
                               reserve_bytes=config["reserve_bytes"],
                               memory_max=config.get("memory_max"))
