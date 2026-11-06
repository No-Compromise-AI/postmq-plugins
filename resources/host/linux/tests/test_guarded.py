"""CPU simulation of kernel leaves plus real provider-owned status pipes."""

import json
from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from guarded_cgroup import GuardedCgroupProvider, ProviderError, StatusChannel
from test_provider import BASELINE, BOOT, CLAIM, FakeChild, FakeProbe, sample


class GuardFixture:
    def __init__(self, case):
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name).resolve()
        self.root, self.proc = self.dir / "cgroup", self.dir / "proc"
        self.root.mkdir()
        (self.root / "cgroup.controllers").write_text("memory")
        boot = self.proc / "sys/kernel/random/boot_id"
        boot.parent.mkdir(parents=True); boot.write_text(BOOT)
        self.path = self.root / "user.slice/user-1000.slice/user@1000.service/app.slice" / GuardedCgroupProvider.epoch_unit(CLAIM, 7)
        self.probe, self.child, self.time = FakeProbe(), FakeChild(), 0.0
        self.actions, self.launches, self.writers = [], [], []
        self.publish_readers = []
        self.provider = GuardedCgroupProvider(BASELINE, required_bytes=1, reserve_bytes=1,
                                             drain_seconds=0.1, sample_interval=0.01, stable_samples=2,
                                             charge_tolerance=10)
        self.bind(self.provider)
        case.addCleanup(self.close)

    def close(self):
        for writer in self.writers:
            writer.close()
        for reader in self.publish_readers:
            reader.close()
        for channel in list(self.provider.channels.values()):
            channel.close()

    def bind(self, provider):
        provider.root, provider.proc, provider.uid = self.root, self.proc, 1000
        provider.runner, provider.probe, provider.popen = self.run, self.probe, self.launch
        provider.clock, provider.sleep = lambda: self.time, self.sleep
        provider._remove_job = self.remove_job
        kill = provider._kill_fd
        def recorded(directory):
            target = "guard" if os.fstat(directory).st_ino == (self.path / "guard").stat().st_ino else "job"
            self.actions.append("kill_" + target)
            kill(directory)
        provider._kill_fd = recorded

    def sleep(self, seconds):
        self.time += seconds

    def run(self, argv):
        if argv[:3] == ["systemctl", "--user", "list-units"]:
            return (self.path.name + " loaded active running PostMQ-GPU:" + BASELINE.gpu_uuid + "\n"
                    if self.path.exists() else "")
        if "--property=Version" in argv:
            return "Version=259\n"
        unit = argv[3]
        path = self.path.with_name(unit)
        if not path.exists():
            return "LoadState=not-found\nControlGroup=\n"
        return ("LoadState=loaded\nControlGroup=/" + str(path.relative_to(self.root)) +
                "\nDescription=PostMQ-GPU:" + BASELINE.gpu_uuid + "\n")

    def launch(self, argv, **kwargs):
        self.launches.append((argv, kwargs))
        status = int(argv[argv.index("--claim") - 1])
        self.writer = os.fdopen(os.dup(status), "wb", buffering=0)
        self.writers.append(self.writer)
        self.publish_reader = os.fdopen(os.dup(int(argv[argv.index("--publish-fd") + 1])), "rb", buffering=0)
        self.publish_readers.append(self.publish_reader)
        self.path.mkdir(parents=True)
        (self.path / "cgroup.procs").write_text("")
        (self.path / "cgroup.kill").write_text("\n")
        # The guard's large accounting charge must never be the job's charge.
        (self.path / "memory.current").write_text("999999999")
        self.leaf("guard", "321", 1, 999999999)
        self.leaf("job", "654", 1, 0)
        return self.child

    def leaf(self, name, pids="", populated=0, current=0, oom=0):
        path = self.path / name; path.mkdir(exist_ok=True)
        for n, value in {"cgroup.procs": pids, "cgroup.events": "populated " + str(populated),
                         "memory.current": str(current), "memory.peak": "999", "cgroup.kill": "\n",
                         "memory.events": "oom 0\noom_kill " + str(oom), "memory.max": "max"}.items():
            (path / n).write_text(value)

    def start(self):
        reader, writer = os.pipe()
        try:
            handle = self.provider.container(CLAIM, 7, ["/bin/true"], {}, reader)
            os.fstat(reader)  # Provider must not close the SDK descriptor.
            return handle
        finally:
            os.close(reader); os.close(writer)

    def ended(self, code=7, **updates):
        self.leaf("job", **updates)
        self.writer.write(("exit " + str(code) + "\n").encode()); self.writer.close()

    def remove_job(self, _scope):
        self.actions.append("remove_job")
        shutil.rmtree(self.path / "job")  # Fake kernel removes its virtual controls.


class GuardedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GuardFixture(self)
        self.provider = self.fixture.provider

    def test_launch_binds_all_inodes_and_uses_provider_status_pipe(self):
        f = self.fixture; handle = f.start(); ref = handle["ref"]
        json.dumps(handle)
        self.assertLessEqual(len(ref), 200)
        self.assertEqual((CLAIM, 7, BOOT), self.provider.parts(ref)[:3])
        self.assertEqual(tuple(p.stat().st_ino for p in (f.path, f.path / "guard", f.path / "job")),
                         self.provider.parts(ref)[3:])
        argv, kwargs = f.launches[0]
        self.assertIn("--property=Delegate=yes", argv)
        self.assertTrue(any(x.endswith("guard.py") for x in argv))
        self.assertEqual(3, len(kwargs["pass_fds"]))
        self.assertEqual(b"p", f.publish_reader.read(1))
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual("alive", self.provider.inspect(handle)["state"])
        self.assertIsNone(self.provider.inspect(handle)["job_exit"])

    def test_primary_status_is_independent_of_guard_liveness(self):
        f = self.fixture; handle = f.start(); f.ended(23)
        self.assertIsNone(f.child.poll())
        seen = self.provider.inspect(handle)
        self.assertEqual(("gone", 23, 0), (seen["state"], seen["job_exit"], seen["memory_current"]))
        self.assertEqual("321", (f.path / "guard/cgroup.procs").read_text())

    def test_unaligned_memory_limit_refuses_without_starting_native_unit(self):
        with self.assertRaises(ValueError):
            GuardedCgroupProvider(BASELINE, required_bytes=1, reserve_bytes=1, memory_max=os.sysconf("SC_PAGE_SIZE") + 1)

    def test_failed_start_closes_status_reader_without_closing_sdk_release(self):
        f = self.fixture; original = f.provider.popen
        def bad_start(*args, **kwargs):
            child = original(*args, **kwargs)
            (f.path / "job/memory.events").write_text("oom 0")
            return child
        f.provider.popen = bad_start
        reader, writer = os.pipe()
        try:
            with self.assertRaises(ProviderError):f.provider.container(CLAIM, 7, ["true"], {}, reader)
            os.fstat(reader)
            with self.assertRaises(BrokenPipeError):f.writer.write(b"no reader")
            self.assertEqual({}, f.provider.channels)
        finally:os.close(reader); os.close(writer)

    def test_late_native_creation_cannot_return_ignored_handle(self):
        f = self.fixture; original = f.provider.popen
        def delayed(*args, **kwargs):
            child = original(*args, **kwargs); f.time += 31; return child
        f.provider.popen = delayed
        with self.assertRaises(ProviderError):f.start()
        with self.assertRaises(BrokenPipeError):f.writer.write(b"unpublished")

    def test_stop_only_kills_prepared_job_and_retains_guard(self):
        f = self.fixture; handle = f.start()
        self.assertEqual("alive", self.provider.stop(handle, 0)["state"])
        self.assertEqual(["kill_job"], f.actions)
        self.assertEqual("\n", (f.path / "guard/cgroup.kill").read_text())
        self.assertEqual("\n", (f.path / "cgroup.kill").read_text())

    def test_nonclean_outcomes_retain_both_leaves_and_status_metadata(self):
        for outcome in ("residue", "unknown", "cleanup_failed"):
            with self.subTest(outcome=outcome):
                f = GuardFixture(self); handle = f.start()
                f.ended(current=11 if outcome == "residue" else 0)
                if outcome == "unknown":
                    (f.path / "job/memory.current").unlink()
                if outcome == "cleanup_failed":
                    f.leaf("job", "999", 1)
                receipt = f.provider.release_receipt(handle, "failed")
                self.assertEqual(outcome, receipt["cleanup"])
                self.assertTrue((f.path / "guard").exists() and (f.path / "job").exists())
                self.assertIn(handle["ref"], f.provider.owned)
                self.assertEqual([], f.actions)

    def test_clean_dismissal_order_after_independent_job_counter_proof(self):
        f = self.fixture; handle = f.start(); f.ended()
        self.assertEqual("verified_clean", self.provider.release_receipt(handle, "completed")["cleanup"])
        self.assertEqual(["remove_job", "kill_guard"], f.actions)
        self.assertFalse((f.path / "job").exists())
        self.assertEqual("\n", (f.path / "cgroup.kill").read_text())
        self.assertNotIn(handle["ref"], self.provider.owned)

    def test_restart_reconcile_uses_full_journal_identity_without_primary_status(self):
        f = self.fixture; handle = f.start(); f.ended()
        restarted = GuardedCgroupProvider(BASELINE, required_bytes=1, reserve_bytes=1, charge_tolerance=10,
                                         drain_seconds=0.1, sample_interval=0.01, stable_samples=2)
        f.bind(restarted)
        self.assertEqual("untracked_epoch", restarted.telemetry()[0]["occupancy"])
        self.assertEqual("unknown", restarted.telemetry()[1])
        self.assertIsNone(restarted.inspect(handle["ref"])["job_exit"])
        with self.assertRaises(ProviderError):
            restarted.stop(handle, 0)
        entries = [{"kind": "launched", "claim": CLAIM, "fence": 7, "ref": handle["ref"]}]
        self.assertEqual("verified_clean", restarted.reconcile_verdict(entries, CLAIM, 7)[0])

    def test_changed_fence_and_each_replaced_leaf_fail_closed(self):
        for target in ("fence", "guard", "job", "scope"):
            with self.subTest(target=target):
                f = GuardFixture(self); handle = f.start()
                if target == "fence":
                    shutil.copytree(f.path, f.path.with_name(GuardedCgroupProvider.epoch_unit(CLAIM, 8)))
                    handle = dict(handle, fence=8, ref=handle["ref"].replace(":7:", ":8:"))
                else:
                    path = f.path if target == "scope" else f.path / target
                    old = path.with_name(path.name + ".old")
                    path.rename(old); shutil.copytree(old, path)
                self.assertEqual("unverifiable", f.provider.inspect(handle)["state"])
                with self.assertRaises(ProviderError):
                    f.provider.stop(handle, 0)
                self.assertEqual([], f.actions)

    def test_lost_or_malformed_channel_does_not_invent_job_exit(self):
        f = self.fixture; handle = f.start(); f.writer.write(b"exit 0\nexit 7\n"); f.writer.close()
        with self.assertRaises(ProviderError):
            self.provider.inspect(handle)
        f.leaf("job")
        # Native proof remains independently usable for an aborted SDK receipt.
        self.assertEqual("verified_clean", self.provider.release_receipt(handle, "aborted")["cleanup"])

    def test_missing_reference_or_old_reference_never_proves_pristine(self):
        self.assertEqual("unknown", self.provider.reconcile_verdict([], CLAIM, 7)[0])
        self.assertEqual("unverifiable", self.provider.inspect("scope:" + CLAIM + ":7:" + BOOT + ":1")["state"])

    def test_another_device_cannot_reconcile_or_dismiss_this_reference(self):
        f = self.fixture; handle = f.start(); f.ended()
        other = GuardedCgroupProvider(replace(BASELINE, gpu_uuid=BASELINE.gpu_uuid[:-1] + "d"),
                                     required_bytes=1, reserve_bytes=1, drain_seconds=0.1,
                                     sample_interval=0.01, stable_samples=2)
        f.bind(other)
        self.assertEqual("unverifiable", other.inspect(handle["ref"])["state"])
        entries = [{"kind":"launched", "claim":CLAIM, "fence":7, "ref":handle["ref"]}]
        self.assertEqual("unknown", other.reconcile_verdict(entries, CLAIM, 7)[0])
        self.assertEqual([], f.actions)

    def test_tagged_scope_for_other_gpu_does_not_hide_this_gpus_health(self):
        f = self.fixture; f.start()
        baseline = replace(BASELINE, gpu_uuid=BASELINE.gpu_uuid[:-1] + "d")
        other = GuardedCgroupProvider(baseline, required_bytes=1, reserve_bytes=1)
        f.bind(other)
        other.probe = FakeProbe(); other.probe.value = sample(gpu_uuid=baseline.gpu_uuid)
        self.assertEqual("healthy", other.telemetry()[1])

    def test_dismiss_failure_does_not_invent_success_or_counters(self):
        f = self.fixture; handle = f.start(); f.ended()
        def refuse(_fd):
            raise OSError("simulated native refusal")
        self.provider._remove_job = refuse
        self.assertEqual("unknown", self.provider.release_receipt(handle, "completed")["cleanup"])
        self.assertTrue((f.path / "job").exists())
        self.assertIn(handle["ref"], self.provider.owned)

    def test_guard_kill_is_retried_after_job_removal(self):
        f = self.fixture; handle = f.start(); f.ended()
        original = self.provider._kill_fd
        attempts = []
        def flaky(fd):
            attempts.append(fd)
            if len(attempts) == 1:raise PermissionError("transient control refusal")
            original(fd)
        self.provider._kill_fd = flaky
        self.assertEqual("verified_clean", self.provider.release_receipt(handle, "failed")["cleanup"])
        self.assertEqual(2, len(attempts))

    def test_late_receipt_keeps_native_evidence_for_sdk_fallback(self):
        f = self.fixture; handle = f.start(); f.ended()
        inspect = self.provider._inspect_evidence
        calls = []
        def slow(ref):
            calls.append(ref)
            if len(calls) == 2:f.time += 23
            return inspect(ref)
        self.provider._inspect_evidence = slow
        self.assertEqual("unknown", self.provider.release_receipt(handle, "failed")["cleanup"])
        self.assertTrue((f.path / "job").exists())
        self.assertEqual([], f.actions)
        self.assertEqual(2, len(calls))


class StatusTests(unittest.TestCase):
    def test_aborted_is_not_an_exit_and_reader_keeps_unexecuted_guard_published(self):
        reader, writer = os.pipe(); channel = StatusChannel(reader)
        try:
            os.write(writer, b"aborted\n")
            status = channel.read()
            self.assertTrue(status["job_aborted"])
            self.assertIsNone(status["job_exit"])
            os.fstat(channel.fd)
        finally:channel.close(); os.close(writer)

    def test_invalid_status_is_bounded_and_never_echoed(self):
        for payload in (b"exit 256\n", b"signal 0\n", b"signal 999\n", b"sensitive-invalid-data\n", b"a" * 64):
            reader, writer = os.pipe(); channel = StatusChannel(reader)
            try:
                os.write(writer, payload)
                with self.assertRaises(ProviderError) as failure:channel.read()
                self.assertNotIn("sensitive", str(failure.exception))
            finally:channel.close(); os.close(writer)
        reader, writer = os.pipe(); channel = StatusChannel(reader); channel.close()
        try:
            with self.assertRaises(ProviderError):channel.read()
        finally:os.close(writer)

    def test_partial_signal_and_closed_channel(self):
        reader, writer = os.pipe(); channel = StatusChannel(reader)
        try:
            os.write(writer, b"signal "); self.assertIsNone(channel.read()["job_signal"])
            os.write(writer, b"15\n"); self.assertEqual(15, channel.read()["job_signal"])
            self.assertEqual(15, channel.read()["job_signal"])
        finally:
            channel.close(); os.close(writer)
        reader, writer = os.pipe(); channel = StatusChannel(reader); os.close(writer)
        try:
            with self.assertRaises(ProviderError):channel.read()
        finally:channel.close()


if __name__ == "__main__":
    unittest.main()
