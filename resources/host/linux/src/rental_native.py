"""Linux rental process evidence and pidfd controls; no broker, gate or journal.

The root must be this supervisor, established before its one prepared child.
Kernel children lists include orphan adoption under PR_SET_CHILD_SUBREAPER.
Same-UID tampering and inaccessible namespaces remain advisory/unknown.
"""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import signal
import socket
import struct
import json


MAX_MEMBERS = 512
MAX_THREADS = 8192
MAX_REPLY = 131072
STOP_DRAIN_FLOOR = 2.0  # Immediate SIGKILL may need several adoption/reap passes.


class NativeUnknown(RuntimeError):
    pass


def process(pid, proc=Path("/proc")):
    if type(pid) is not int or pid <= 0:
        raise NativeUnknown("invalid native pid")
    text = (Path(proc) / str(pid) / "stat").read_text()
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) < 20 or not text.startswith(str(pid) + " ("):
        raise NativeUnknown("invalid native process stat")
    return {"pid": pid, "ppid": int(fields[1]), "start": int(fields[19]), "state": fields[0]}


def children(pid, proc=Path("/proc")):
    tasks = list((Path(proc) / str(pid) / "task").iterdir())
    if not tasks or len(tasks) > MAX_THREADS:
        raise NativeUnknown("native thread inventory exceeds bound")
    result = set()
    for task in tasks:
        try:
            text = (task / "children").read_text()
        except FileNotFoundError:
            if task.exists():
                raise
            continue  # This thread exited; another snapshot observes reparenting.
        for value in text.split():
            if not value.isdecimal() or not 0 < int(value) < 2**31:
                raise NativeUnknown("invalid kernel child identity")
            result.add(int(value))
    return sorted(result)


@contextmanager
def pinned_tree(root, *, proc=Path("/proc"), opener=None, sender=None, on_pin=None):
    """Pin and validate each edge before exposing any signal-capable handle.

    A racing exit/reparent refuses the snapshot; a streaming stop may already
    have controlled earlier validated edges. There is no process-name, group
    or unbound PID fallback. A pidfd never redirects to PID reuse.
    """
    pins = []
    try:
        opener = opener or getattr(os, "pidfd_open", None)
        sender = sender or getattr(signal, "pidfd_send_signal", None)
        if opener is None or sender is None:
            raise NativeUnknown("Linux pidfd controls unavailable")
        root_before = process(root, proc)
        ancestors = {root: (None, root_before)}
        todo = [(root, p) for p in children(root, proc)]
        seen = {root}
        while todo:
            parent, pid = todo.pop()
            if pid in seen or len(seen) > MAX_MEMBERS:
                raise NativeUnknown("native ancestry is ambiguous or exceeds bound")
            seen.add(pid)
            fd = opener(pid, 0)
            pins.append((fd, None))
            before = process(pid, proc)
            if before["ppid"] != parent:
                raise NativeUnknown("native ancestry changed")
            parent_fd, parent_row = ancestors[parent]
            if parent_fd is not None:
                sender(parent_fd, 0, None, 0)
            if process(parent, proc)["start"] != parent_row["start"]:
                raise NativeUnknown("native parent birth changed")
            sender(fd, 0, None, 0)
            nested = children(pid, proc) if on_pin is None else ()
            after = process(pid, proc)
            if any(before[k] != after[k] for k in ("pid", "ppid", "start")):
                raise NativeUnknown("native process changed during inspection")
            sender(fd, 0, None, 0)
            pins[-1] = (fd, before)
            ancestors[pid] = (fd, before)
            if on_pin is not None:
                on_pin(fd, before)  # Controls only this already-validated edge.
                nested = children(pid, proc)
            todo.extend((pid, p) for p in nested)
        root_after = process(root, proc)
        if root_before["start"] != root_after["start"]:
            raise NativeUnknown("supervisor birth changed")
        yield pins
    finally:
        for fd, _ in pins:
            os.close(fd)


def inventory(root):
    with pinned_tree(root) as pins:
        return [dict(row) for _, row in pins]


def kill_owned(root):
    def kill(fd, row):
        if row["pid"] == root:
            raise NativeUnknown("supervisor cannot be a job member")
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
        except ProcessLookupError:
            pass  # The pinned process ended; never retry by PID.
    with pinned_tree(root, on_pin=kill):
        pass


def require_support():
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise NativeUnknown("Linux pidfd controls unavailable")
    fd = os.pidfd_open(os.getpid(), 0)
    try:
        signal.pidfd_send_signal(fd, 0, None, 0)
        children(os.getpid())
    finally:
        os.close(fd)


def descendant(pid, root):
    seen = set()
    while pid and len(seen) <= MAX_MEMBERS:
        if pid == root:
            return True
        if pid in seen:
            raise NativeUnknown("caller ancestry ambiguous")
        seen.add(pid)
        pid = process(pid)["ppid"]
    if pid:
        raise NativeUnknown("caller ancestry exceeds bound")
    return False


JOB_ENV = {"PATH", "HOME", "LANG", "USER", "LOGNAME", "SHELL", "TMPDIR",
           "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_COLLATE", "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
           "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"}
SECRET_ENV = {"CONTAINER_API_KEY", "OPEN_BUTTON_TOKEN", "WEB_PASSWORD", "JUPYTER_TOKEN", "SSH_AUTH_SOCK",
              "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "XDG_RUNTIME_DIR", "NOTIFY_SOCKET"}


def job_environment(env, extra=()):
    if not isinstance(extra, (tuple, list)) or any(not isinstance(k, str) or not k or "=" in k or "\0" in k for k in extra):
        raise ValueError("explicit additional job environment keys required")
    allowed = JOB_ENV | set(extra)
    return {k: v for k, v in env.items() if k in allowed and k not in SECRET_ENV
            and not k.startswith(("POSTMQ_", "VAST_"))}


def peer(sock):
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def receive(sock, limit=MAX_REPLY):
    data = bytearray()
    while b"\n" not in data:
        part = sock.recv(min(4096, limit + 1 - len(data)))
        if not part or len(data) + len(part) > limit:
            raise NativeUnknown("native response is incomplete or oversized")
        data.extend(part)
    if data.count(b"\n") != 1 or not data.endswith(b"\n"):
        raise NativeUnknown("native response has trailing content")
    answer = json.loads(data)
    if not isinstance(answer, dict):
        raise NativeUnknown("native response is not an object")
    return answer


def send(sock, value):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_REPLY:
        raise NativeUnknown("native reply exceeds bound")
    sock.sendall(data)


def kill_error(error):
    return isinstance(error, OSError) and error.errno == errno.ESRCH
