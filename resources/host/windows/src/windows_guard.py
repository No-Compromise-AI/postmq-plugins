"""Per-job Windows supervisor. A Job Object, not a remote SSH session, owns descendants."""
import argparse
import ctypes as C
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import select
import socket
import sys
import threading
import time


def receive_control(channel, seconds=0.5):
    """A newline-delimited bounded frame; TCP reads are not message frames."""
    end=time.monotonic()+seconds;raw=bytearray()
    while True:
        remaining=end-time.monotonic()
        if remaining<=0:raise ValueError("control frame deadline")
        channel.settimeout(remaining)
        block=channel.recv(min(4096,8193-len(raw)))
        if not block:raise ValueError("control frame EOF")
        raw.extend(block)
        if len(raw)>8192:raise ValueError("control frame too large")
        if b"\n" in raw:
            if not raw.endswith(b"\n") or raw.count(b"\n")!=1:raise ValueError("control frame suffix")
            value=json.loads(bytes(raw[:-1]))
            if not isinstance(value,dict):raise ValueError("control object required")
            return value


def modules():
    spec=importlib.util.spec_from_file_location("guard_entry",Path(__file__).with_name("entry.py"))
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    return entry.module("windows_state"),entry.module("windows_identity"),entry.module("windows_job")


def run():
    state,identity,job_module=modules();require=job_module.require
    parser=argparse.ArgumentParser();parser.add_argument("--epoch",type=Path,required=True)
    parser.add_argument("--release-handle",type=int,required=True);args=parser.parse_args()
    frame=json.loads(sys.stdin.buffer.read(65537));api=state.StateApi()
    require(identity.host(api)==frame["host"] and identity.sources()==frame["source_sha256"],"guard generation differs")
    kernel=identity.kernel_observation()
    require(kernel['machine_guid']==frame['host']['machine_guid'] and
            kernel['boot_identifier']==frame['host']['boot_identifier'],'guard kernel observation differs')
    api.private(args.epoch);sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.bind(("127.0.0.1",0));sock.listen(4)
    job=None
    try:
        job=job_module.SuspendedJob(frame["argv"],frame["env"],str(args.epoch),args.epoch,frame["job_memory_bytes"],api=api)
        info={"version":1,"claim_id":frame["claim_id"],"fence":frame["fence"],"host":frame["host"],
              "source_sha256":frame["source_sha256"],"epoch_inode":args.epoch.stat().st_ino,
              "kernel_observation":kernel,"kernel_source_sha256":identity.kernel_source_sha256(),
              "guard":{"pid":os.getpid(),"birth":api.birth(api.GetCurrentProcess())},
              "job":job.identity,"control_port":sock.getsockname()[1],"profile":"windows-job-object-v1"}
        seal=identity.digest(info)
        api.json_write(args.epoch/"identity.json",info)
        status={"identity_sha256":seal,"phase":"prepared","started":False,"job_exit":None,
                "native_exit":None,"reason":None,"observation_failure":False,"job_empty":False,"primary_signaled":False}
        def save():
            status["observed_tick"]=api.now();api.json_write(args.epoch/"status.json",status,replace=True)
        save()
        release={};ready=threading.Event()
        def read_release():
            import msvcrt
            fd=None
            try:
                fd=msvcrt.open_osfhandle(args.release_handle,os.O_RDONLY|os.O_BINARY)
                release["byte"]=os.read(fd,1)
            except Exception:release["error"]=True
            finally:
                if fd is not None:os.close(fd)
                ready.set()
        threading.Thread(target=read_release,daemon=True).start()
        deadline=api.now()+frame["max_run_seconds"];stopped=False;last_write=0
        while True:
            now=api.now()
            if ready.is_set() and not job.resumed and not stopped:
                if release.get("byte") and not release.get("error"):
                    # The suspended child is already assigned to the private
                    # job. Only the SDK's release byte makes its thread runnable.
                    job.resume();status.update(started=True,phase="running")
                else:
                    status["reason"]="unwritten release EOF";job.terminate(125);stopped=True
            if now>=deadline and not stopped:
                status["reason"]="execution deadline";job.terminate(124);stopped=True
            seen=job.snapshot()
            if seen["managed_job_empty"]:
                actual=seen["job_exit"]
                outcome=124 if status["reason"]=="execution deadline" else 125 if status["reason"] in ("unwritten release EOF","owned stop") else actual
                status.update(phase="terminal",job_exit=outcome,native_exit=actual,job_empty=True,primary_signaled=True,
                              total_processes=seen["total_processes"])
                save();return
            if now>=deadline+5:raise RuntimeError("native job drain unverified")
            if now-last_write>=0.5:save();last_write=now
            readable,_,_=select.select([sock],[],[],0.05)
            if readable:
                try:connection,_=sock.accept()
                except OSError:continue
                with connection:
                    accepted=False;connection.settimeout(0.5)
                    try:
                        request=receive_control(connection)
                        require(request.get("identity_sha256")==seal and request.get("operation")=="stop","control identity differs")
                        require(secrets.compare_digest(request.get("token",""),frame["token"]),"not creator authority")
                        until=request.get("deadline")
                        require(type(until) in (int,float) and math.isfinite(until) and 0<=until-api.now()<=2,"control deadline differs")
                        job.terminate(125);stopped=True;status["reason"]="owned stop";accepted=True
                    except Exception:pass
                    try:connection.sendall(b'{"accepted":true}\n' if accepted else b'{"accepted":false}\n')
                    except OSError:pass
    except BaseException:
        # Closing this guard's only Job Object handle kills the owned tree.
        # Without an observed empty job, retained status stays unknown.
        if "status" in locals():
            status.update(phase="unverifiable",observation_failure=True,reason="native supervisor failure")
            try:save()
            except Exception:pass
        raise
    finally:
        if job is not None:job.close()
        sock.close()


if __name__=="__main__":run()
