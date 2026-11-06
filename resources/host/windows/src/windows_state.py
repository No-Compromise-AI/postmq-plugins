"""Per-token canonical local Windows state with owner/SYSTEM-only DACLs."""
import ctypes as C
import json
from pathlib import Path
import secrets
import time

from .windows_job import Api,D,H,BOOL,SecurityAttributes,require,INVALID_HANDLE


class AclSize(C.Structure):
    _fields_=[("count",D),("used",D),("free",D)]


class AceHeader(C.Structure):
    _fields_=[("kind",C.c_uint8),("flags",C.c_uint8),("size",C.c_uint16)]


class Overlapped(C.Structure):
    _fields_=[("internal",C.c_size_t),("internal_high",C.c_size_t),("offset",D),("offset_high",D),("event",H)]


class StateApi(Api):
    def __init__(self):
        super().__init__();self.advapi=C.WinDLL("advapi32",use_last_error=True);self.userenv=C.WinDLL("userenv",use_last_error=True)
        functions={
            self.dll:{"GetCurrentProcess":([],H),"LocalFree":([H],H),"GetFileAttributesW":([C.c_wchar_p],D),
                "GetDriveTypeW":([C.c_wchar_p],D),
                "GetVolumeInformationW":([C.c_wchar_p,C.c_wchar_p,D,C.POINTER(D),C.POINTER(D),C.POINTER(D),C.c_wchar_p,D],BOOL),
                "CreateDirectoryW":([C.c_wchar_p,C.POINTER(SecurityAttributes)],BOOL),
                "MoveFileExW":([C.c_wchar_p,C.c_wchar_p,D],BOOL),
                "WriteFile":([H,H,D,C.POINTER(D),H],BOOL),"FlushFileBuffers":([H],BOOL),
                "ReadFile":([H,H,D,C.POINTER(D),H],BOOL),"GetFileSizeEx":([H,C.POINTER(C.c_int64)],BOOL),
                "SetFilePointerEx":([H,C.c_int64,C.POINTER(C.c_int64),D],BOOL),
                "LockFileEx":([H,D,D,D,D,C.POINTER(Overlapped)],BOOL),
                "UnlockFileEx":([H,D,D,D,C.POINTER(Overlapped)],BOOL)},
            self.advapi:{"OpenProcessToken":([H,D,C.POINTER(H)],BOOL),
                "GetTokenInformation":([H,C.c_int32,H,D,C.POINTER(D)],BOOL),
                "ConvertSidToStringSidW":([H,C.POINTER(H)],BOOL),
                "ConvertStringSecurityDescriptorToSecurityDescriptorW":([C.c_wchar_p,D,C.POINTER(H),C.POINTER(D)],BOOL),
                "GetNamedSecurityInfoW":([C.c_wchar_p,C.c_int32,D,C.POINTER(H),H,C.POINTER(H),H,C.POINTER(H)],D),
                "GetAclInformation":([H,H,D,C.c_int32],BOOL),"GetAce":([H,D,C.POINTER(H)],BOOL)},
            self.userenv:{"GetUserProfileDirectoryW":([H,C.c_wchar_p,C.POINTER(D)],BOOL)}}
        for dll,rows in functions.items():
            for name,(args,result) in rows.items():
                fn=getattr(dll,name);fn.argtypes=args;fn.restype=result;setattr(self,name,fn)
        token=H();self.check(self.OpenProcessToken(self.GetCurrentProcess(),8,C.byref(token)),"OpenProcessToken")
        try:
            size=D();self.GetTokenInformation(token,1,None,0,C.byref(size));require(0<size.value<65536,"token size invalid")
            raw=C.create_string_buffer(size.value)
            self.check(self.GetTokenInformation(token,1,raw,size,C.byref(size)),"GetTokenInformation")
            self.sid=self.sid_string(C.cast(raw,C.POINTER(H))[0])
            owner_size=D();self.GetTokenInformation(token,4,None,0,C.byref(owner_size))
            require(0<owner_size.value<65536,"token owner size invalid")
            owner_raw=C.create_string_buffer(owner_size.value)
            self.check(self.GetTokenInformation(token,4,owner_raw,owner_size,C.byref(owner_size)),"GetTokenInformation owner")
            self.token_owner_sid=self.sid_string(C.cast(owner_raw,C.POINTER(H))[0])
            elevation=D();returned=D()
            self.check(self.GetTokenInformation(token,20,C.byref(elevation),C.sizeof(elevation),C.byref(returned)),"GetTokenInformation elevation")
            self.token_elevated=bool(elevation.value)
            self.state_eligible=not self.token_elevated and self.token_owner_sid==self.sid
            length=D();self.GetUserProfileDirectoryW(token,None,C.byref(length));require(0<length.value<32768,"profile size invalid")
            profile=C.create_unicode_buffer(length.value)
            self.check(self.GetUserProfileDirectoryW(token,profile,C.byref(length)),"GetUserProfileDirectoryW")
            self.profile=Path(profile.value)
        finally:self.close(token)

    def sid_string(self,pointer):
        text=H();self.check(self.ConvertSidToStringSidW(pointer,C.byref(text)),"ConvertSidToStringSidW")
        try:return C.wstring_at(text)
        finally:self.LocalFree(text)

    def no_reparse(self,path):
        for candidate in (Path(path),*Path(path).parents):
            flags=self.GetFileAttributesW(str(candidate))
            require(flags!=0xffffffff and not flags&0x400,"state path unavailable or reparse point")

    def private(self,path):
        self.no_reparse(path);owner,dacl,descriptor=H(),H(),H()
        code=self.GetNamedSecurityInfoW(str(path),1,5,C.byref(owner),None,C.byref(dacl),None,C.byref(descriptor))
        require(code==0 and dacl.value is not None,"state security unavailable")
        try:
            require(self.sid_string(owner)==self.sid,"state belongs to another owner")
            info=AclSize();self.check(self.GetAclInformation(dacl,C.byref(info),C.sizeof(info),2),"GetAclInformation")
            require(0<info.count<=64,"unbounded or empty DACL")
            own=False
            for index in range(info.count):
                ace=H();self.check(self.GetAce(dacl,index,C.byref(ace)),"GetAce")
                header=C.cast(ace,C.POINTER(AceHeader)).contents
                require(header.kind in (0,1) and header.size>=12,"unsupported ACE")
                if header.kind==1:continue # an explicit deny cannot broaden access
                sid=self.sid_string(ace.value+8)
                require(sid in (self.sid,"S-1-5-18"),"state grants another principal access")
                own=own or sid==self.sid
            require(own,"owner access absent")
        finally:self.LocalFree(descriptor)

    def directory(self,path, *, exclusive=False):
        require(self.state_eligible,"non-elevated standard-owner account required for native state")
        path=Path(path)
        if exclusive or not path.exists():
            self.no_reparse(path.parent)
            descriptor=H()
            sddl="O:"+self.sid+"D:P(A;OICI;FA;;;"+self.sid+")(A;OICI;FA;;;SY)"
            self.check(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,C.byref(descriptor),None),"private DACL")
            try:
                sa=SecurityAttributes(C.sizeof(SecurityAttributes),descriptor,False)
                if not self.CreateDirectoryW(str(path),C.byref(sa)):
                    if exclusive and C.get_last_error()==183:raise FileExistsError("private directory already exists")
                    require(C.get_last_error()==183,"private directory creation failed")
            finally:self.LocalFree(descriptor)
        require(path.is_dir(),"private directory is not a directory")
        self.private(path);return path

    def root(self):
        require(len(self.profile.drive)==2 and self.profile.drive[1]==":" and self.GetDriveTypeW(self.profile.anchor)==3,
                "fixed local Windows profile required")
        filesystem=C.create_unicode_buffer(64)
        self.check(self.GetVolumeInformationW(self.profile.anchor,None,0,None,None,None,filesystem,len(filesystem)),"GetVolumeInformationW")
        require(filesystem.value=="NTFS","first Windows profile requires NTFS")
        parent=self.profile/"AppData"/"Local";self.no_reparse(parent)
        return self.directory(parent/"PostMQ-native-v1")

    def open_write(self,path, *, create_new=False):
        self.private(Path(path).parent)
        if Path(path).exists():self.private(path)
        handle=self.CreateFileW(str(path),0x40000000,3,None,1 if create_new else 4,0x80200080,None)
        require(handle not in (None,0,INVALID_HANDLE),"private write unavailable")
        try:self.private(path)
        except BaseException:self.close(handle);raise
        return handle

    def write_bytes(self,handle,raw):
        offset=0
        while offset<len(raw):
            block=C.create_string_buffer(raw[offset:]);written=D()
            self.check(self.WriteFile(handle,block,len(raw)-offset,C.byref(written),None),"WriteFile")
            require(0<written.value<=len(raw)-offset,"short journal write made no progress");offset+=written.value
        self.check(self.FlushFileBuffers(handle),"FlushFileBuffers")

    def json_write(self,path,value, *, replace=False):
        raw=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()+b"\n"
        require(len(raw)<=65536,"native record too large")
        path=Path(path);temporary=path.with_name(".write-"+secrets.token_hex(16))
        handle=self.open_write(temporary,create_new=True)
        try:self.write_bytes(handle,raw)
        finally:self.close(handle)
        # No replacement unless explicitly requested. WRITE_THROUGH waits for
        # the move; original immutable identity files are never overwritten.
        end=time.monotonic()+0.5
        try:
            while not self.MoveFileExW(str(temporary),str(path),8|(1 if replace else 0)):
                error=C.get_last_error()
                if not replace and error in (80,183):raise FileExistsError(error,"native record already exists")
                if error not in (5,32) or time.monotonic()>=end:raise OSError(error,"MoveFileExW failed")
                time.sleep(0.01)
            self.private(path)
        finally:
            # Only the unique temporary file created above; no broad cleanup.
            try:temporary.unlink()
            except FileNotFoundError:pass

    def json_read(self,path):
        self.private(path)
        # Concurrent atomic status replacement requires SHARE_DELETE. Python's
        # ordinary open() omits it on Windows, so never use it for these records.
        handle=self.CreateFileW(str(path),0x80000000,7,None,3,0x00200080,None)
        require(handle not in (None,0,INVALID_HANDLE),"private read unavailable")
        try:
            size=C.c_int64();self.check(self.GetFileSizeEx(handle,C.byref(size)),"GetFileSizeEx")
            require(0<size.value<=65536,"native record too large")
            buffer=C.create_string_buffer(65537);count=D()
            self.check(self.ReadFile(handle,buffer,65537,C.byref(count),None),"ReadFile")
            raw=buffer.raw[:count.value];require(0<len(raw)<=65536,"native record too large")
        finally:self.close(handle)
        value=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(RuntimeError("nonfinite record")))
        require(isinstance(value,dict),"native object required");return value
