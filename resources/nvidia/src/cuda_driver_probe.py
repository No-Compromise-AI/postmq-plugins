"""Finite CUDA observer/qualification child; no model, network or global reset.

Run in a fresh process. Explicit device/pinned allocations are diagnostic only,
bounded to 64 MiB each; the default merely samples through its own context.
This does not enable unified mode in the provider factory.
"""

import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid


MAX_ALLOCATION = 64 * 1024 ** 2


class ProbeError(RuntimeError):
    def __init__(self, stage, code=None, *, cleanup=None, primary=None):
        super().__init__(stage)
        self.stage, self.code = stage, code
        self.cleanup, self.primary = cleanup, primary


class CUuuid(C.Structure):
    _fields_ = [("bytes", C.c_ubyte * 16)]


class Driver:
    def __init__(self, library, loader=C.CDLL):
        self.lib = loader(str(library))
        specs = {
            "cuInit": [C.c_uint],
            "cuDriverGetVersion": [C.POINTER(C.c_int)],
            "cuDeviceGetCount": [C.POINTER(C.c_int)],
            "cuDeviceGet": [C.POINTER(C.c_int), C.c_int],
            "cuDeviceGetUuid_v2": [C.POINTER(CUuuid), C.c_int],
            "cuDeviceGetName": [C.c_char_p, C.c_int, C.c_int],
            "cuDeviceGetAttribute": [C.POINTER(C.c_int), C.c_int, C.c_int],
            "cuDevicePrimaryCtxRetain": [C.POINTER(C.c_void_p), C.c_int],
            "cuCtxSetCurrent": [C.c_void_p],
            "cuCtxSynchronize": [],
            "cuDevicePrimaryCtxRelease_v2": [C.c_int],
            "cuMemGetInfo_v2": [C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)],
            "cuMemAlloc_v2": [C.POINTER(C.c_uint64), C.c_size_t],
            "cuMemsetD8_v2": [C.c_uint64, C.c_ubyte, C.c_size_t],
            "cuMemFree_v2": [C.c_uint64],
            "cuMemHostAlloc": [C.POINTER(C.c_void_p), C.c_size_t, C.c_uint],
            "cuMemFreeHost": [C.c_void_p],
        }
        for name, args in specs.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, C.c_int

    def call(self, name, *args):
        code = getattr(self.lib, name)(*args)
        if code:
            raise ProbeError(name, int(code))

    def memory(self):
        free, total = C.c_size_t(), C.c_size_t()
        self.call("cuMemGetInfo_v2", C.byref(free), C.byref(total))
        if not 0 <= free.value <= total.value or total.value == 0:
            raise ProbeError("invalid_memory_counters")
        return {"free_bytes": free.value, "total_bytes": total.value}


def sample(library, expected_uuid, *, allocation_bytes=0, hold_seconds=0, on_hold=None,
           loader=C.CDLL, proc_root=Path("/proc")):
    if (type(allocation_bytes) is not int or not 0 <= allocation_bytes <= MAX_ALLOCATION):
        raise ProbeError("allocation_bound")
    if type(hold_seconds) is not int or not 0 <= hold_seconds <= 5:
        raise ProbeError("hold_bound")
    if not isinstance(expected_uuid, str) or not expected_uuid.startswith("GPU-"):
        raise ProbeError("gpu_identity")
    try:
        if "GPU-" + str(uuid.UUID(expected_uuid[4:])) != expected_uuid:
            raise ValueError
    except ValueError:
        raise ProbeError("gpu_identity") from None
    library = Path(library)
    if not library.is_absolute():
        raise ProbeError("absolute_driver_library_required")
    library = library.resolve(strict=True)
    proc_root = Path(proc_root)
    boot_id = (proc_root / "sys/kernel/random/boot_id").read_text().strip()
    library_sha = hashlib.sha256(library.read_bytes()).hexdigest()
    driver = Driver(library, loader=loader)
    driver.call("cuInit", 0)
    count, device, version = C.c_int(), C.c_int(), C.c_int()
    driver.call("cuDeviceGetCount", C.byref(count))
    if count.value != 1:
        raise ProbeError("one_device_required")
    driver.call("cuDeviceGet", C.byref(device), 0)
    identity = CUuuid()
    driver.call("cuDeviceGetUuid_v2", C.byref(identity), device.value)
    if "GPU-" + str(uuid.UUID(bytes=bytes(identity.bytes))) != expected_uuid:
        raise ProbeError("gpu_identity_mismatch")
    driver.call("cuDriverGetVersion", C.byref(version))
    name = C.create_string_buffer(256)
    driver.call("cuDeviceGetName", name, len(name), device.value)
    integrated = C.c_int()
    driver.call("cuDeviceGetAttribute", C.byref(integrated), 18, device.value)
    context, device_ptr, host_ptr = C.c_void_p(), C.c_uint64(), C.c_void_p()
    retained = False
    cleanup_errors = []
    primary_error = None
    try:
        driver.call("cuDevicePrimaryCtxRetain", C.byref(context), device.value)
        retained = True
        driver.call("cuCtxSetCurrent", context)
        before = driver.memory()
        if allocation_bytes:
            try:
                row = next(x.split() for x in (proc_root / "meminfo").read_text().splitlines()
                           if x.startswith("MemAvailable:"))
                if row[2] != "kB": raise ValueError
                available = int(row[1]) * 1024
            except (StopIteration, ValueError, IndexError):
                raise ProbeError("meminfo") from None
            if min(available, before["free_bytes"]) < 2 * 1024 ** 3:
                raise ProbeError("diagnostic_headroom")
            driver.call("cuMemAlloc_v2", C.byref(device_ptr), allocation_bytes)
            driver.call("cuMemsetD8_v2", device_ptr, 0xA5, allocation_bytes)
            driver.call("cuMemHostAlloc", C.byref(host_ptr), allocation_bytes, 0)
            C.memset(host_ptr, 0x5A, allocation_bytes)
            driver.call("cuCtxSynchronize")
        during = driver.memory()
        if during["total_bytes"] != before["total_bytes"]:
            raise ProbeError("device_total_changed")
        if hold_seconds:
            if on_hold is not None:
                on_hold({"phase": "held", "pid": os.getpid(), "gpu_uuid": expected_uuid,
                         "allocation_bytes_each": allocation_bytes, "before": before,
                         "during": during, "boot_id": boot_id,
                         "library": str(library), "library_sha256": library_sha})
            time.sleep(hold_seconds)
    except BaseException as error:
        primary_error = {"error_type": type(error).__name__, "stage": getattr(error, "stage", None),
                         "code": getattr(error, "code", None)}
        raise
    finally:
        cleanup = []
        if host_ptr.value:
            cleanup.append(("cuMemFreeHost", host_ptr))
        if device_ptr.value:
            cleanup.append(("cuMemFree_v2", device_ptr))
        if retained:
            cleanup.extend([("cuCtxSetCurrent", None), ("cuDevicePrimaryCtxRelease_v2", device.value)])
        for operation, argument in cleanup:
            try:
                driver.call(operation, argument)
            except ProbeError as error:
                cleanup_errors.append({"stage": error.stage, "code": error.code})
        if cleanup_errors:
            raise ProbeError("owned_context_cleanup_failed", cleanup=cleanup_errors, primary=primary_error)
    if (proc_root / "sys/kernel/random/boot_id").read_text().strip() != boot_id:
        raise ProbeError("boot_identity_changed")
    return {**before, "during": during, "gpu_uuid": expected_uuid,
            "device_name": name.value.decode("ascii", errors="strict"),
            "integrated_attribute": integrated.value, "driver_api_version": version.value,
            "measurement": "cuMemGetInfo_v2-os-free", "allocation_bytes_each": allocation_bytes,
            "allocations_freed": allocation_bytes > 0, "owned_primary_context_released": True,
            "library": str(library), "library_sha256": library_sha,
            "observed_at_unix": time.time(), "boot_id": boot_id}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--allocation-bytes", type=int, default=0)
    parser.add_argument("--hold-seconds", type=int, default=0)
    args = parser.parse_args()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    try:
        answer = sample(args.library, args.gpu_uuid, allocation_bytes=args.allocation_bytes,
                        hold_seconds=args.hold_seconds,
                        on_hold=lambda value: print(json.dumps(value), flush=True))
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "stage": getattr(error, "stage", "probe_failed"),
                          "code": getattr(error, "code", None), "cleanup": getattr(error, "cleanup", None),
                          "primary": getattr(error, "primary", None)}), flush=True)
        return 1
    print(json.dumps(answer), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
