"""Windows 11 x64 Job Object primitive. Kernel handles never leave the guard.

No coordinator, PID-based kill, breakaway flag or GPU-empty assertion here.
An owner must provide an already-private local output directory and a validated
runtime/environment. Importing this module on another OS does not load a DLL.
"""
import ctypes as C
from pathlib import PureWindowsPath
import subprocess
import sys

D = C.c_uint32
W = C.c_uint16
Q = C.c_uint64
H = C.c_void_p
SIZE = C.c_size_t
BOOL = C.c_int32
INVALID_HANDLE = C.c_void_p(-1).value
CREATE_SUSPENDED = 0x4
KILL_ON_JOB_CLOSE = 0x2000
JOB_MEMORY = 0x200


class FileTime(C.Structure):
    _fields_ = [("low",D),("high",D)]


class SecurityAttributes(C.Structure):
    _fields_ = [("size",D),("descriptor",H),("inherit",BOOL)]


class StartupInfo(C.Structure):
    _fields_ = [("size",D),("reserved",C.c_wchar_p),("desktop",C.c_wchar_p),("title",C.c_wchar_p)]
    _fields_ += [(name,D) for name in ("x","y","width","height","chars_x","chars_y","fill","flags")]
    _fields_ += [("show",W),("reserved_size",W),("reserved_bytes",H),("stdin",H),("stdout",H),("stderr",H)]


class StartupInfoEx(C.Structure):
    _fields_ = [("startup",StartupInfo),("attributes",H)]


class ProcessInfo(C.Structure):
    _fields_ = [("process",H),("thread",H),("pid",D),("tid",D)]


class BasicLimits(C.Structure):
    _fields_ = [("process_time",C.c_int64),("job_time",C.c_int64),("flags",D),
                ("min_working_set",SIZE),("max_working_set",SIZE),("active_limit",D),
                ("affinity",SIZE),("priority",D),("scheduling",D)]


class IoCounters(C.Structure):
    _fields_ = [(name,Q) for name in ("read_ops","write_ops","other_ops","read_bytes","write_bytes","other_bytes")]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic",BasicLimits),("io",IoCounters),("process_memory",SIZE),("job_memory",SIZE),
                ("peak_process_memory",SIZE),("peak_job_memory",SIZE)]


class Accounting(C.Structure):
    _fields_ = [(name,C.c_int64) for name in ("user_time","kernel_time","period_user","period_kernel")]
    _fields_ += [(name,D) for name in ("page_faults","total_processes","active_processes","terminated_processes")]


def require(value, message):
    if not value: raise RuntimeError(message)


def environment_block(env):
    require(isinstance(env,dict) and env, "explicit Windows environment required")
    keys = set(); rows = []
    for key,value in sorted(env.items(),key=lambda item:item[0].casefold()):
        require(isinstance(key,str) and key and "=" not in key and "\0" not in key
                and isinstance(value,str) and "\0" not in value, "invalid environment entry")
        require(key.casefold() not in keys,"case-aliased environment key")
        keys.add(key.casefold());rows.append(key+"="+value)
    require("systemroot" in keys,"SystemRoot required")
    return "\0".join(rows)+"\0\0"


class Api:
    def __init__(self):
        require(sys.platform=="win32" and C.sizeof(H)==8,"Windows x64 required")
        self.dll = C.WinDLL("kernel32",use_last_error=True)
        prototypes = {
            "CreateJobObjectW":([H,C.c_wchar_p],H),
            "SetInformationJobObject":([H,C.c_int32,H,D],BOOL),
            "QueryInformationJobObject":([H,C.c_int32,H,D,C.POINTER(D)],BOOL),
            "AssignProcessToJobObject":([H,H],BOOL),"TerminateJobObject":([H,D],BOOL),
            "TerminateProcess":([H,D],BOOL),"ResumeThread":([H],D),
            "CloseHandle":([H],BOOL),"GetHandleInformation":([H,C.POINTER(D)],BOOL),
            "WaitForSingleObject":([H,D],D),"GetExitCodeProcess":([H,C.POINTER(D)],BOOL),
            "GetProcessTimes":([H,C.POINTER(FileTime),C.POINTER(FileTime),C.POINTER(FileTime),C.POINTER(FileTime)],BOOL),
            "CreateFileW":([C.c_wchar_p,D,D,C.POINTER(SecurityAttributes),D,D,H],H),
            "InitializeProcThreadAttributeList":([H,D,D,C.POINTER(SIZE)],BOOL),
            "UpdateProcThreadAttribute":([H,D,SIZE,H,SIZE,H,H],BOOL),
            "DeleteProcThreadAttributeList":([H],None),
            "CreateProcessW":([C.c_wchar_p,C.c_wchar_p,H,H,BOOL,D,H,C.c_wchar_p,C.POINTER(StartupInfo),C.POINTER(ProcessInfo)],BOOL),
            "GetTickCount64":([],Q)}
        for name,(args,result) in prototypes.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=result;setattr(self,name,fn)

    def check(self, value, operation):
        if not value: raise OSError(C.get_last_error(),operation+" failed")
        return value

    def close(self, handle):
        if handle not in (None,0,INVALID_HANDLE): self.check(self.CloseHandle(handle),"CloseHandle")

    def birth(self, process):
        created,exited,kernel,user = FileTime(),FileTime(),FileTime(),FileTime()
        self.check(self.GetProcessTimes(process,C.byref(created),C.byref(exited),C.byref(kernel),C.byref(user)),"GetProcessTimes")
        return str((created.high<<32)|created.low)

    def now(self): return self.GetTickCount64()/1000


class SuspendedJob:
    """Own unnamed Job Object, created child suspended, assign before resume."""
    def __init__(self, argv, env, cwd, output, memory_bytes, *, api=None):
        require(isinstance(argv,list) and argv and all(isinstance(x,str) and "\0" not in x for x in argv),"invalid argv")
        require(PureWindowsPath(argv[0]).is_absolute(),"absolute Windows executable required")
        require(type(memory_bytes) is int and 64*1024**2 <= memory_bytes <= 2**63-1,"invalid job commit limit")
        self.api=api or Api();a=self.api;self.job=None;self.process=None;self.thread=None;self.resumed=False;self.closed=False
        require(C.sizeof(ExtendedLimits)==144 and C.sizeof(Accounting)==48,"native x64 ABI differs")
        self.job=a.check(a.CreateJobObjectW(None,None),"CreateJobObjectW")
        try:
            flags=D();a.check(a.GetHandleInformation(self.job,C.byref(flags)),"GetHandleInformation")
            require(not flags.value&1,"job handle must not be inheritable")
            limits=ExtendedLimits();limits.basic.flags=KILL_ON_JOB_CLOSE|JOB_MEMORY;limits.job_memory=memory_bytes
            a.check(a.SetInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits)),"SetInformationJobObject")
            self._spawn(argv,env,cwd,output)
            a.check(a.AssignProcessToJobObject(self.job,self.process),"AssignProcessToJobObject")
            self.identity={"pid":self.pid,"birth":a.birth(self.process)}
            require(self.snapshot()["active_processes"]==1,"prepared job membership differs")
        except BaseException:
            # The primary is still suspended and its owned kernel handle pins
            # identity, even if assignment failed. Never signal an arbitrary PID.
            if self.process:
                a.TerminateProcess(self.process,125);a.WaitForSingleObject(self.process,2000)
            self.close();raise

    def _spawn(self,argv,env,cwd,output):
        a=self.api;handles=[];attributes=None;attributes_ready=False
        try:
            security=SecurityAttributes(C.sizeof(SecurityAttributes),None,True)
            for name in ("out","err"):
                handle=a.CreateFileW(str(output/name),0x40000000,0,C.byref(security),1,0x80000080,None)
                require(handle not in (None,0,INVALID_HANDLE),"private output CreateFileW failed");handles.append(handle)
            stdin=a.CreateFileW("NUL",0x80000000,3,C.byref(security),3,0x80,None)
            require(stdin not in (None,0,INVALID_HANDLE),"NUL CreateFileW failed");handles.append(stdin)
            size=SIZE();a.InitializeProcThreadAttributeList(None,1,0,C.byref(size))
            require(0<size.value<65536,"attribute-list size invalid")
            attributes=C.create_string_buffer(size.value)
            a.check(a.InitializeProcThreadAttributeList(attributes,1,0,C.byref(size)),"InitializeProcThreadAttributeList")
            attributes_ready=True
            allowed=(H*3)(*handles)
            a.check(a.UpdateProcThreadAttribute(attributes,0,0x20002,allowed,C.sizeof(allowed),None,None),"UpdateProcThreadAttribute")
            startup=StartupInfoEx();startup.startup.size=C.sizeof(startup);startup.startup.flags=0x100
            startup.startup.stdin=stdin;startup.startup.stdout=handles[0];startup.startup.stderr=handles[1]
            startup.attributes=C.cast(attributes,H)
            info=ProcessInfo();command=C.create_unicode_buffer(subprocess.list2cmdline(argv))
            block=C.create_unicode_buffer(environment_block(env))
            flags=CREATE_SUSPENDED|0x400|0x80000|0x08000000
            a.check(a.CreateProcessW(argv[0],command,None,None,True,flags,block,str(cwd),
                    C.cast(C.byref(startup),C.POINTER(StartupInfo)),C.byref(info)),"CreateProcessW")
            self.process,self.thread,self.pid=info.process,info.thread,info.pid
        finally:
            if attributes_ready:a.DeleteProcThreadAttributeList(attributes)
            for handle in handles:a.close(handle)

    def resume(self):
        require(not self.closed and not self.resumed,"job not resumable")
        require(self.api.ResumeThread(self.thread)==1,"primary suspension count differs")
        self.resumed=True

    def terminate(self, code=125):
        require(not self.closed,"job already closed")
        self.api.check(self.api.TerminateJobObject(self.job,code),"TerminateJobObject")

    def snapshot(self):
        require(not self.closed,"job already closed")
        data=Accounting();a=self.api
        a.check(a.QueryInformationJobObject(self.job,1,C.byref(data),C.sizeof(data),None),"QueryInformationJobObject")
        state=a.WaitForSingleObject(self.process,0)
        require(state in (0,258),"process wait unavailable")
        code=None
        if state==0:
            exit_code=D();a.check(a.GetExitCodeProcess(self.process,C.byref(exit_code)),"GetExitCodeProcess");code=exit_code.value
        return {"active_processes":data.active_processes,"total_processes":data.total_processes,
                "job_exit":code,"primary_signaled":state==0,"resumed":self.resumed,
                "managed_job_empty":data.active_processes==0 and state==0}

    def close(self):
        if self.closed:return
        self.closed=True
        for name in ("job","thread","process"):
            handle=getattr(self,name);setattr(self,name,None)
            self.api.close(handle)
