"""Guarded cgroup provider. Native evidence is retained on non-clean verdicts.

No network, SDK journal/gate, PID signal or GPU context is implemented here.
All four controls remain advisory under a same-uid delegated user scope.
"""

from contextlib import ExitStack, contextmanager
import errno
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading

if __package__:
    from .linux_cgroup import LinuxCgroupProvider, ProviderError
    from .evidence import BOOT, claim_identity, integer, utc_now
    from .probe import ProbeError
else:
    from linux_cgroup import LinuxCgroupProvider, ProviderError
    from evidence import BOOT, claim_identity, integer, utc_now
    from probe import ProbeError


REF8 = re.compile(r"scope:([0-9A-HJKMNP-TV-Z]{26}):([1-9][0-9]{0,18}):"
                  r"([0-9a-f-]{36}):([1-9][0-9]{0,19}):([1-9][0-9]{0,19}):([1-9][0-9]{0,19})\Z")
MAX_GUARD_DRAIN = 15.0
DISMISS_BEFORE = 22.0


class StatusChannel:
    """One provider-owned nonblocking reader, serialized against close/reuse."""
    def __init__(self, fd):
        self.fd, self.buffer, self.result = fd, b"", None
        self.error = False
        self.lock = threading.Lock()
        os.set_blocking(fd, False)

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self):
        with self.lock:
            if self.result is not None:
                return dict(self.result)
            if self.error or self.fd is None:
                raise ProviderError("guard status channel failed")
            try:
                chunk = os.read(self.fd, 64)
            except BlockingIOError:
                return {"job_exit": None, "job_signal": None}
            except OSError:
                chunk = b""
            self.buffer += chunk
            if self.buffer == b"aborted\n":
                self.result = {"job_exit": None, "job_signal": None, "job_aborted": True}
                return dict(self.result)
            match = re.fullmatch(rb"(exit|signal) ([0-9]{1,3})\n", self.buffer)
            if match:
                kind, value = match[1], int(match[2])
                if (kind == b"exit" and value <= 255) or (kind == b"signal" and 0 < value < signal.NSIG):
                    self.result = {"job_exit": value if kind == b"exit" else None,
                                   "job_signal": value if kind == b"signal" else None}
                    return dict(self.result)
            if not chunk or b"\n" in self.buffer or len(self.buffer) >= 32:
                self.error = True
                raise ProviderError("guard status channel failed")
            return {"job_exit": None, "job_signal": None}


class GuardedCgroupProvider(LinuxCgroupProvider):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("drain_seconds", 10.0)
        super().__init__(*args, **kwargs)
        if self.drain_seconds > MAX_GUARD_DRAIN:
            raise ValueError("guard drain leaves insufficient dismissal budget")
        if self.memory_max is not None and self.memory_max % os.sysconf("SC_PAGE_SIZE"):
            raise ValueError("memory_max must be page aligned")
        self.channels = {}

    @property
    def description(self):
        return "PostMQ-GPU:" + self.baseline.gpu_uuid

    def declare(self):
        result = super().declare()
        result["capabilities"].update(native_ref_version=8, container_layout="guard-job",
                                      launch_barrier="prepared-child")
        return result

    @staticmethod
    def epoch_unit(claim, fence):
        claim_identity(claim, fence)
        if fence > 2**63 - 1:
            raise ProviderError("fence exceeds native unit range")
        return "postmq-claim-" + claim + "-f" + str(fence) + ".scope"

    @staticmethod
    def parts(ref):
        match = REF8.fullmatch(ref) if isinstance(ref, str) and len(ref) <= 200 else None
        if not match or not BOOT.fullmatch(match[3]):
            raise ProviderError("invalid guarded native reference")
        claim, fence, boot, scope, guard, job = match.groups()
        GuardedCgroupProvider.epoch_unit(claim, int(fence))
        return claim, int(fence), boot, int(scope), int(guard), int(job)

    def journal_ref(self, handle):
        if not isinstance(handle, dict):
            raise ProviderError("handle must be an object")
        parts = self.parts(handle.get("ref"))
        if parts[:2] != (handle.get("claim_id"), handle.get("fence")) or type(handle.get("fence")) is not int:
            raise ProviderError("handle claim or fence mismatch")
        return handle["ref"]

    def _paths(self, ref):
        claim, fence, boot, *inodes = self.parts(ref)
        if boot != self.baseline.boot_id or boot != self._boot():
            raise ProviderError("reference belongs to another boot")
        scope = self._scope_path_for_unit(self.epoch_unit(claim, fence), self.description)
        if scope is None or not scope.exists():
            return None
        paths = scope, scope / "guard", scope / "job"
        for path, inode in zip(paths, inodes):
            if path.is_symlink() or not path.is_dir() or path.stat().st_ino != inode:
                raise ProviderError("native container identity changed")
        return paths

    @contextmanager
    def _bound_dirs(self, ref):
        paths = self._paths(ref)
        if paths is None:
            raise FileNotFoundError(errno.ENOENT, "scope collected")
        expected = self.parts(ref)[3:]
        with ExitStack() as stack:
            fds = []
            for index, path in enumerate(paths):
                fd = (os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if index == 0 else
                      os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[0]))
                stack.callback(os.close, fd)
                if os.fstat(fd).st_ino != expected[index]:
                    raise ProviderError("native identity changed before control")
                fds.append(fd)
            yield fds

    @staticmethod
    def _kill_fd(directory):
        fd = os.open("cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            if os.write(fd, b"1") != 1:
                raise ProviderError("native kill control accepted no byte")
        finally:
            os.close(fd)

    def container(self, claim_id, fence, argv, env, release_fd):
        started = self.clock()
        unit = self.epoch_unit(claim_id, fence)
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN or self._boot() != self.baseline.boot_id:
            raise ProviderError("child wait status or boot identity unavailable")
        if (not argv or not argv[0] or not all(isinstance(x, str) and "\0" not in x for x in argv)
                or not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                       and "\0" not in k + v for k, v in env.items())):
            raise ProviderError("explicit argv and environment required")
        integer(release_fd, "release_fd", 3)
        if self._scope_path_for_unit(unit, self.description) is not None:
            raise ProviderError("claim epoch scope already exists")
        reader, writer = os.pipe()
        channel = StatusChannel(reader)
        try:
            publish_r, publish_w = os.pipe()
        except BaseException:
            channel.close(); os.close(writer)
            raise
        child_env = {k: v for k, v in env.items() if not k.startswith("POSTMQ_")}
        child_env.update(CUDA_VISIBLE_DEVICES=self.baseline.gpu_uuid, XDG_RUNTIME_DIR="/run/user/" + str(self.uid))
        args = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--unit", unit,
                "--property=Delegate=yes", "--property=MemoryAccounting=yes", "--property=Description=" + self.description,
                sys.executable, "-I", "-B",
                str(Path(__file__).with_name("guard.py")), str(release_fd), str(writer),
                "--claim", claim_id, "--fence", str(fence), "--publish-fd", str(publish_r)]
        if self.memory_max is not None:
            args += ["--memory-max", str(self.memory_max)]
        args += ["--"] + list(argv)
        try:
            try:
                child = self.popen(args, pass_fds=(release_fd, writer, publish_r), env=child_env,
                                   stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            finally:
                os.close(writer)
                os.close(publish_r)
            with self._process_lock:
                self.children[child.pid] = child
            deadline = started + self.drain_seconds
            while self.clock() < deadline:
                try:
                    if child.poll() is not None:
                        break
                    scope = self._scope_path_for_unit(unit, self.description)
                    if scope is not None:
                        guard, job = scope / "guard", scope / "job"
                        if (guard.is_dir() and job.is_dir() and not guard.is_symlink() and not job.is_symlink()
                                and self._members(guard) == [child.pid] and len(self._members(job)) == 1
                                and not (scope / "cgroup.procs").read_text().strip()):
                            if self.memory_max is not None and (job / "memory.max").read_text().strip() != str(self.memory_max):
                                raise ProviderError("job memory limit not ready")
                            for leaf in (guard, job):
                                if not os.access(leaf / "cgroup.kill", os.W_OK):
                                    raise ProviderError("leaf kill control unavailable")
                            integer(int((job / "memory.current").read_text()), "memory.current")
                            if "oom_kill" not in self._counters(job / "memory.events"):
                                raise ProviderError("job OOM accounting unavailable")
                            ref = ":".join(["scope", claim_id, str(fence), self._boot()] +
                                           [str(p.stat().st_ino) for p in (scope, guard, job)])
                            self.parts(ref)
                            if self._inspect_evidence(ref).get("state") != "alive":
                                raise ProviderError("blocked job accounting not ready")
                            if self.clock() >= deadline or child.poll() is not None:
                                raise ProviderError("guard verification completed too late")
                            with self._process_lock:
                                self.owned.add(ref); self.created[ref] = None; self.channels[ref] = channel
                                self._prune_created()
                            if os.write(publish_w, b"p") != 1:
                                raise ProviderError("publication signal failed")
                            return {"ref": ref, "claim_id": claim_id, "fence": fence}
                    if child.poll() is not None:
                        break
                except FileNotFoundError:
                    pass
                self.sleep(0.02)
        except BaseException:
            with self._process_lock:
                if "ref" in locals():
                    self.owned.discard(ref); self.created.pop(ref, None); self.channels.pop(ref, None)
            channel.close()
            raise
        finally:
            os.close(publish_w)
        channel.close()
        raise ProviderError("guard and job leaves were not verified")

    def _inspect_evidence(self, handle_or_ref):
        ref = handle_or_ref if isinstance(handle_or_ref, str) else self.journal_ref(handle_or_ref)
        result = {"job_exit": None, "job_signal": None, "observed_at": utc_now()}
        try:
            paths = self._paths(ref)
            if paths is None:
                return {**result, "state": "unverifiable", "reason": "scope_collected"}
            scope, guard, job = paths
            pids = self._members(job)
            events = self._counters(job / "cgroup.events")
            relative = "/" + str(job.relative_to(self.root))
            if pids or events.get("populated") == 1:
                return {**result, "state": "alive", "pids": pids, "cgroup_path": relative}
            # Only the guard may remain outside job/. A missing guard cannot
            # stand in for a retained accounting witness.
            guard_members = self._members(guard)
            if len(guard_members) != 1 or (scope / "cgroup.procs").read_text().strip():
                raise ProviderError("accounting guard membership unavailable")
            if any(p.is_dir() and p.name not in {"guard", "job"} for p in scope.iterdir()):
                raise ProviderError("unexpected scope descendant")
            current = integer(int((job / "memory.current").read_text()), "memory.current")
            memory = self._counters(job / "memory.events")
            peak = job / "memory.peak"
            if events.get("populated") != 0 or "oom_kill" not in memory:
                raise ProviderError("required job counters unavailable")
            return {**result, "state": "gone", "pids": [], "memory_current": current,
                    "memory_peak": integer(int(peak.read_text()), "memory.peak") if peak.exists() else None,
                    "oom_kill": memory["oom_kill"], "cgroup_path": relative}
        except (OSError, ValueError, ProbeError, ProviderError):
            return {**result, "state": "unverifiable", "reason": "native_evidence_unavailable"}

    def inspect(self, handle_or_ref):
        self._reap()
        ref = handle_or_ref if isinstance(handle_or_ref, str) else self.journal_ref(handle_or_ref)
        result = self._inspect_evidence(ref)
        with self._process_lock:
            channel = self.channels.get(ref)
        if channel is not None:
            # Guard death is never substituted for primary-job termination.
            # An invalid/closed live channel raises into the SDK's fixed abort
            # fallback; native release/reconcile evidence remains independent.
            result.update(channel.read())
        return result

    def stop(self, handle, grace_seconds):
        import math
        if (isinstance(grace_seconds, bool) or not isinstance(grace_seconds, (int, float))
                or not math.isfinite(grace_seconds) or grace_seconds < 0):
            raise ValueError("post-kill grace must be finite and nonnegative")
        ref = self.journal_ref(handle)
        with self._process_lock:
            authorized = ref in self.created
        if not authorized:
            raise ProviderError("stop refused: epoch not created by this provider")
        try:
            with self._bound_dirs(ref) as (_scope, _guard, job):
                self._kill_fd(job)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENODEV):
                raise
            observed = self._inspect_evidence(ref)
            if observed["state"] == "alive":
                raise ProviderError("job still alive after control disappeared") from None
            return observed
        deadline = self.clock() + grace_seconds
        while True:
            observed = self._inspect_evidence(ref)
            if observed["state"] != "alive" or self.clock() >= deadline:
                return observed
            self.sleep(min(0.1, max(0, deadline - self.clock())))

    @staticmethod
    def _remove_job(scope):
        os.rmdir("job", dir_fd=scope)

    def _dismiss(self, ref):
        # Called only after the complete verified-clean proof. Never kill the
        # scope root: removing the empty job then killing guard/ is the order.
        with self._bound_dirs(ref) as (scope, guard, _job):
            observed = self._inspect_evidence(ref)
            if observed["state"] != "gone" or observed.get("memory_current", self.charge_tolerance + 1) > self.charge_tolerance:
                raise ProviderError("job evidence changed before dismissal")
            self._remove_job(scope)
            for attempt in range(3):
                try:
                    self._kill_fd(guard)
                    break
                except OSError as error:
                    if error.errno in (errno.ENOENT, errno.ENODEV):
                        break  # The bound guard was already collected.
                    if attempt == 2:
                        raise
                    self.sleep(0.05)

    def _finish_receipt(self, ref):
        started = self.clock()
        outcome, detail, tail = super()._receipt(ref, inspector=self._inspect_evidence)
        if outcome == "verified_clean":
            if self.clock() - started >= DISMISS_BEFORE:
                return "unknown", "native proof retained: insufficient dismissal budget", tail
            try:
                self._dismiss(ref)
            except (OSError, ProviderError):
                # No clean receipt is invented after a changed observation or
                # partial native teardown. Deleted counters cannot be recreated.
                return "unknown", "clean proof observed but dismissal failed or evidence changed", tail
            with self._process_lock:
                self.owned.discard(ref)
                channel = self.channels.pop(ref, None)
            if channel is not None:
                channel.close()
        return outcome, detail, tail

    def release_receipt(self, handle, execution):
        if execution not in {"completed", "failed", "aborted", "unknown"}:
            raise ValueError("invalid execution outcome")
        ref = self.journal_ref(handle)
        with self._process_lock:
            authorized = ref in self.created
        if not authorized:
            raise ProviderError("release refused: epoch not created by this provider")
        outcome, detail, tail = self._finish_receipt(ref)
        return {"cleanup": outcome, "detail": detail, "telemetry_tail": tail}

    def reconcile_verdict(self, entries, claim_id, fence):
        self.epoch_unit(claim_id, fence)
        for entry in entries:
            get = entry.get if isinstance(entry, dict) else lambda k, default=None: getattr(entry, k, default)
            if get("kind") == "launched" and get("claim", get("claim_id")) == claim_id and get("fence") == fence:
                ref = get("journal_ref", get("ref"))
                try:
                    if self.parts(ref)[:2] == (claim_id, fence):
                        return self._finish_receipt(ref)
                except ProviderError:
                    pass
                return "unknown", "journal reference does not identify this epoch", []
        return "unknown", "no native launched reference; no never-existed proof", []

    def telemetry(self):
        self._reap()
        try:
            listing = self.runner(["systemctl", "--user", "list-units", "--all", "--type=scope",
                                   "--plain", "--full", "--no-legend", "--no-pager", "postmq-claim-*.scope"])
            units = []
            for line in listing.splitlines():
                fields = line.split()
                if not fields or not fields[0].startswith("postmq-claim-") or not fields[0].endswith(".scope"):
                    continue
                tags = [field for field in fields[4:] if field.startswith("PostMQ-GPU:")]
                if not tags or self.description in tags:
                    units.append(fields[0])  # Untagged legacy units stay unknown.
        except (OSError, ProbeError):
            return {"observed_at": utc_now(), "occupancy": "unknown"}, "unknown"
        with self._process_lock:
            references = list(self.owned)
        known = {self.epoch_unit(*self.parts(ref)[:2]) for ref in references}
        if set(units) - known:
            return {"observed_at": utc_now(), "occupancy": "untracked_epoch", "native_units": units[:64]}, "unknown"
        if len(references) > 1:
            return {"observed_at": utc_now(), "occupancy": "multiple_retained_epochs"}, "unknown"
        if references:
            return {"observed_at": utc_now(), "occupancy": "retained_epoch",
                    "evidence": self._inspect_evidence(references[0])}, "unknown"
        return super().telemetry()
