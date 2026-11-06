"""Block before exec; EOF from a dead SDK must never start the job."""

import os
import signal
import sys


def main():
    fd = int(sys.argv[1])
    try:
        go = os.read(fd, 1)
    finally:
        os.close(fd)
    if go != b"x" or len(sys.argv) < 3:
        return 125
    # The manager needs its bus to create the scope; the job does not. This is
    # defence in depth, not proof against same-uid cgroup migration.
    for key in list(os.environ):
        if key.startswith("POSTMQ_") or key in {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                                                "DBUS_SYSTEM_BUS_ADDRESS", "SSH_AUTH_SOCK", "NOTIFY_SOCKET"}:
            os.environ.pop(key, None)
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ", "SIGCHLD"):
        # SIGXFZ mirrors subprocess.restore_signals, including platform variants.
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, signal.SIG_DFL)
    try:
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    except BaseException:
        os._exit(126)  # Never disclose a command path in an exec traceback.


if __name__ == "__main__":
    try:
        status = main()
    except Exception:
        status = 125  # Setup/read failure: no traceback or command execution.
    sys.exit(status)
