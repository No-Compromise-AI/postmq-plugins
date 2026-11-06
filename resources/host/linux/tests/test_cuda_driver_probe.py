"""CPU controls for native CUDA ABI ordering and conservative failure paths."""

import ctypes as C
import contextlib
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import shutil
import subprocess
import types
from unittest import mock

import cuda_driver_probe as probe
from native_cuda_checks import positive_control


GPU = "GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000007"


class Function:
    def __init__(self, fake, name):
        self.fake, self.name = fake, name

    def __call__(self, *args):
        self.fake.calls.append(self.name)
        if self.name in self.fake.failures:
            return self.fake.failures[self.name]
        if self.name == "cuDeviceGetCount": args[0]._obj.value = self.fake.count
        elif self.name == "cuDeviceGet": args[0]._obj.value = 0
        elif self.name == "cuDriverGetVersion": args[0]._obj.value = 13030
        elif self.name == "cuDeviceGetUuid_v2":
            args[0]._obj.bytes[:] = uuid.UUID(self.fake.gpu[4:]).bytes
        elif self.name == "cuDeviceGetName": args[0].value = b"NVIDIA GB10"
        elif self.name == "cuDeviceGetAttribute": args[0]._obj.value = 1
        elif self.name == "cuDevicePrimaryCtxRetain": args[0]._obj.value = 17
        elif self.name == "cuMemGetInfo_v2":
            args[0]._obj.value = 4 * 1024 ** 3
            args[1]._obj.value = 8 * 1024 ** 3
        elif self.name == "cuMemAlloc_v2": args[0]._obj.value = 1234
        elif self.name == "cuMemHostAlloc":
            self.fake.host = C.create_string_buffer(args[1])
            args[0]._obj.value = C.addressof(self.fake.host)
        return 0


class Fake:
    def __init__(self, *, count=1, gpu=GPU, failures=None):
        self.count, self.gpu, self.failures = count, gpu, failures or {}
        self.calls, self.functions = [], {}

    def __getattr__(self, name):
        if name not in self.functions:
            self.functions[name] = Function(self, name)
        return self.functions[name]


class CUDAControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = Path(self.tmp.name) / "driver.so"
        self.library.write_bytes(b"CPU fixture, not a CUDA library")
        self.proc = Path(self.tmp.name) / "proc"
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text("aaaaaaaa-aaaa-4aaa-8aaa-00000000000b\n")
        (self.proc / "meminfo").write_text("MemAvailable: 4194304 kB\n")

    def run_probe(self, fake, **kwargs):
        return probe.sample(self.library, GPU, loader=lambda _: fake, proc_root=self.proc, **kwargs)

    def test_default_query_allocates_no_job_memory(self):
        f = Fake(); answer = self.run_probe(f)
        self.assertEqual(answer["gpu_uuid"], GPU)
        self.assertEqual(answer["free_bytes"], 4 * 1024 ** 3)
        self.assertNotIn("cuMemAlloc_v2", f.calls)
        self.assertNotIn("cuMemHostAlloc", f.calls)
        self.assertIs(answer["allocations_freed"], False)
        self.assertEqual(f.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_allocations_touch_then_free_both_before_context_release(self):
        f = Fake(); answer = self.run_probe(f, allocation_bytes=1024)
        self.assertEqual(answer["allocation_bytes_each"], 1024)
        self.assertEqual(f.host.raw[0], 0x5A)
        self.assertIn("cuCtxSynchronize", f.calls)
        self.assertEqual(f.calls[-4:], ["cuMemFreeHost", "cuMemFree_v2", "cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_wrong_identity_never_creates_context(self):
        f = Fake(gpu="GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000001")
        with self.assertRaises(probe.ProbeError): self.run_probe(f)
        self.assertNotIn("cuDevicePrimaryCtxRetain", f.calls)

    def test_ambiguous_device_inventory_never_creates_context(self):
        for count in (0, 2):
            f = Fake(count=count)
            with self.assertRaises(probe.ProbeError): self.run_probe(f)
            self.assertNotIn("cuDevicePrimaryCtxRetain", f.calls)

    def test_invalid_bounds_never_load_library(self):
        for value in (-1, probe.MAX_ALLOCATION + 1, True, 1.5):
            with self.assertRaises(probe.ProbeError):
                probe.sample(self.library, GPU, allocation_bytes=value,
                             loader=lambda _: self.fail("must not load"))

    def test_invalid_uuid_never_loads_library(self):
        for value in ("0", "GPU-../bad", GPU + "\n", GPU.upper()):
            with self.assertRaises(probe.ProbeError):
                probe.sample(self.library, value, loader=lambda _: self.fail("must not load"))

    def test_unbounded_hold_never_loads_library(self):
        for value in (-1, 6, True, 1.5):
            with self.assertRaises(probe.ProbeError):
                probe.sample(self.library, GPU, hold_seconds=value,
                             loader=lambda _: self.fail("must not load"))

    def test_hold_callback_failure_still_cleans_every_owned_allocation(self):
        f = Fake()
        def fail(_): raise RuntimeError("diagnostic reader disappeared")
        with self.assertRaises(RuntimeError):
            self.run_probe(f, allocation_bytes=1024, hold_seconds=1, on_hold=fail)
        self.assertEqual(f.calls[-4:], ["cuMemFreeHost", "cuMemFree_v2", "cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_held_payload_binds_the_loaded_driver_before_cleanup(self):
        held = []
        with mock.patch.object(probe.time, "sleep"):
            self.run_probe(Fake(), allocation_bytes=1024, hold_seconds=1, on_hold=held.append)
        self.assertEqual(held[0]["library"], str(self.library.resolve()))
        self.assertEqual(held[0]["library_sha256"], hashlib.sha256(self.library.read_bytes()).hexdigest())

    def test_relative_library_is_refused(self):
        with self.assertRaises(probe.ProbeError): probe.sample("driver.so", GPU)

    def test_query_failure_still_releases_context(self):
        f = Fake(failures={"cuMemGetInfo_v2": 999})
        with self.assertRaises(probe.ProbeError): self.run_probe(f)
        self.assertEqual(f.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_missing_available_memory_has_a_stage_and_no_allocation(self):
        (self.proc / "meminfo").write_text("MemTotal: 8388608 kB\n")
        f = Fake()
        with self.assertRaisesRegex(probe.ProbeError, "meminfo"):
            self.run_probe(f, allocation_bytes=1024)
        self.assertNotIn("cuMemAlloc_v2", f.calls)

    def test_primary_and_cleanup_failures_are_both_retained(self):
        f = Fake(failures={"cuCtxSynchronize": 999, "cuMemFree_v2": 888})
        with self.assertRaises(probe.ProbeError) as raised:
            self.run_probe(f, allocation_bytes=1024)
        self.assertEqual(raised.exception.primary["stage"], "cuCtxSynchronize")
        self.assertEqual(raised.exception.primary["code"], 999)
        self.assertEqual(raised.exception.cleanup, [{"stage": "cuMemFree_v2", "code": 888}])

    def test_host_allocation_failure_still_frees_device(self):
        f = Fake(failures={"cuMemHostAlloc": 2})
        with self.assertRaises(probe.ProbeError): self.run_probe(f, allocation_bytes=1024)
        self.assertIn("cuMemFree_v2", f.calls)
        self.assertNotIn("cuMemFreeHost", f.calls)

    def test_cleanup_failure_is_not_success_and_does_not_skip_remaining_cleanup(self):
        for operation in ("cuMemFreeHost", "cuMemFree_v2", "cuDevicePrimaryCtxRelease_v2"):
            f = Fake(failures={operation: 999})
            with self.assertRaisesRegex(probe.ProbeError, "owned_context_cleanup_failed"):
                self.run_probe(f, allocation_bytes=1024)
            self.assertEqual(f.calls[-4:], ["cuMemFreeHost", "cuMemFree_v2", "cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])


class CUDAABIControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        cls.compiled = Path(cls.build.name) / "cuda-test.so"
        compiler = shutil.which("cc")
        if compiler is None:
            raise RuntimeError("C compiler required for ABI controls")
        subprocess.run([compiler, "-shared", "-fPIC", str(Path(__file__).with_name("cuda_abi_fixture.c")),
                        "-o", str(cls.compiled)], capture_output=True, check=True, timeout=30)

    def setUp(self):
        CUDAControls.setUp(self)
        self.lib = C.CDLL(str(self.compiled))
        self.lib.fixture_reset()

    def test_real_ctypes_conversion_and_wide_handles(self):
        answer = probe.sample(self.compiled, GPU, allocation_bytes=1024, proc_root=self.proc)
        self.assertEqual(answer["total_bytes"], 8 * 1024 ** 3)
        self.assertTrue(answer["allocations_freed"])
        self.assertEqual(self.lib.fixture_cleanup_count(), 3)

    def test_control_rejects_narrowed_device_pointer_abi(self):
        original = probe.Driver
        class Narrowed(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.lib.cuMemsetD8_v2.argtypes = [C.c_uint32, C.c_ubyte, C.c_size_t]
            def call(self, name, *args):
                if name == "cuMemsetD8_v2":
                    args = (args[0].value, *args[1:])  # Exercise truncation in C, not Python rejection.
                return super().call(name, *args)
        with mock.patch.object(probe, "Driver", Narrowed):
            with self.assertRaises(probe.ProbeError) as raised:
                probe.sample(self.compiled, GPU, allocation_bytes=1024, proc_root=self.proc)
        self.assertEqual(raised.exception.stage, "cuMemsetD8_v2")
        self.assertEqual(raised.exception.code, 999)


class ObservationControls(unittest.TestCase):
    def test_invisible_or_half_visible_allocations_cannot_qualify_return(self):
        before = {"free_bytes": 4 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}
        for delta in (0, 64 * 1024 ** 2):
            with self.assertRaisesRegex(RuntimeError, "inconclusive"):
                positive_control(before, {**before, "free_bytes": before["free_bytes"] - delta})

    def test_measured_drop_at_inclusive_bounds_passes_positive_control(self):
        before = {"free_bytes": 4 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}
        for mib in (96, 128, 160):
            positive_control(before, {**before, "free_bytes": before["free_bytes"] - mib * 1024 ** 2})

    def test_excessive_drop_is_contamination_not_a_pass(self):
        before = {"free_bytes": 4 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}
        with self.assertRaisesRegex(RuntimeError, "contaminated"):
            positive_control(before, {**before, "free_bytes": before["free_bytes"] - 161 * 1024 ** 2})




if __name__ == "__main__":
    unittest.main()
