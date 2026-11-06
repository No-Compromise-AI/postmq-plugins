"""One prepared rental job and a surviving, private native supervisor.

No cgroup/systemd requirement, network API, SDK gate or claim journal. The
only socket is private Unix IPC. Non-clean evidence remains in this live guard;
losing it yields unknown rather than replaying a cached clean verdict.
"""

import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import threading
import time


def sibling(name):
    spec = importlib.util.spec_from_file_location("rental_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = sibling("guard")
native = sibling("rental_native")


def write_line(fd, value):
    data = json.dumps(value, allow_nan=False).encode() + b"\n"
    while data:
        n = os.write(fd, data)
        if n <= 0:
            raise RuntimeError("native ready channel failed")
        data = data[n:]


def main():
    if len(sys.argv) != 6:
        return 2
    data_fd, release_fd, ready_fd, publish_fd = map(int, sys.argv[1:5])
    directory = Path(sys.argv[5])
    # Configuration travels in a pipe, never command arguments or a state file.
    with os.fdopen(data_fd, "rb") as stream:
        raw = stream.read(1048577)
    if len(raw) > 1048576:
        return 2
    config = json.loads(raw)
    common.subreaper()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        # Caught handlers, unlike SIG_IGN, reset to defaults at the job's exec.
        signal.signal(sig, lambda *_: None)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    own = native.process(os.getpid())
    inode = directory.stat().st_ino
    ref = "procscan:" + ":".join(map(str, (config["claim_id"], config["fence"], boot,
                                             own["pid"], own["start"], inode)))
    endpoint = directory / "guard.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint)); os.chmod(endpoint, 0o600); server.listen(8); server.settimeout(0.2)
    ended = threading.Event()
    dismissed = threading.Event()
    lock = threading.Lock()
    membership_lock = threading.RLock()
    state = {"job_exit": None, "job_signal": None, "published": False}

    def snapshot():
        if directory.stat().st_ino != inode:
            raise native.NativeUnknown("native directory identity changed")
        with membership_lock:
            members = native.inventory(os.getpid())
        with lock:
            result = dict(state)
        # A zombie thread-group leader can still have live threads. Empty
        # membership, including collected zombies, is the conservative boundary.
        result.update(ref=ref, members=members, state="alive" if members else "gone",
                      job_ended=ended.is_set())
        if result.get("error"):
            result["state"] = "unverifiable"
        return result

    def serve():
        while not dismissed.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                try:
                    caller, uid, _ = native.peer(connection)
                    if uid != os.getuid() or native.descendant(caller, os.getpid()):
                        raise native.NativeUnknown("native caller uid differs")
                    request = native.receive(connection, 2048)
                    if request.get("ref") != ref:
                        raise native.NativeUnknown("native claim identity differs")
                    op = request.get("op")
                    if op == "inspect":
                        answer = snapshot()
                    elif op == "stop":
                        grace = request.get("grace", 0)
                        if type(grace) not in (int, float) or not 0 <= grace <= 10:
                            raise native.NativeUnknown("native drain bound invalid")
                        deadline = time.monotonic() + max(grace, native.STOP_DRAIN_FLOOR)
                        while True:
                            try:
                                with membership_lock:
                                    native.kill_owned(os.getpid())
                                answer = snapshot()
                            except (native.NativeUnknown, OSError):
                                answer = {"ref": ref, "state": "unverifiable", "job_exit": None,
                                          "job_signal": None, "reason": "native membership changed during stop"}
                            if answer["state"] == "gone" or time.monotonic() >= deadline:
                                break
                            time.sleep(0.05)
                    elif op == "dismiss":
                        answer = snapshot()
                        if not ended.is_set() or answer["members"]:
                            raise native.NativeUnknown("cannot dismiss a live job")
                        # No remaining descendant can fork after both empty scans.
                        if native.inventory(os.getpid()):
                            raise native.NativeUnknown("job membership changed")
                        answer["dismissed"] = True
                        native.send(connection, answer)
                        dismissed.set()
                        continue
                    else:
                        raise native.NativeUnknown("unsupported native operation")
                    native.send(connection, answer)
                except Exception:
                    try:
                        native.send(connection, {"ref": ref, "state": "unverifiable",
                                                 "error": "native operation could not be verified"})
                    except OSError:
                        pass

    def prepared(pid):
        before = native.process(pid)
        if before["ppid"] != os.getpid():
            raise native.NativeUnknown("prepared job is not a direct child")
        threading.Thread(target=serve, daemon=True).start()  # Fork already happened.
        write_line(ready_fd, {"ref": ref, "prepared_pid": pid, "prepared_start": before["start"]})
        os.close(ready_fd)
        try:
            ready, _, _ = select.select([publish_fd], [], [], 8)
            if not ready or os.read(publish_fd, 1) != b"x":
                raise native.NativeUnknown("unpublished job must not execute")
        finally:
            os.close(publish_fd)
        with lock:
            state["published"] = True

    # Only the explicit job environment reaches exec. Management credentials
    # and control-channel discovery variables never do.
    environment = native.job_environment(config["env"], config.get("allowed_job_env", ()))
    os.environ.clear(); os.environ.update(environment)
    try:
        status = common.launch_job(config["argv"], release_fd, prepared,
                                   close_in_child=(ready_fd, publish_fd, server.fileno()))
        with lock:
            if status is None:
                state["job_aborted"] = True
            elif os.WIFEXITED(status):
                state["job_exit"] = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                state["job_signal"] = os.WTERMSIG(status)
            else:
                state["error"] = "primary terminal status unavailable"
    except common.JobExecError as error:
        with lock:
            if error.marker in (b"F", b"RF") and os.WIFEXITED(error.status):
                state["job_failed_to_start"] = True
                state["job_exit"] = os.WEXITSTATUS(error.status)
            else:
                state["error"] = "primary launch status unavailable"
    except Exception:
        with lock:
            state["error"] = "primary launch or status unavailable"
        if not state["published"]:
            dismissed.set()
    finally:
        ended.set()
    try:
        while not dismissed.is_set():
            try:
                with membership_lock:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
            except ChildProcessError:
                pass
            time.sleep(0.05)
    finally:
        server.close()
        if directory.stat().st_ino == inode:
            endpoint.unlink(missing_ok=True)
            directory.rmdir()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("rental supervisor setup failed", file=sys.stderr)
        sys.exit(2)
