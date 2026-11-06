"""Bounded x64 kernel-process epoch observation, without PID control.

This is a private NT layout, not a claimed stable public ABI. Each observation
cross-checks its process-time slot against GetProcessTimes for the caller,
validates the System record and requires two matching kernel samples. A layout
change refuses observation; there is no boot-entry GUID or wall-clock fallback.
Migration of existing history and actual sleep/restart acceptance are separate.

Primary references:
https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntquerysysteminformation
https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tsts/96702c7d-6f5f-4965-8f29-5fdbea73278e
"""
import ctypes as C
import os
import re
import struct
import sys
import uuid

SCHEME='postmq.windows-kernel-process-epoch-v2'
MAX_BYTES=16*1024*1024


def require(ok,message):
    if not ok:raise ValueError(message)


def identifier(machine,birth):
    require(isinstance(machine,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',machine)
            and uuid.UUID(machine).int!=0,'canonical installation MachineGuid required')
    require(isinstance(birth,str) and re.fullmatch(r'[1-9][0-9]{0,19}',birth)
            and int(birth)<=2**64-1,'exact decimal kernel process creation time required')
    return str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_OID,SCHEME),machine+'|'+birth))


def parse_kernel_birth(raw,base,pid,birth):
    require(isinstance(raw,bytes) and 104<=len(raw)<=MAX_BYTES and type(base) is int and base>0
            and type(pid) is int and pid>4 and type(birth) is int and birth>0,
            'bounded native process data and caller birth required')
    offset=0;own=[];kernel=[];count=0
    while True:
        require(offset+104<=len(raw),'truncated native process header')
        stride,threads=struct.unpack_from('<II',raw,offset)
        end=offset+stride if stride else len(raw)
        require(offset+104<=end<=len(raw) and (stride==0 or stride%8==0),'native process stride differs')
        observed=struct.unpack_from('<Q',raw,offset+80)[0]
        created=struct.unpack_from('<Q',raw,offset+32)[0]
        if observed==pid:own.append(created)
        if observed==4:
            parent=struct.unpack_from('<Q',raw,offset+88)[0]
            size,maximum=struct.unpack_from('<HH',raw,offset+56)
            pointer=struct.unpack_from('<Q',raw,offset+64)[0];start=pointer-base
            require(parent==0 and threads>0 and size==12 and maximum>=size and
                    offset+104<=start and start+size<=end,'kernel System record differs')
            require(raw[start:start+size].decode('utf-16-le')=='System','kernel System image differs')
            kernel.append(created)
        count+=1;require(count<=100000,'native process count exceeds bound')
        if stride==0:break
        offset=end
    require(own==[birth] and len(kernel)==1 and 0<kernel[0]<birth,
            'process-time layout or current caller cross-check differs')
    return kernel[0]


def birth():
    require(sys.platform=='win32' and C.sizeof(C.c_void_p)==8,'Windows x64 kernel epoch required')
    class FileTime(C.Structure):_fields_=[('low',C.c_uint32),('high',C.c_uint32)]
    kernel=C.WinDLL('kernel32',use_last_error=True);nt=C.WinDLL('ntdll')
    current=kernel.GetCurrentProcess;current.argtypes=[];current.restype=C.c_void_p
    times=kernel.GetProcessTimes
    times.argtypes=[C.c_void_p]+[C.POINTER(FileTime)]*4;times.restype=C.c_int32
    values=[FileTime() for _ in range(4)]
    require(bool(times(current(),*[C.byref(v) for v in values])),'caller process birth unavailable')
    own_birth=(values[0].high<<32)|values[0].low
    query=nt.NtQuerySystemInformation
    query.argtypes=[C.c_uint32,C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32)];query.restype=C.c_int32
    samples=[]
    for _ in range(2):
        size=1024*1024
        for _ in range(4):
            buffer=C.create_string_buffer(size);used=C.c_uint32()
            status=query(5,buffer,size,C.byref(used))
            if status==0:break
            require(status&0xffffffff==0xc0000004 and used.value<=MAX_BYTES,'native process query unavailable')
            size=max(size*2,used.value+65536);require(size<=MAX_BYTES,'native buffer growth exceeds bound')
        else:raise ValueError('native process data remained unstable')
        require(104<=used.value<=size,'native process length differs')
        samples.append(parse_kernel_birth(buffer.raw[:used.value],C.addressof(buffer),os.getpid(),own_birth))
    require(samples[0]==samples[1],'kernel process birth changed during observation')
    return str(samples[0])


def snapshot(machine):
    value=birth()
    return {'scheme':SCHEME,'machine_guid':machine,'kernel_system_creation_windows_100ns':value,
            'boot_identifier':identifier(machine,value)}
