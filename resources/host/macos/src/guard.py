"""Finite host-side supervisor; only controls its own unreaped child's session."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import secrets
import select
import signal
import socket
import subprocess
import sys


TRAMPOLINE = r'''
import json,os,resource,sys
release,notify=int(sys.argv[1]),int(sys.argv[2])
frame=json.loads(sys.stdin.buffer.read(65537))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_FSIZE,(frame['output_limit'],frame['output_limit']))
resource.setrlimit(resource.RLIMIT_CPU,(frame['cpu_limit'],frame['cpu_limit']))
byte=os.read(release,1);os.close(release)
if len(byte)!=1:os._exit(125)
if os.write(notify,b'1')!=1:os._exit(126)
os.close(notify)
os.execve(frame['argv'][0],frame['argv'],frame['env'])
'''


def native_module():
    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("mac_guard_entry", root / "entry.py")
    entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
    return entry.module("native")


def run():
    n = native_module(); parser = argparse.ArgumentParser()
    parser.add_argument("--epoch", type=Path, required=True); parser.add_argument("--release-fd", type=int, required=True)
    args = parser.parse_args(); os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    frame = json.loads(sys.stdin.buffer.read(65537)); d = n.Darwin()
    n.require(d.host() == frame["host"], "guard host generation differs")
    n.require(n.source_digest() == frame["source_sha256"], "guard source bundle differs")
    fd = n.private_dir(args.epoch); listener = socket.socket(socket.AF_UNIX)
    n.require(len(os.fsencode(args.epoch / "s")) < 100, "native socket path too long")
    listener.bind(str(args.epoch / "s")); os.chmod(args.epoch / "s", 0o600); listener.listen(4)
    activation_r, activation_w = os.pipe(); os.set_blocking(activation_r, False)
    with (args.epoch / "out").open("xb") as out, (args.epoch / "err").open("xb") as err:
        child = subprocess.Popen([sys.executable, "-I", "-c", TRAMPOLINE, str(args.release_fd), str(activation_w)],
            stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True,
            pass_fds=(args.release_fd, activation_w), env=frame["env"])
    os.close(args.release_fd); os.close(activation_w)
    # Retain this Popen unreaped until all controls and the terminal census end.
    job = d.process(child.pid); me = d.process(os.getpid())
    n.require(job and job["ppid"] == os.getpid() and job["pgid"] == child.pid, "child session differs")
    kq = select.kqueue()
    event = select.kevent(child.pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                         fflags=select.KQ_NOTE_EXIT | select.KQ_NOTE_FORK | select.KQ_NOTE_EXEC | 0x04000000)
    kq.control([event], 0, 0)
    identity = {"version":1, "claim_id":frame["claim_id"], "fence":frame["fence"], "host":frame["host"],
                "epoch_inode":os.fstat(fd).st_ino, "guard":me, "job":job,
                "guard_sha256":frame["guard_sha256"], "source_sha256":frame["source_sha256"], "profile":"fork-free-session-v1"}
    seal = n.digest(identity); n.write_json(fd, "identity.json", identity)
    status = {"identity_sha256":seal, "phase":"prepared", "fork_seen":False, "kernel_exit_observed":False,
              "started":False, "job_exit":None, "job_signal":None, "reason":None}
    start = d.now(); deadline = start + frame["max_run_seconds"]; killed = False; last_write = -1
    child.stdin.write(n.canonical({"argv":frame["argv"], "env":frame["env"],
                                  "output_limit":frame["output_limit"], "cpu_limit":frame["max_run_seconds"]}))
    child.stdin.close()

    def save():
        status["observed_continuous"] = d.now(); status["observed_at"] = n.utc_now()
        n.write_json(fd, "status.json", status, replace=True)

    def owned_signal(signum):
        nonlocal killed
        n.require(not status.get("reaped_by_guard"), "no signal after reap")
        # libproc normally hides zombies. Our own unreaped child still fences
        # its PID/PGID after exit, including a fork-and-immediate-parent-exit.
        current = d.process(child.pid, include_zombie=True)
        n.require(current and current["birth"] == job["birth"] and current["uid"] == job["uid"]
                  and current["pgid"] == job["pgid"], "unreaped session identity differs")
        try: os.killpg(job["pgid"], signum)
        except ProcessLookupError: pass
        if signum == signal.SIGKILL: killed = True

    save()
    try:
        while True:
            now = d.now()
            for observed in kq.control([], 8, 0):
                if observed.flags & select.KQ_EV_ERROR:
                    status["reason"] = "kernel observation error"
                if observed.fflags & select.KQ_NOTE_FORK:
                    status["fork_seen"] = True; status["reason"] = "fork outside initial supported profile"
                if observed.fflags & select.KQ_NOTE_EXIT:
                    status["kernel_exit_observed"] = True
            try:
                byte = os.read(activation_r, 16)
                if byte: status["started"] = True; status["phase"] = "running"
            except BlockingIOError: pass
            if (now >= deadline or status["fork_seen"] or status["reason"]) and not killed:
                status["reason"] = status["reason"] or "execution deadline"
                owned_signal(signal.SIGKILL)
            if status["kernel_exit_observed"]:
                members = d.group(job["pgid"])
                if not set(members).issubset({child.pid}) and not killed:
                    status["reason"] = "unexpected session membership"; owned_signal(signal.SIGKILL)
                if set(members).issubset({child.pid}) or now >= deadline + 5:
                    if not set(members).issubset({child.pid}): status["reason"] = "session drain unverified"
                    code = child.wait(timeout=0.5)  # never a signal after this point
                    status.update(reaped_by_guard=True, group_before_reap=members,
                        job_exit=code if code >= 0 else None, job_signal=-code if code < 0 else None,
                        phase="terminal")
                    save(); break
            elif now >= deadline + 5:
                raise RuntimeError("exit observation did not settle within the finite drain")
            if now - last_write >= 0.5:
                save(); last_write = now
            readable, _, _ = select.select([listener], [], [], 0.05)
            if readable:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(0.5)
                    accepted = False
                    try:
                        raw = connection.recv(8193); n.require(len(raw) <= 8192, "control frame too large")
                        request = json.loads(raw)
                        n.require(request.get("identity_sha256") == seal, "control identity differs")
                        n.require(secrets.compare_digest(request.get("token", ""), frame["token"]), "not creator control")
                        n.require(d.now() <= request["deadline"], "control deadline expired")
                        signum = request["signal"]
                        n.require(signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGKILL), "signal not allowed")
                        owned_signal(signum)
                        accepted = True
                    except Exception:
                        pass
                    # A timed-out/disconnected requester must not terminate the
                    # native supervisor or surrender its unreaped-child fence.
                    try: connection.sendall(b'{"accepted":true}\n' if accepted else b'{"accepted":false}\n')
                    except OSError: pass
    except Exception:
        status["reason"] = "supervisor observation failure"
        # No wider fallback or claim that an unobserved tree was collected.
        try:
            if not status.get("reaped_by_guard"): owned_signal(signal.SIGKILL)
        except Exception: pass
        status["phase"] = "unverifiable"; save()
    finally:
        listener.close(); kq.close(); os.close(activation_r); os.close(fd)


if __name__ == "__main__": run()
