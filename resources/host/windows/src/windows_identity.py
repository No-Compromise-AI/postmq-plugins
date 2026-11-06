"""Read-only Windows installation, boot, token and process observations."""
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import re
from . import windows_kernel

from .windows_job import D,Q,H,BOOL,require,FileTime

CORE=("__init__.py","entry.py","windows_job.py","windows_state.py","windows_identity.py",
      "windows_kernel.py","windows_provider.py","windows_guard.py")


def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def sources():
    root=Path(__file__).resolve().parent
    return digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in CORE})


def kernel_observation():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\Microsoft\Cryptography",0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
        machine=winreg.QueryValueEx(key,"MachineGuid")[0].lower()
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",machine),"Windows host identity unavailable")
    return windows_kernel.snapshot(machine)


def boot_identifier():return kernel_observation()['boot_identifier']


def kernel_source_sha256():return hashlib.sha256(Path(windows_kernel.__file__).read_bytes()).hexdigest()


def host(api):
    observed=kernel_observation()
    return {"machine_guid":observed['machine_guid'],"boot_identifier":observed['boot_identifier'],"owner_sid":api.sid}


def process(api,pid):
    require(type(pid) is int and 1<=pid<=2**32-1,"invalid Windows PID")
    fn=api.dll.OpenProcess;fn.argtypes=[D,BOOL,D];fn.restype=H
    handle=fn(0x101000,False,pid)
    if not handle:
        if C.get_last_error() in (87,1168):return None
        raise OSError(C.get_last_error(),"process observation unavailable")
    try:
        state=api.WaitForSingleObject(handle,0);require(state in (0,258),"process wait unavailable")
        return {"pid":pid,"birth":api.birth(handle),"exited":state==0}
    finally:api.close(handle)


class MemoryStatus(C.Structure):
    _fields_=[("size",D),("load",D)]+[(name,Q) for name in
       ("total_physical","available_physical","total_pagefile","available_pagefile","total_virtual","available_virtual","extended_virtual")]


def memory(api):
    fn=api.dll.GlobalMemoryStatusEx;fn.argtypes=[C.POINTER(MemoryStatus)];fn.restype=BOOL
    value=MemoryStatus();value.size=C.sizeof(value);api.check(fn(C.byref(value)),"GlobalMemoryStatusEx")
    require(0<value.total_physical and value.available_physical<=value.total_physical,"memory observation invalid")
    return {"total_physical":value.total_physical,"available_physical":value.available_physical,
            "measurement":"Windows physical RAM advisory; not VRAM or combined reservation"}
