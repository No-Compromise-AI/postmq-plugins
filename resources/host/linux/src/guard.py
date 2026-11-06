"""Retain native evidence until a verified-clean dismissal.

Only the provider starts this helper, inside an already created --scope unit.
No credentials, coordinator calls, PID signals or device contexts are used.
"""

import argparse
import ctypes
import fcntl
import os
from pathlib import Path, PurePosixPath
import re
import select
import signal
import stat
import sys
import time


UNIT = re.compile(r"postmq-claim-[0-9A-HJKMNP-TV-Z]{26}-f[1-9][0-9]{0,18}\.scope\Z")


def write_control(path, value):
    """Write an existing cgroup control; never create a lookalike regular file."""
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        data = str(value).encode("ascii")
        while data:
            count = os.write(fd, data)
            if count <= 0:
                raise OSError("cgroup control accepted no bytes")
            data = data[count:]
    finally:
        os.close(fd)


def current_scope(expected_claim, expected_fence, proc=Path("/proc"), root=Path("/sys/fs/cgroup")):
    lines = (proc / "self/cgroup").read_text().splitlines()
    paths = [line[3:] for line in lines if line.startswith("0::")]
    if len(paths) != 1:
        raise RuntimeError("guard requires one cgroup v2 scope")
    relative = PurePosixPath(paths[0])
    if (".." in relative.parts or not UNIT.fullmatch(relative.name)
            or relative.name != "postmq-claim-" + expected_claim + "-f" + str(expected_fence) + ".scope"):
        raise RuntimeError("guard is not in a claim scope")
    path = root.resolve() / str(relative).lstrip("/")
    if path.resolve() != path.absolute() or not path.is_dir():
        raise RuntimeError("claim scope path cannot be verified")
    return path


def subreaper():
    if sys.platform != "linux":
        raise RuntimeError("native guard requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    # Keep unreaped children birth-bound during cgroup placement. Ignored
    # SIGCHLD inherited from a caller could otherwise enable automatic reaping.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    # PR_SET_DUMPABLE: a same-uid job must not reopen the guard's status writer
    # via /proc or ptrace it. This does not prevent same-uid signals or escape.
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot protect guard descriptors")
    # PR_SET_CHILD_SUBREAPER affects reaping, not cgroup containment.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot establish subreaper")


def create_leaves(scope, memory_max=None, *, writer=write_control, pid=None):
    """Ordering obeys cgroup v2's no-internal-process rule."""
    scope = Path(scope)
    if memory_max is not None and (type(memory_max) is not int or memory_max <= 0
                                   or memory_max % os.sysconf("SC_PAGE_SIZE")):
        raise ValueError("memory_max must be a positive page multiple")
    guard, job = scope / "guard", scope / "job"
    guard.mkdir()  # Never adopt a pre-existing leaf from another attempt.
    writer(guard / "cgroup.procs", os.getpid() if pid is None else pid)
    writer(scope / "cgroup.subtree_control", "+memory")
    job.mkdir()
    if memory_max is not None:
        writer(job / "memory.max", memory_max)
    return guard, job


def _wait(pid):
    while True:
        try:
            reaped, status = os.waitpid(-1, 0)
            if reaped == pid:
                return status
        except InterruptedError:
            continue


class JobExecError(RuntimeError):
    """The error channel proved exec failed, rather than losing native status."""
    def __init__(self, marker, status):
        super().__init__("requested executable did not start")
        self.marker, self.status = marker, status


def launch_job(argv, release_fd, place_child, *, close_in_child=()):
    """Return a wait status, or None on abandoned launch. No argv runs on EOF.

    The child waits for placement, then the SDK byte, inside job/. There is one
    prepared child before container() returns, so a stop of an unreleased job
    cannot race a later fork into an already-killed empty job leaf.
    """
    if not argv or not argv[0] or not all(isinstance(x, str) and "\0" not in x for x in argv):
        raise ValueError("explicit argv required")
    read_fd, write_fd = os.pipe()
    try:
        error_r, error_w = os.pipe()  # close-on-exec; explicit never-ran markers
    except BaseException:
        os.close(read_fd); os.close(write_fd)
        raise
    try:
        pid = os.fork()
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        os.close(error_r); os.close(error_w)
        raise
    if pid == 0:
        os.close(write_fd)
        os.close(error_r)
        def never_ran(marker, code):
            try:
                os.write(error_w, marker)
            finally:
                os._exit(code)
        try:
            for fd in close_in_child:
                if fd != read_fd:
                    os.close(fd)
            go = os.read(read_fd, 1)
            os.close(read_fd)
            if not go:
                never_ran(b"A", 125)
            go = os.read(release_fd, 1)
            os.close(release_fd)
            if go != b"x":
                never_ran(b"A", 125)
            # No inherited manager bus or SDK credentials are available to argv.
            for key in list(os.environ):
                if key.startswith("POSTMQ_") or key in {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                                                        "DBUS_SYSTEM_BUS_ADDRESS", "SSH_AUTH_SOCK", "NOTIFY_SOCKET"}:
                    os.environ.pop(key, None)
            for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ", "SIGCHLD"):
                # SIGXFZ is intentional: it mirrors subprocess.restore_signals.
                signum = getattr(signal, name, None)
                if signum is not None:
                    signal.signal(signum, signal.SIG_DFL)
            os.write(error_w, b"R")  # This child received the SDK release byte.
            os.execvpe(argv[0], argv, os.environ)
        except BaseException:
            never_ran(b"F", 126)
    os.close(error_w)
    try:
        os.close(read_fd)
        os.close(release_fd)  # The blocked child holds the helper's read copy.
        place_child(pid)
        os.write(write_fd, b"x")
    except BaseException as error:
        os.close(write_fd)
        status = _wait(pid)  # EOF made the blocked child exit without exec.
        os.close(error_r)
        if isinstance(error, BrokenPipeError) and os.WIFSIGNALED(status):
            return None  # A stopped prepared child never received placement.
        raise
    else:
        os.close(write_fd)
    try:
        status = _wait(pid)
        marker = os.read(error_r, 16)
    finally:
        os.close(error_r)
    if marker == b"A" or (not marker and os.WIFSIGNALED(status)):
        return None
    if marker != b"R":
        raise JobExecError(marker, status)
    return status


def report_status(fd, status):
    """The guard survives a sidecar disappearing after launch."""
    try:
        if os.WIFEXITED(status):
            data = ("exit " + str(os.WEXITSTATUS(status)) + "\n").encode("ascii")
        elif os.WIFSIGNALED(status):
            data = ("signal " + str(os.WTERMSIG(status)) + "\n").encode("ascii")
        else:
            raise RuntimeError("job wait status is not terminal")
        while data:
            count = os.write(fd, data)
            if count <= 0:
                raise OSError("status pipe accepted no bytes")
            data = data[count:]
    except BrokenPipeError:
        pass
    finally:
        os.close(fd)


def hold_evidence():
    """No self-timeout: the provider dismisses guard/ with cgroup.kill."""
    while True:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        time.sleep(0.2)


def reader_present(fd):
    poll = select.poll()
    poll.register(fd, select.POLLERR | select.POLLHUP)
    return not poll.poll(0)


def hold_unexecuted(fd, marker, published):
    """Retain published evidence even when the SDK process has died.

    EOF without a publication byte permits an unexecuted helper to finish.
    After publication, reader loss can be a crash after journal fsync and must
    not discard the native proof needed by a new reconciler.
    """
    try:
        try:
            os.write(fd, marker)
        except BrokenPipeError:
            pass
        if published:
            hold_evidence()  # Reader loss after publication is a recovery case.
    finally:
        os.close(fd)


def parse_cli(argv):
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("invalid guard invocation")
    values = list(argv)
    if "--" not in values:
        raise ValueError("guard requires an explicit command separator")
    separator = values.index("--")
    command = values[separator + 1:]
    options = values[:separator]
    for name in ("--claim", "--fence", "--memory-max", "--publish-fd"):
        if sum(value.split("=", 1)[0] == name for value in options) > 1:
            raise ValueError("guard option repeated")
    parser = QuietParser(add_help=False, allow_abbrev=False)
    parser.add_argument("release_fd", type=int)
    parser.add_argument("status_fd", type=int)
    parser.add_argument("--memory-max", type=int)
    parser.add_argument("--claim", required=True)
    parser.add_argument("--fence", required=True, type=int)
    parser.add_argument("--publish-fd", required=True, type=int)
    args = parser.parse_args(options)
    descriptors = (args.release_fd, args.status_fd, args.publish_fd)
    if min(descriptors) < 3 or len(set(descriptors)) != 3:
        raise ValueError("two distinct inherited pipe descriptors required")
    if not command:
        raise ValueError("explicit job argv required")
    if (not command[0] or not 0 < args.fence <= 2**63 - 1
            or not UNIT.fullmatch("postmq-claim-" + args.claim + "-f" + str(args.fence) + ".scope")):
        raise ValueError("invalid job or expected claim")
    if args.memory_max is not None and (args.memory_max <= 0 or args.memory_max % os.sysconf("SC_PAGE_SIZE")):
        raise ValueError("memory_max must be a positive page multiple")
    args.command = command
    return args


def validate_pipes(release_fd, status_fd, publish_fd=None):
    identities = []
    descriptors = [(release_fd, os.O_RDONLY), (status_fd, os.O_WRONLY)]
    if publish_fd is not None:
        descriptors.append((publish_fd, os.O_RDONLY))
    for fd, direction in descriptors:
        info = os.fstat(fd)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if (not stat.S_ISFIFO(info.st_mode) or flags & os.O_ACCMODE != direction
                or flags & os.O_NONBLOCK):
            raise ValueError("guard descriptors must be directed pipes")
        identities.append((info.st_dev, info.st_ino))
    if len(set(identities)) != len(identities):
        raise ValueError("release and status require separate pipes")


def main(argv=None):
    args = parse_cli(sys.argv[1:] if argv is None else argv)
    validate_pipes(args.release_fd, args.status_fd, args.publish_fd)
    if not reader_present(args.status_fd):
        os.close(args.release_fd); os.close(args.status_fd); os.close(args.publish_fd)
        return 125
    subreaper()
    _guard, job = create_leaves(current_scope(args.claim, args.fence), args.memory_max)
    marker = b"aborted\n"
    try:
        status = launch_job(args.command, args.release_fd, lambda pid: write_control(job / "cgroup.procs", pid),
                            close_in_child=(args.status_fd, args.publish_fd))
    except Exception:
        status = None
        marker = b"failed\n"
    if status is None:
        try:
            published = os.read(args.publish_fd, 1) == b"p"
        finally:
            os.close(args.publish_fd)
        hold_unexecuted(args.status_fd, marker, published)
        return 125  # reachable only in a test replacing the infinite hold
    try:
        os.close(args.publish_fd)
    except OSError:
        pass  # A real job ran; descriptor cleanup cannot discard its evidence.
    try:
        report_status(args.status_fd, status)
    except Exception:
        # After launch, loss of the status channel must never discard native
        # evidence. A future reconcile can still inspect the job leaf.
        pass
    hold_evidence()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # No argv/environment/command output is logged on setup failures.
        sys.stderr.write("postmq native guard: setup or job placement failed\n")
        sys.exit(125)
