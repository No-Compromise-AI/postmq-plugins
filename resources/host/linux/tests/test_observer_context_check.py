"""CPU controls for retained-context timing, identity and failure cleanup."""

import contextlib
import io
import json
import unittest
from unittest import mock

import cuda_driver_probe as probe
import observer_context_check as check
import test_cuda_driver_probe as base

Fake, GPU = base.Fake, base.GPU


class RetainedControls(unittest.TestCase):
    def setUp(self):
        base.CUDAControls.setUp(self)
        (self.proc / "meminfo").write_text("MemFree: 4194304 kB\nMemAvailable: 4194304 kB\n")
        self.fake = Fake()
        self.sleeps = []

    def backend(self, *args, **kwargs):
        with mock.patch.object(probe.time, "sleep", side_effect=self.sleeps.append):
            return probe.sample(*args, loader=lambda _: self.fake, **kwargs)

    def observe(self, **kwargs):
        return check.observe(self.library, GPU, sample_fn=self.backend,
                             reader_factory=lambda p: probe.Driver(p, loader=lambda _: self.fake),
                             proc_root=self.proc, sleep_fn=self.sleeps.append, **kwargs)

    def test_six_queries_share_one_context_and_no_job_allocation(self):
        result = self.observe()
        self.assertEqual(len(result["observations"]), 6)
        self.assertEqual(self.sleeps, [0.2] * 5 + [1])
        self.assertEqual(self.fake.calls.count("cuDevicePrimaryCtxRetain"), 1)
        self.assertEqual(self.fake.calls.count("cuDevicePrimaryCtxRelease_v2"), 1)
        self.assertEqual(self.fake.calls.count("cuMemGetInfo_v2"), 8)  # before/during plus six observations
        self.assertNotIn("cuMemAlloc_v2", self.fake.calls)
        self.assertNotIn("cuMemHostAlloc", self.fake.calls)
        self.assertTrue(result["probe"]["owned_primary_context_released"])

    def test_no_callback_cannot_report_success(self):
        with self.assertRaisesRegex(probe.ProbeError, "retained_observation_incomplete") as raised:
            check.observe(self.library, GPU, sample_fn=lambda *a, **k: {"owned_primary_context_released": True})
        self.assertEqual(raised.exception.observations, [])
        self.assertTrue(raised.exception.probe["owned_primary_context_released"])

    def test_main_error_stream_preserves_partial_rows_and_code(self):
        def fail(*args, on_event, **kwargs):
            on_event({"phase": "held", "held_observed_at": 1.0})
            on_event({"phase": "observation", "index": 0})
            error = probe.ProbeError("cuMemGetInfo_v2", 999)
            error.observations = [{"index": 0}]
            error.probe = {"owned_primary_context_released": True}
            raise error
        out = io.StringIO()
        with mock.patch.object(check, "observe", side_effect=fail), \
                mock.patch.object(check.sys, "argv", ["check", "--library", str(self.library), "--gpu-uuid", GPU]), \
                mock.patch.object(check.signal, "signal"), mock.patch.object(check.signal, "pthread_sigmask"), \
                contextlib.redirect_stdout(out):
            self.assertEqual(check.main(), 1)
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["phase"] for r in rows], ["held", "observation", "error"])
        self.assertEqual(rows[-1]["code"], 999)
        self.assertIsNone(rows[-1]["errno"])
        self.assertEqual(rows[-1]["observations_completed"], 1)
        self.assertTrue(rows[-1]["probe"]["owned_primary_context_released"])

    def test_main_success_is_only_a_complete_diagnostic_record(self):
        out = io.StringIO()
        with mock.patch.object(check, "observe", return_value={"experiment": "CPU fixture only"}), \
                mock.patch.object(check.sys, "argv", ["check", "--library", str(self.library), "--gpu-uuid", GPU]), \
                mock.patch.object(check.signal, "signal"), mock.patch.object(check.signal, "pthread_sigmask"), \
                contextlib.redirect_stdout(out):
            self.assertEqual(check.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), {"phase": "complete", "experiment": "CPU fixture only"})

    def test_mid_window_failure_retains_rows_events_and_driver_code(self):
        events = []
        class Interrupted:
            count = 0
            def memory(self):
                self.count += 1
                if self.count == 3:
                    raise probe.ProbeError("cuMemGetInfo_v2", 999)
                return {"free_bytes": 4 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}
        def create_reader(_):
            self.assertEqual(events[0]["phase"], "held")
            return Interrupted()
        with self.assertRaises(probe.ProbeError) as raised:
            check.observe(self.library, GPU, sample_fn=self.backend,
                          reader_factory=create_reader, proc_root=self.proc,
                          sleep_fn=self.sleeps.append, on_event=events.append)
        self.assertEqual(raised.exception.code, 999)
        self.assertEqual(len(raised.exception.observations), 2)
        self.assertEqual(events[0]["phase"], "held")
        self.assertLessEqual(events[0]["held_observed_at"], events[1]["query_announced_at"])
        self.assertEqual(events[-1]["phase"], "query_intent")
        self.assertEqual(events[-1]["index"], 2)
        self.assertLessEqual(events[2]["query_start"], events[2]["query_end"])
        self.assertEqual(self.fake.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_query_failure_inside_callback_still_releases_context(self):
        class Broken:
            def memory(self): raise probe.ProbeError("query_fixture_failure")
        with self.assertRaisesRegex(probe.ProbeError, "query_fixture_failure"):
            check.observe(self.library, GPU, sample_fn=self.backend, reader_factory=lambda _: Broken(),
                          proc_root=self.proc, sleep_fn=self.sleeps.append)
        self.assertEqual(self.fake.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_missing_os_counter_does_not_become_zero(self):
        (self.proc / "meminfo").write_text("MemAvailable: 4194304 kB\n")
        with self.assertRaisesRegex(probe.ProbeError, "meminfo"):
            self.observe()
        self.assertEqual(self.fake.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_wrong_held_identity_never_opens_the_reader(self):
        def wrong(*args, **kwargs):
            callback = kwargs["on_hold"]
            kwargs["on_hold"] = lambda row: callback({**row, "gpu_uuid": "wrong"})
            return self.backend(*args, **kwargs)
        with self.assertRaisesRegex(probe.ProbeError, "held_observer_identity"):
            check.observe(self.library, GPU, sample_fn=wrong,
                          reader_factory=lambda _: self.fail("must not create a reader"),
                          proc_root=self.proc, sleep_fn=self.sleeps.append)
        self.assertEqual(self.fake.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])

    def test_bad_counter_unit_is_failure_and_still_releases_context(self):
        (self.proc / "meminfo").write_text("MemFree: 10 MB\nMemAvailable: 4194304 kB\n")
        with self.assertRaisesRegex(probe.ProbeError, "meminfo"):
            self.observe()
        self.assertEqual(self.fake.calls[-2:], ["cuCtxSetCurrent", "cuDevicePrimaryCtxRelease_v2"])


if __name__ == "__main__":
    unittest.main()
