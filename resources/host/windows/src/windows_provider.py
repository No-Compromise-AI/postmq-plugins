"""Nine-method Windows provider for one cooperative logical host execution gate."""
import ctypes as C
import csv
import hashlib
import json
import math
import os
import platform
from pathlib import Path,PureWindowsPath
import re
import secrets
import socket
import subprocess
import sys
import time

from . import windows_identity as native
from .windows_state import StateApi
from .windows_job import require,D,H
from .windows_guard import receive_control

CLAIM=re.compile(r"[0-9A-HJKMNP-TV-Z]{26}\Z")
REF=re.compile(r"win1:([0-9A-HJKMNP-TV-Z]{26}):([1-9][0-9]{0,18}):([0-9a-f]{64})\Z")
SAFE_ENV={"SYSTEMROOT","WINDIR","PATH","TEMP","TMP","USERPROFILE","USERNAME","LANG",
          "OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","PROGRAMFILES"}


def integer(value,name,minimum,maximum):
    require(type(value) is int and minimum<=value<=maximum,"invalid "+name);return value


def epoch(claim,fence):
    require(isinstance(claim,str) and CLAIM.fullmatch(claim),"invalid claim")
    integer(fence,"fence",1,2**63-1)
    return hashlib.sha256((claim+":"+str(fence)).encode()).hexdigest()[:32]


def clean(status):
    return bool(status.get("phase")=="terminal" and status.get("job_empty") is True
        and status.get("primary_signaled") is True and status.get("observation_failure") is False
        and type(status.get("native_exit")) is int and type(status.get("job_exit")) is int)


def environment(env):
    require(isinstance(env,dict),"invalid environment")
    result={}
    for key,value in env.items():
        if isinstance(key,str) and key.upper() in SAFE_ENV and isinstance(value,str) and "\0" not in value:
            upper=key.upper();require(upper not in result,"case-aliased environment");result[upper]=value
    require("SYSTEMROOT" in result,"SystemRoot required")
    result.update(PYTHONDONTWRITEBYTECODE="1",HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")
    return result


class WindowsProvider:
    def __init__(self,config, *, api=None,fixture_root=None,config_path=None):
        self.api=api or StateApi();self.host=native.host(self.api)
        require(config.get("expected_host")==self.host,"Windows host/boot/owner differs")
        self.config=dict(config);self.config_path=config_path;self.source=native.sources()
        self.seconds=integer(config.get("max_run_seconds"),"max_run_seconds",1,3600)
        self.job_memory=integer(config.get("job_memory_bytes"),"job_memory_bytes",64*1024**2,2**63-1)
        self.reserve=integer(config.get("minimum_available_bytes"),"minimum_available_bytes",64*1024**2,2**63-1)
        self.cpu_fixture=config.get("cpu_fixture",False)
        require(type(self.cpu_fixture) is bool,"explicit CPU fixture flag required")
        self.gpu=config.get("gpu_uuid");self.smi=config.get("nvidia_smi")
        if self.cpu_fixture:
            require(self.gpu is None and self.smi is None and
                    type(config.get("minimum_free_vram_bytes")) is int and
                    config["minimum_free_vram_bytes"]==0,"CPU fixture cannot claim GPU capacity")
            self.vram_floor=0
        else:
            require(isinstance(self.gpu,str) and re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",self.gpu),"explicit GPU UUID required")
            self.vram_floor=integer(config.get("minimum_free_vram_bytes"),"minimum_free_vram_bytes",64*1024**2,2**63-1)
            require(isinstance(self.smi,dict) and PureWindowsPath(self.smi.get("path","")).is_absolute()
                    and isinstance(self.smi.get("sha256"),str) and re.fullmatch(r"[0-9a-f]{64}",self.smi["sha256"]),"pinned NVIDIA observer required")
        require(config.get("owner_mode") in ("background","interactive","unavailable"),"invalid owner mode")
        self.executables=config.get("allowed_executables")
        require(isinstance(self.executables,dict) and self.executables,"explicit executable pins required")
        for name,pin in self.executables.items():
            require(PureWindowsPath(name).is_absolute() and isinstance(pin,str) and re.fullmatch(r"[0-9a-f]{64}",pin),"invalid executable policy")
        self.fixture=fixture_root is not None
        self.root=self.api.directory(Path(fixture_root)) if self.fixture else self.api.directory(self.api.root()/"jobs")
        self.root_id=(self.root.stat().st_dev,self.root.stat().st_ino)
        self.epochs=self.api.directory(self.root/"e");self.created={};self.guards={}

    def declare(self):
        suffix=":"+hashlib.sha256(str(self.root).encode()).hexdigest()[:16] if self.fixture else ""
        value={"receipt_provider":"generic","identity_kind":"logical",
            "identity_key":("windows-test-job:" if self.fixture else "windows-host-job:")+self.host["machine_guid"]+suffix,
            "provider_host_key":self.host["machine_guid"],
            "enforcement":{key:"advisory" for key in ("ownership","descendant_supervision","memory_accounting","revocation")},
            "capabilities":{"os":"windows","architecture":{"amd64":"x86_64","arm64":"aarch64"}.get(platform.machine().lower(),platform.machine().lower()),"backend":"job-object","test_fixture":self.fixture,
                "owner_sid":self.host["owner_sid"],"source_sha256":self.source,"gpu_uuid":self.gpu,
                "ram_control":"job commit limit, not VRAM","gpu_control":"unavailable; CPU lifecycle fixture only" if self.cpu_fixture else "sampled headroom and advisory UUID environment binding",
                "native_qualification":"pending; declarations stay advisory until host evidence exists",
                "cleanup_scope":"owned Job Object only; no physical desktop GPU-empty claim","profile_version":1}}
        if self.cpu_fixture:
            value["identity_key"]+=":cpu-fixture"
            value["capabilities"].update(cpu_fixture=True,profile_version=2)
        return value

    def generation(self):
        self.api.private(self.root)
        require((self.root.stat().st_dev,self.root.stat().st_ino)==self.root_id and native.sources()==self.source,"native root/source changed")

    def gpu_observation(self):
        if self.cpu_fixture:return None
        name=self.smi["path"]
        require(hashlib.sha256(Path(name).read_bytes()).hexdigest()==self.smi["sha256"],"GPU observer pin differs")
        raw=subprocess.check_output([name,"--id="+self.gpu,"--query-gpu=uuid,memory.total,memory.free",
            "--format=csv,noheader,nounits"],text=True,timeout=3,stderr=subprocess.DEVNULL)
        rows=list(csv.reader(raw.splitlines()));require(len(rows)==1 and len(rows[0])==3,"GPU observation unavailable")
        uuid,total,free=(x.strip() for x in rows[0]);require(uuid==self.gpu and total.isdigit() and free.isdigit(),"GPU observation differs")
        total,free=int(total)*1024**2,int(free)*1024**2
        require(0<=free<=total and total>0,"GPU headroom invalid")
        return {"uuid":uuid,"total_bytes":total,"free_bytes":free,"measurement":"sampled NVML via nvidia-smi; not a reservation"}

    def owner(self):
        if self.config_path is None:return self.config["owner_mode"]
        current=self.api.json_read(self.config_path)
        require({k:v for k,v in current.items() if k!="owner_mode"}=={k:v for k,v in self.config.items() if k!="owner_mode"},"runtime policy changed")
        require(current.get("owner_mode") in ("background","interactive","unavailable"),"owner policy unavailable")
        return current["owner_mode"]

    def native_record(self,path,expected=None):
        self.api.private(path);info=self.api.json_read(path/"identity.json");status=self.api.json_read(path/"status.json")
        seal=native.digest(info);ref=f"win1:{info['claim_id']}:{info['fence']}:{seal}"
        require(info["host"]==self.host and info["source_sha256"]==self.source
            and info["epoch_inode"]==path.stat().st_ino and path.name==epoch(info["claim_id"],info["fence"])
            and status["identity_sha256"]==seal and (expected is None or ref==expected),"native identity differs")
        return info,status,ref

    def preflight(self,claim):
        try:
            epoch(claim["claim_id"],claim["fence"]);require(claim.get("state")=="granted","claim not granted")
            require(self.telemetry()[1]=="healthy","owner/headroom/native state blocks admission")
            return {"result":"admitted"}
        except Exception:return {"result":"refused","reason":"Windows owner/headroom or retained native state blocks admission","cleanup":"pristine","health":"degraded"}

    def container(self,claim_id,fence,argv,env,release_fd):
        import msvcrt
        # The original CRT fd belongs to the SDK. Duplicate before native IO and
        # inherit only the owned duplicate through an explicit handle list.
        held=os.dup(release_fd);handle=None;lock=None
        try:
            handle=msvcrt.get_osfhandle(held)
            self.generation();epoch(claim_id,fence)
            lock=self.api.open_write(self.root/"admission")
            from .windows_state import Overlapped
            overlap=Overlapped();self.api.check(self.api.LockFileEx(lock,3,0,1,0,C.byref(overlap)),"native admission gate")
            require(self.telemetry()[1]=="healthy","owner/headroom/native epoch changed")
            require(isinstance(argv,list) and argv and all(isinstance(x,str) and "\0" not in x for x in argv),"invalid argv")
            require(argv[0] in self.executables and hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest()==self.executables[argv[0]],"runtime pin differs")
            path=self.epochs/epoch(claim_id,fence);require(not path.exists(),"epoch already consumed");self.api.directory(path)
            token=secrets.token_hex(32)
            job_env=environment(env);job_env.update(CUDA_VISIBLE_DEVICES="-1" if self.cpu_fixture else self.gpu,NVIDIA_VISIBLE_DEVICES="none" if self.cpu_fixture else self.gpu)
            frame={"claim_id":claim_id,"fence":fence,"host":self.host,"source_sha256":self.source,"token":token,
                "argv":argv,"env":job_env,"job_memory_bytes":self.job_memory,"max_run_seconds":self.seconds}
            raw=native.canonical(frame);require(len(raw)<=65536,"launch frame too large")
            startup=subprocess.STARTUPINFO();startup.lpAttributeList={"handle_list":[handle]}
            os.set_handle_inheritable(handle,True)
            guard_env=environment(dict(os.environ))
            with (path/"guard-out").open("xb") as out,(path/"guard-err").open("xb") as err:
                guard=subprocess.Popen([sys.executable,"-I","-B",str(Path(__file__).with_name("windows_guard.py")),
                    "--epoch",str(path),"--release-handle",str(handle)],stdin=subprocess.PIPE,stdout=out,stderr=err,
                    startupinfo=startup,close_fds=True,env=guard_env,
                    creationflags=0x8|0x200|0x01000000) # detach + new group; refuse if parent disallows breakaway
            guard.stdin.write(raw);guard.stdin.close()
            end=time.monotonic()+15
            while time.monotonic()<end:
                try:info,status,ref=self.native_record(path)
                except (OSError,RuntimeError,ValueError,KeyError):
                    if guard.poll() is not None:raise RuntimeError("native guard startup failed")
                    time.sleep(0.02);continue
                observed=native.process(self.api,guard.pid)
                require(observed and not observed["exited"] and observed["birth"]==info["guard"]["birth"] and info["guard"]["pid"]==guard.pid,"guard birth differs")
                require(len(ref)<=200,"native reference too long")
                self.created[ref]=token;self.guards[ref]=guard
                return {"ref":ref,"claim_id":claim_id,"fence":fence}
            raise RuntimeError("guard publication timed out; retain epoch")
        finally:
            try:
                if handle is not None:os.set_handle_inheritable(handle,False)
            finally:
                os.close(held)
                if lock is not None:self.api.close(lock) # only this independent admission OFD, not SDK gate

    def bound(self,handle):
        ref=handle if isinstance(handle,str) else handle["ref"];match=REF.fullmatch(ref)
        require(match is not None,"invalid Windows native reference")
        claim,fence,_=match.groups();fence=int(fence)
        if isinstance(handle,dict):require(handle.get("claim_id")==claim and type(handle.get("fence")) is int and handle["fence"]==fence,"handle fence differs")
        self.generation();path=self.epochs/epoch(claim,fence);info,status,ref=self.native_record(path,ref)
        return path,info,status,ref

    def journal_ref(self,handle):return self.bound(handle)[3]

    def inspect(self,handle):
        try:
            _,info,status,ref=self.bound(handle)
            if ref in self.guards:self.guards[ref].poll()
            if clean(status):state="gone"
            else:
                guard=native.process(self.api,info["guard"]["pid"])
                good=guard and guard["birth"]==info["guard"]["birth"] and not guard["exited"]
                state="alive" if good and 0<=self.api.now()-status["observed_tick"]<3 and status["phase"] in ("prepared","running") else "unverifiable"
            return {"state":state,"job_exit":status.get("job_exit") if state=="gone" else None,"job_signal":None,
                    "native_exit":status.get("native_exit"),"started":status.get("started")}
        except Exception:return {"state":"unverifiable","job_exit":None,"job_signal":None}

    def stop(self,handle,grace_seconds):
        require(type(grace_seconds) in (int,float) and math.isfinite(grace_seconds) and 0<=grace_seconds<=30,"invalid drain window")
        _,info,status,ref=self.bound(handle);require(ref in self.created,"not creator control")
        if not clean(status):
            guard=native.process(self.api,info["guard"]["pid"])
            require(guard and guard["birth"]==info["guard"]["birth"] and not guard["exited"],"creator guard no longer live")
            with socket.socket() as channel:
                channel.settimeout(2);channel.connect(("127.0.0.1",info["control_port"]))
                channel.sendall(native.canonical({"identity_sha256":native.digest(info),"token":self.created[ref],"operation":"stop","deadline":self.api.now()+2})+b"\n")
                require(receive_control(channel,2).get("accepted") is True,"guard refused control")
        end=time.monotonic()+grace_seconds
        while time.monotonic()<end and self.inspect(handle)["state"]=="alive":time.sleep(0.05)

    def release_receipt(self,handle,execution):
        state=self.inspect(handle)["state"]
        return {"cleanup":"verified_clean" if state=="gone" else "cleanup_failed" if state=="alive" else "unknown",
                "detail":"owned Windows Job Object cleanup only; desktop GPU residency and host budgets separate"}

    def reconcile_verdict(self,entries,claim_id,fence):
        refs={e.ref for e in entries if e.kind=="launched" and e.claim==claim_id and e.fence==fence}
        if len(refs)!=1:return "unknown","missing or ambiguous native launch",None
        ref=next(iter(refs));match=REF.fullmatch(ref)
        if not match or match[1]!=claim_id or int(match[2])!=fence:return "unknown","native reference differs",None
        return self.release_receipt(ref,"reconcile")["cleanup"],"retained Windows native job evidence",None

    def telemetry(self):
        try:
            self.generation();mode=self.owner();mem=native.memory(self.api);gpu=self.gpu_observation();blocked=0
            for path in self.epochs.iterdir():
                try:
                    _,_,ref=self.native_record(path);blocked+=self.inspect(ref)["state"]!="gone"
                except Exception:blocked+=1
            good=mode=="background" and mem["available_physical"]>=self.reserve+self.job_memory and (self.cpu_fixture or gpu["free_bytes"]>=self.vram_floor) and blocked==0
            return {"owner_mode":mode,"memory":mem,"gpu":gpu,"unsettled_epochs":blocked,"external_load_inventory":"incomplete"},"healthy" if good else "degraded"
        except Exception:return {"reason":"native Windows telemetry unavailable"},"unknown"


def factory(*,lending_plan=None):
    api=StateApi();path=api.root()/"owner.json"
    require(not os.environ.get("POSTMQ_HOST_STATE") and not os.environ.get("XDG_STATE_HOME"),"canonical native state required")
    config=api.json_read(path)
    if config.get("cpu_fixture",False):
        require(isinstance(lending_plan,dict) and lending_plan.get('version')==2 and
            lending_plan.get('platform')=='win32' and
            lending_plan.get('lifecycle',{}).get('policy',{}).get('cpu_fixture') is True and
            lending_plan.get('native_policy_immutable')=={k:v for k,v in config.items() if k!='owner_mode'},
            "CPU factory requires its verified lifecycle lending plan; raw host use is refused")
    return WindowsProvider(config,api=api,config_path=path)
