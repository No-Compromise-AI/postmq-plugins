"""Read-only OS counters. Never turn pressure percentages into capacity bytes."""
import ctypes as C
import os
import re
import subprocess
import sys
from .policy import integer, require


def mac_capacity(*, page_size, free_pages, inactive_pages, total_bytes, swapout_pages):
    """Mach free_count INCLUDES speculative_count; purgeable is not additive."""
    integer(page_size, 4096, 65536)
    require(page_size & (page_size-1) == 0, 'invalid kernel page size')
    total = integer(total_bytes, 1)
    free = integer(free_pages)*page_size
    available = free + integer(inactive_pages)*page_size
    require(free <= available <= total, 'inconsistent Mach capacity counts')
    return {'total_bytes': total, 'immediate_free_bytes': free,
            'available_advisory_bytes': available, 'swapout_bytes': integer(swapout_pages)*page_size,
            'accounting': 'Mach free plus inactive; inactive reclaim is advisory; speculative already included'}


class VmPrefix(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('free', 'active', 'inactive', 'wired')]
    _fields_ += [(n, C.c_uint64) for n in ('zero_fill', 'reactivations', 'pageins', 'pageouts',
        'faults', 'cow_faults', 'lookups', 'hits', 'purges')]
    _fields_ += [(n, C.c_uint32) for n in ('purgeable', 'speculative')]
    _fields_ += [(n, C.c_uint64) for n in ('decompressions', 'compressions', 'swapins', 'swapouts')]


class DarwinSample:
    def __init__(self, native):
        require(sys.platform == 'darwin', 'Mac sampler requires macOS')
        self.native = native
        self.lib = C.CDLL('/usr/lib/libSystem.B.dylib')
        self.lib.mach_host_self.argtypes = []; self.lib.mach_host_self.restype = C.c_uint32
        self.lib.host_page_size.argtypes = [C.c_uint32, C.POINTER(C.c_size_t)]
        self.lib.host_page_size.restype = C.c_int
        self.lib.host_statistics64.argtypes = [C.c_uint32, C.c_int, C.c_void_p, C.POINTER(C.c_uint32)]
        self.lib.host_statistics64.restype = C.c_int
        self.lib.mach_port_deallocate.argtypes = [C.c_uint32, C.c_uint32]
        self.lib.mach_port_deallocate.restype = C.c_int
        self.task = C.c_uint32.in_dll(self.lib, 'mach_task_self_').value

    def __call__(self):
        start = self.native.now()
        identity = self.native.host()
        host = self.lib.mach_host_self()
        require(host != 0, 'Mach host port unavailable')
        failure = None
        try:
            page = C.c_size_t()
            require(self.lib.host_page_size(host, C.byref(page)) == 0, 'kernel page size unavailable')
            # Larger output buffer tolerates appended fields. Only the checked
            # prefix is read; a truncated/changed prefix cannot authorize work.
            buffer = (C.c_uint32*512)(); count = C.c_uint32(512)
            require(self.lib.host_statistics64(host, 4, buffer, C.byref(count)) == 0 and
                    C.sizeof(VmPrefix)//4 <= count.value < 512, 'Mach VM prefix unavailable')
            vm = C.cast(buffer, C.POINTER(VmPrefix)).contents
            pressure = self.native.pressure()
            result = mac_capacity(page_size=page.value, free_pages=vm.free, inactive_pages=vm.inactive,
                total_bytes=pressure['total_bytes'], swapout_pages=vm.swapouts)
        except BaseException as error:
            failure=error
            raise
        finally:
            try:
                require(self.lib.mach_port_deallocate(self.task, host) == 0, 'Mach host port collection failed')
            except Exception:
                if failure is None:raise
                # Preserve the primary failure AND the collection failure.
                # Never return a capacity sample after either failure.
                failure.add_note('Mach host port collection also failed; capacity remains unavailable')
        require(self.native.host() == identity and 0 <= self.native.now()-start <= 4,
                'boot changed or observation deadline exceeded')
        return {**result, 'platform':'darwin', 'boot':identity, 'observed_continuous':start,
                'pressure_free_percent':pressure['free_percent'], 'commit_available_bytes':None, 'device':None}


class Performance(C.Structure):
    _fields_ = [('size', C.c_uint32)] + [(n, C.c_size_t) for n in ('commit_total', 'commit_limit',
        'commit_peak', 'physical_total', 'physical_available', 'system_cache', 'kernel_total',
        'kernel_paged', 'kernel_nonpaged', 'page_size')] + [(n, C.c_uint32) for n in
        ('handle_count', 'process_count', 'thread_count')]


def nvidia_sample(text, expected_uuid):
    lines = text.strip().splitlines()
    require(1 <= len(lines) <= 16, 'bounded NVIDIA device inventory required')
    rows = []
    for line in lines:
        values = [s.strip() for s in line.split(',')]
        require(len(values) == 3 and re.fullmatch(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', values[0]) and
                all(re.fullmatch(r'[0-9]{1,10}', x) for x in values[1:]), 'NVIDIA observation unavailable')
        uuid = values[0].lower().replace('gpu-', 'GPU-', 1)
        total, free = (int(x)*1024**2 for x in values[1:])
        require(0 <= free <= total and total > 0, 'invalid NVIDIA counters')
        rows.append({'uuid':uuid, 'total_bytes':total, 'free_bytes':free})
    require(len({r['uuid'] for r in rows}) == len(rows), 'duplicate device identity')
    matches = [r for r in rows if r['uuid'] == expected_uuid]
    require(len(matches) == 1, 'exact owner GPU absent')
    return matches[0]


class WindowsSample:
    def __init__(self, *, host, nvidia_path, device_uuid, verify_binary, cpu_fixture=False):
        require(sys.platform == 'win32' and C.sizeof(C.c_void_p) == 8, '64-bit Windows required')
        require(type(cpu_fixture) is bool, 'explicit CPU fixture flag required')
        if cpu_fixture:
            require(nvidia_path is None and device_uuid is None and verify_binary is None,
                    'CPU fixture cannot bind a GPU query')
        else:
            require(isinstance(nvidia_path,str) and isinstance(device_uuid,str) and callable(verify_binary),
                    'Windows GPU sampler needs its pinned observer')
        self.cpu_fixture=cpu_fixture
        self.host, self.path, self.uuid, self.verify_binary = host, nvidia_path, device_uuid, verify_binary
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.k.GetTickCount64.argtypes = []; self.k.GetTickCount64.restype = C.c_uint64
        self.p = C.WinDLL('psapi', use_last_error=True)
        self.p.GetPerformanceInfo.argtypes = [C.POINTER(Performance), C.c_uint32]
        self.p.GetPerformanceInfo.restype = C.c_int

    def now(self): return self.k.GetTickCount64()/1000

    def __call__(self):
        start = self.now(); identity = self.host()
        if not self.cpu_fixture:self.verify_binary(self.path)
        value = Performance(); value.size = C.sizeof(value)
        require(self.p.GetPerformanceInfo(C.byref(value), value.size) != 0, 'host counters unavailable')
        require(0 < value.page_size <= 65536 and 0 < value.physical_total and
                value.physical_available <= value.physical_total and value.commit_total <= value.commit_limit,
                'invalid host capacity counters')
        device=None
        if not self.cpu_fixture:
            # Fixed installed binary/arguments, no caller command language. The
            # child is collected even on timeout; only read-only GPU queries occur.
            answer = subprocess.run([self.path, '--query-gpu=uuid,memory.total,memory.free',
                '--format=csv,noheader,nounits'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=2, check=True, creationflags=0x08000000,
                env={k:v for k,v in os.environ.items() if not k.upper().startswith('POSTMQ_')})
            require(len(answer.stdout) <= 8192, 'NVIDIA output exceeds inventory bound')
            device = nvidia_sample(answer.stdout.decode('ascii'), self.uuid)
        require(self.host() == identity and 0 <= self.now()-start <= 4,
                'boot changed or observation deadline exceeded')
        available = value.physical_available*value.page_size
        return {'platform':'win32', 'boot':identity, 'observed_continuous':start,
            'total_bytes':value.physical_total*value.page_size,
            'available_advisory_bytes':available, 'immediate_free_bytes':available,
            'commit_available_bytes':(value.commit_limit-value.commit_total)*value.page_size,
            'pressure_free_percent':None, 'swapout_bytes':None, 'device':device,
            'accounting':'GetPerformanceInfo physical/commit; no GPU pool' if self.cpu_fixture else
                         'GetPerformanceInfo physical/commit; NVIDIA VRAM sampled separately'}
