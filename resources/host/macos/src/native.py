"""Darwin identity/clock primitives and private native-epoch IO."""
import ctypes as C
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import stat
import subprocess
import sys
import time


class ProviderError(RuntimeError):
    pass


def require(value, message):
    if not value: raise ProviderError(message)


def integer(value, name, minimum=0, maximum=2**63-1):
    require(type(value) is int and minimum <= value <= maximum, "invalid " + name)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def source_digest():
    root = Path(__file__).resolve().parent
    return digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                   for name in ("__init__.py", "entry.py", "native.py", "provider.py", "guard.py")})


def utc_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=3).strip()


class BsdInfo(C.Structure):
    _fields_ = [(x, C.c_uint32) for x in ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid",
        "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", C.c_char*16), ("name", C.c_char*32)]
    _fields_ += [(x, C.c_uint32) for x in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", C.c_int32), ("start_sec", C.c_uint64), ("start_usec", C.c_uint64)]


class Timebase(C.Structure):
    _fields_ = [("numer", C.c_uint32), ("denom", C.c_uint32)]


class Darwin:
    def __init__(self):
        require(sys.platform == "darwin", "macOS required")
        self._host_identity = None
        self.lib = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.lib.proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
        self.lib.proc_pidinfo.restype = C.c_int
        self.lib.proc_listpids.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.c_int]
        self.lib.proc_listpids.restype = C.c_int
        self.clocklib = C.CDLL("/usr/lib/libSystem.B.dylib")
        self.clocklib.mach_continuous_time.restype = C.c_uint64
        self.clocklib.mach_continuous_time.argtypes = []
        self.clocklib.mach_timebase_info.argtypes = [C.POINTER(Timebase)]
        tb = Timebase(); require(self.clocklib.mach_timebase_info(C.byref(tb)) == 0 and tb.denom > 0,
                                  "continuous clock unavailable")
        self.scale = tb.numer / tb.denom / 1_000_000_000
        me = self.process(os.getpid())
        require(me and me["pid"] == os.getpid() and me["uid"] == os.getuid()
                and me["pgid"] == os.getpgrp(), "native process ABI check failed")

    def now(self):
        return self.clocklib.mach_continuous_time() * self.scale

    def process(self, pid, *, include_zombie=False):
        integer(pid, "pid", 1, 2**31-1); data = BsdInfo(); C.set_errno(0)
        require(type(include_zombie) is bool, "invalid zombie observation policy")
        n = self.lib.proc_pidinfo(pid, 3, int(include_zombie), C.byref(data), C.sizeof(data))
        if n == 0 and C.get_errno() in (errno.ESRCH, errno.ENOENT): return None
        require(n == C.sizeof(data) and data.pid == pid, "process identity unavailable")
        return {"pid": data.pid, "ppid": data.ppid, "pgid": data.pgid, "uid": data.uid,
                "birth": str(data.start_sec) + "." + str(data.start_usec), "status": data.status}

    def group(self, pgid):
        integer(pgid, "pgid", 1, 2**31-1); C.set_errno(0)
        size = self.lib.proc_listpids(2, pgid, None, 0)
        require(size >= 0 and C.get_errno() == 0, "group census unavailable")
        if size == 0: return []
        values = (C.c_uint32 * (size // 4 + 64))(); C.set_errno(0)
        n = self.lib.proc_listpids(2, pgid, values, C.sizeof(values))
        require(0 <= n < C.sizeof(values) and n % 4 == 0 and C.get_errno() == 0,
                "group census incomplete")
        return sorted(x for x in values[:n//4] if x)

    def host(self):
        # A reboot ends this process. Keep this immutable per-process binding
        # out of the frequent SDK inspection loop; source/epoch checks stay fresh.
        if self._host_identity is not None:
            return dict(self._host_identity)
        raw = subprocess.check_output(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a"], timeout=3)
        value = plistlib.loads(raw)[0]["IOPlatformUUID"].lower()
        boot = command(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"]).lower()
        pattern = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
        require(re.fullmatch(pattern, value) and re.fullmatch(pattern, boot), "host/boot identity unavailable")
        self._host_identity = {"host_uuid": value, "boot_uuid": boot}
        return dict(self._host_identity)

    def pressure(self):
        text = command(["/usr/bin/memory_pressure", "-Q"])
        match = re.search(r"System-wide memory free percentage:\s*(\d+)%", text)
        require(match is not None and 0 <= int(match[1]) <= 100, "memory pressure unavailable")
        return {"free_percent": int(match[1]), "total_bytes": int(command(["/usr/sbin/sysctl", "-n", "hw.memsize"])),
                "measurement": "memory_pressure-Q; advisory availability, not allocatable bytes"}


def private_dir(path, create=False):
    path = Path(path)
    if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.absolute() == path.resolve(), "native root contains a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd); raise ProviderError("native root must be owner-only")
    return fd


def read_json(fd, name):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        st = os.fstat(child)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077
                and st.st_size <= 65536, "invalid native record")
        raw = os.read(child, 65537); require(len(raw) <= 65536, "native record too large")
        value = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ProviderError("nonfinite record")))
        require(isinstance(value, dict), "native record must be an object")
        return value
    finally: os.close(child)


def write_json(fd, name, value, replace=False):
    temporary = ".write-" + secrets.token_hex(8)
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(child, "wb") as f:
            f.write(canonical(value) + b"\n"); f.flush(); os.fsync(f.fileno())
        if replace: os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else: os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    finally:
        try: os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError: pass
