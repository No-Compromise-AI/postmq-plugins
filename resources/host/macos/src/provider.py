"""Nine-method provider for an explicitly logical, cooperative Mac job gate."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import platform
import pwd
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time

from .native import (Darwin, ProviderError, canonical, digest, integer, private_dir,
                     read_json, require, source_digest, utc_now)

CLAIM = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}\Z")
REF = re.compile(r"mac1:([0-9A-HJKMNP-TV-Z]{26}):([1-9][0-9]{0,18}):([0-9a-f-]{36}):([1-9][0-9]{0,19}):([0-9a-f]{64})\Z")
SAFE_ENV = {"HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "SHELL",
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"}


def epoch_name(claim, fence):
    require(isinstance(claim, str) and CLAIM.fullmatch(claim), "invalid claim")
    integer(fence, "fence", 1)
    return hashlib.sha256((claim + ":" + str(fence)).encode()).hexdigest()[:32]


def clean_status(status):
    return bool(status.get("phase") == "terminal" and status.get("kernel_exit_observed") is True
                and status.get("reaped_by_guard") is True and status.get("fork_seen") is False
                and status.get("reason") in (None, "execution deadline")
                and (type(status.get("job_exit")) is int or type(status.get("job_signal")) is int))


def job_environment(env):
    require(isinstance(env, dict), "invalid environment")
    value = {k:v for k,v in env.items() if k in SAFE_ENV and isinstance(v,str) and "\0" not in v}
    value.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return value


def read_config(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(), "canonical configuration path required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077
                and st.st_size <= 65536, "configuration must be a private owner file")
        value = json.loads(os.read(fd, 65537))
        require(isinstance(value, dict), "configuration must be an object")
        return value
    finally: os.close(fd)


class MacProvider:
    def __init__(self, config, *, native=None, state_root=None, config_path=None):
        self.native = native or Darwin(); self.host = self.native.host()
        require(config.get("expected_host") == self.host, "configuration belongs to another host/boot")
        self.config = dict(config); self.config_path = config_path
        self.seconds = integer(config.get("max_run_seconds"), "max_run_seconds", 1, 3600)
        self.free_percent = integer(config.get("minimum_free_percent"), "minimum_free_percent", 1, 100)
        require(config.get("owner_mode") in ("background", "interactive", "unavailable"), "invalid owner mode")
        self.executables = config.get("allowed_executables")
        require(isinstance(self.executables, dict) and self.executables, "explicit executable policy required")
        for name, value in self.executables.items():
            require(Path(name).is_absolute() and re.fullmatch(r"[0-9a-f]{64}", value), "invalid executable pin")
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        self.fixture = state_root is not None
        self.root = (Path(state_root) if self.fixture else home/".postmq-macos-v1").absolute()
        require(self.root.resolve() == self.root, "native state root contains a symlink")
        # Refuse before creating directories/epochs or starting a native guard.
        require(len(os.fsencode(self.root / "e" / ("0" * 32) / "s")) < 100,
                "native root exceeds UNIX socket path limit")
        fd = private_dir(self.root, create=True); self.root_inode = os.fstat(fd).st_ino; os.close(fd)
        self.epochs = self.root / "e"; fd = private_dir(self.epochs, create=True); os.close(fd)
        self.guard = Path(__file__).with_name("guard.py")
        self.guard_sha = hashlib.sha256(self.guard.read_bytes()).hexdigest()
        self.source_sha = source_digest()
        self.created, self.guards = {}, {}

    def declare(self):
        return {"receipt_provider":"generic", "identity_kind":"logical",
                "identity_key":("macos-test-job:" + self.host["host_uuid"] + ":" + hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
                                if self.fixture else "macos-host-job:" + self.host["host_uuid"]),
                "provider_host_key":self.host["host_uuid"],
                "enforcement":{k:"advisory" for k in ("ownership", "descendant_supervision", "memory_accounting", "revocation")},
                "capabilities":{"os":"macos", "architecture":platform.machine().lower(), "backend":"host-process",
                    "cleanup_scope":"fork-free managed session; no physical GPU-empty claim",
                    "native_watch":"kqueue-fork-exit-before-release", "memory_measurement":"OS pressure advisory",
                    "launch_barrier":"sdk-release-fd", "profile_version":1, "test_fixture":self.fixture,
                    "source_sha256":self.source_sha, "owner_uid":os.getuid()}}

    def _owner(self):
        if self.config_path is None: return self.config["owner_mode"]
        value = read_config(self.config_path)
        require(value.get("expected_host") == self.host, "owner policy generation differs")
        require({k:v for k,v in value.items() if k != "owner_mode"}
                == {k:v for k,v in self.config.items() if k != "owner_mode"}, "runtime policy changed; restart required")
        require(value.get("owner_mode") in ("background", "interactive", "unavailable"), "owner policy unavailable")
        return value["owner_mode"]

    def _generation(self):
        require(self.native.host() == self.host, "host generation changed")
        require(source_digest() == self.source_sha, "provider sources changed; restart/review required")
        fd = private_dir(self.root)
        try: require(os.fstat(fd).st_ino == self.root_inode, "native root replaced")
        finally: os.close(fd)

    def _inspect_epoch(self, path, ref=None):
        fd = private_dir(path)
        try:
            identity = read_json(fd, "identity.json"); status = read_json(fd, "status.json")
            seal = digest(identity)
            require(identity["host"] == self.host and identity["epoch_inode"] == os.fstat(fd).st_ino
                    and identity["guard_sha256"] == self.guard_sha and identity["source_sha256"] == self.source_sha
                    and status["identity_sha256"] == seal,
                    "native epoch identity differs")
            expected = f"mac1:{identity['claim_id']}:{identity['fence']}:{self.host['boot_uuid']}:{os.fstat(fd).st_ino}:{seal}"
            require(path.name == epoch_name(identity["claim_id"], identity["fence"])
                    and (ref is None or ref == expected), "native reference differs")
            return identity, status, expected
        finally: os.close(fd)

    def preflight(self, claim):
        try:
            epoch_name(claim["claim_id"], claim["fence"])
            require(claim.get("state") == "granted", "claim is not granted")
            telemetry, health = self.telemetry()
            require(health == "healthy", "owner/headroom/retained-epoch gate blocks admission")
            return {"result":"admitted"}
        except Exception:
            return {"result":"refused", "reason":"Mac owner, generation, headroom or retained native evidence blocks admission",
                    "cleanup":"pristine", "health":"degraded"}

    def container(self, claim_id, fence, argv, env, release_fd):
        # Native admission also serializes direct/provider callers. The SDK's
        # canonical host gate remains the coordinator gate.
        fd = os.open(self.root / "admission", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077,
                    "invalid native admission file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._container(claim_id, fence, argv, env, release_fd)
        finally: os.close(fd)

    def _container(self, claim_id, fence, argv, env, release_fd):
        # Pin the read-pipe object before any native probe/IO; never close the
        # SDK's original descriptor, including after a method budget expires.
        before = os.fstat(release_fd); held = os.dup(release_fd)
        try:
            require(stat.S_ISFIFO(before.st_mode) and (os.fstat(held).st_dev, os.fstat(held).st_ino) == (before.st_dev, before.st_ino)
                    and fcntl.fcntl(held, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY, "release pipe differs")
            self._generation()
            require(self.telemetry()[1] == "healthy", "owner/headroom/native epoch changed before publication")
            require(isinstance(argv, (list, tuple)) and argv and all(isinstance(x, str) and "\0" not in x for x in argv), "invalid argv")
            name = argv[0]; require(name in self.executables and hashlib.sha256(Path(name).read_bytes()).hexdigest()
                    == self.executables[name], "executable is not the approved runtime")
            require(not Path(name).stat().st_mode & (stat.S_ISUID | stat.S_ISGID), "privileged executable not supported")
            job_env = job_environment(env)
            path = self.epochs / epoch_name(claim_id, fence)
            try: path.mkdir(mode=0o700)
            except FileExistsError as error: raise ProviderError("native epoch already exists") from error
            token = secrets.token_hex(32)
            frame = {"claim_id":claim_id, "fence":fence, "host":self.host, "token":token,
                     "argv":list(argv), "env":job_env, "guard_sha256":self.guard_sha, "source_sha256":self.source_sha,
                     "max_run_seconds":self.seconds, "output_limit":16*1024**2}
            raw = canonical(frame); require(len(raw) <= 65536, "launch frame too large")
            with (path / "guard-out").open("xb") as out, (path / "guard-err").open("xb") as err:
                guard = subprocess.Popen([sys.executable, "-I", "-B", str(self.guard), "--epoch", str(path), "--release-fd", str(held)],
                    stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True,
                    pass_fds=(held,), env={k:v for k,v in os.environ.items() if k in SAFE_ENV})
            guard.stdin.write(raw); guard.stdin.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try: identity, status, ref = self._inspect_epoch(path)
                except (OSError, ValueError, ProviderError, KeyError):
                    if guard.poll() is not None: raise ProviderError("native guard startup failed")
                    time.sleep(0.02); continue
                observed = self.native.process(guard.pid)
                require(observed and observed["birth"] == identity["guard"]["birth"]
                        and identity["guard"]["pid"] == guard.pid, "guard identity differs")
                require(len(ref) <= 200, "native reference too long")
                self.created[ref] = token; self.guards[ref] = guard
                return {"ref":ref, "claim_id":claim_id, "fence":fence}
            raise ProviderError("native guard publication timed out; retain evidence")
        finally: os.close(held)

    @contextmanager
    def _bound(self, handle_or_ref):
        ref = handle_or_ref if isinstance(handle_or_ref, str) else handle_or_ref["ref"]
        match = REF.fullmatch(ref); require(match is not None, "invalid native reference")
        claim, fence, boot, inode, seal = match.groups(); fence = int(fence)
        require(boot == self.host["boot_uuid"], "reference is from another boot")
        if isinstance(handle_or_ref, dict):
            require(handle_or_ref.get("claim_id") == claim and type(handle_or_ref.get("fence")) is int
                    and handle_or_ref.get("fence") == fence, "handle/fence differs")
        self._generation(); path = self.epochs / epoch_name(claim, fence)
        identity, status, actual = self._inspect_epoch(path, ref)
        require(identity["epoch_inode"] == int(inode), "epoch inode changed")
        yield path, identity, status, ref

    def journal_ref(self, handle):
        with self._bound(handle) as (_, _, _, ref): return ref

    def _observation(self, handle_or_ref):
        try:
            with self._bound(handle_or_ref) as (_, identity, status, ref):
                guard = self.guards.get(ref)
                if guard is not None: guard.poll()  # reaping the guard never releases the job's PID fence
                process = self.native.process(identity["job"]["pid"])
                same = process is not None and process["birth"] == identity["job"]["birth"]
                terminal = clean_status(status) and not same
                failed=False
                if terminal: state = "gone"
                elif status["phase"] == "terminal" or status["phase"] == "unverifiable":
                    state = "unverifiable";failed=True
                else:
                    current_guard = self.native.process(identity["guard"]["pid"])
                    good = current_guard and current_guard["birth"] == identity["guard"]["birth"]
                    fresh=0 <= self.native.now()-status["observed_continuous"] < 3
                    failed=not (good and fresh)
                    state = "alive" if same and good and fresh else "unverifiable"
                return {"state":state, "job_exit":status.get("job_exit"), "job_signal":status.get("job_signal"),
                        "observation_failed":failed,"fork_seen":status.get("fork_seen"),
                        "started":status.get("started"), "observed_at":utc_now()}
        except Exception:
            return {"state":"unverifiable", "job_exit":None, "job_signal":None,"observation_failed":True}

    def inspect(self, handle_or_ref):
        value=self._observation(handle_or_ref)
        if isinstance(handle_or_ref,dict) and value["state"]=="unverifiable" and value.get("observation_failed") is True \
                and type(value.get("job_exit")) is not int and type(value.get("job_signal")) is not int:
            # The SDK keeps renewing when an ordinary unverifiable observation
            # has no exit code. Spend this handle's inspect method instead, so
            # it stops exact creator control and releases UNKNOWN under budget.
            raise ProviderError("managed job observation unavailable; retain epoch for recovery")
        return value

    def _signal(self, handle, signum):
        with self._bound(handle) as (path, identity, status, ref):
            require(ref in self.created, "this provider did not create the epoch")
            if status.get("reaped_by_guard"): return
            request = {"identity_sha256":digest(identity), "token":self.created[ref],
                       "signal":int(signum), "deadline":self.native.now()+2}
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(2); s.connect(str(path/"s")); s.sendall(canonical(request))
                require(json.loads(s.recv(8192)).get("accepted") is True, "native control refused")

    def stop(self, handle, grace_seconds):
        require(type(grace_seconds) in (int,float) and 0 <= grace_seconds <= 30, "invalid drain window")
        self._signal(handle, signal.SIGKILL)
        end = time.monotonic()+grace_seconds
        while time.monotonic() < end:
            if self.inspect(handle)["state"] != "alive": break
            time.sleep(0.05)

    def forward_signal(self, handle, signum):
        require(signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP), "signal not allowed")
        self._signal(handle, signum)

    def release_receipt(self, handle, execution):
        state = self._observation(handle)
        outcome = "verified_clean" if state["state"] == "gone" else "cleanup_failed" if state["state"] == "alive" else "unknown"
        return {"cleanup":outcome, "detail":"fork-free logical session proof only; shared-memory capacity is separate"}

    def reconcile_verdict(self, entries, claim_id, fence):
        refs = {e.ref for e in entries if e.kind == "launched" and e.claim == claim_id and e.fence == fence}
        if len(refs) != 1: return "unknown", "missing or ambiguous native launch evidence", None
        ref = next(iter(refs)); match = REF.fullmatch(ref)
        if not match or match[1] != claim_id or int(match[2]) != fence: return "unknown", "native epoch differs", None
        state = self.inspect(ref)
        result = "verified_clean" if state["state"] == "gone" else "cleanup_failed" if state["state"] == "alive" else "unknown"
        return result, "fresh inspection of the retained logical session", None

    def telemetry(self):
        try:
            self._generation(); mode = self._owner(); pressure = self.native.pressure(); blocked = 0
            for path in self.epochs.iterdir():
                try:
                    _, _, ref = self._inspect_epoch(path)
                    if self.inspect(ref)["state"] != "gone": blocked += 1
                except Exception: blocked += 1
            healthy = mode == "background" and pressure["free_percent"] >= self.free_percent and blocked == 0
            return {"observed_at":utc_now(), "owner_mode":mode, "memory":pressure, "unsettled_epochs":blocked,
                    "memory_accounting":"advisory", "external_load_inventory":"not complete", "cleanup_scope":"logical job session"}, "healthy" if healthy else "degraded"
        except Exception: return {"observed_at":utc_now(), "reason":"native telemetry unavailable"}, "unknown"


def factory():
    path = Path(os.environ["POSTMQ_MACOS_CONFIG"])
    require(path.is_absolute(), "absolute configuration path required")
    config = read_config(path)
    require("state_root" not in config, "production state root cannot be overridden")
    require(Path.home().resolve() == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(), "OS owner's home required")
    require(not os.environ.get("POSTMQ_HOST_STATE") and not os.environ.get("XDG_STATE_HOME"), "canonical SDK state required")
    return MacProvider(config, config_path=path)
