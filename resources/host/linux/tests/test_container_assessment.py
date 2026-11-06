"""Local assessment controls. No live device, process control or coordinator."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import container_assessment as assessment
from evidence import utc_now
from test_linux_procscan import BASE, BOOT, CLAIM, GPU, REF, Device, make_provider


NOW = "2026-10-28T15:00:00Z"
BIND = assessment.LocalBinding(CLAIM, 1, BOOT, 123, 456, 789, GPU, BASE.total_bytes)


def ended(**updates):
    return {"ref": REF, "observed_at": NOW, "state": "gone", "job_ended": True,
            "members": [], "job_exit": 0, "job_signal": None, **updates}


def device(**updates):
    return Device(**{"observed_at": NOW, "measurement": "simulated-device-counters", **updates}).snapshot()


def limits(**updates):
    return {"observed_at": NOW, "boot_id": BOOT, "system_available_bytes": 4096,
            "cgroup_current_bytes": 1024, "cgroup_max_bytes": 8192,
            "cgroup_high_bytes": None,
            "cgroup_inode": 12345, "scope": "visible-cgroup-v2-namespace-root", **updates}


def assess(inspection=None, sample=None, memory=None, binding=BIND, *, memory_mode="unified", compute=None):
    sample = device() if sample is None else sample
    return assessment.assess(binding, ended() if inspection is None else inspection,
        sample, limits() if memory is None else memory, compute_sample=sample if compute is None else compute,
        memory_mode=memory_mode, required_bytes=128, reserve_bytes=128, _test_now=NOW)


class AssessmentControls(unittest.TestCase):
    def test_normal_local_facts_never_authorize_a_handoff_or_receipt(self):
        r = assess()
        self.assertEqual("ended", r["managed_cleanup"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertEqual("none_additional_visible", r["visible_compute"]["state"])
        self.assertTrue(r["not_a_receipt"])
        self.assertIs(False, r["can_authorize_handoff"])
        self.assertNotIn("cleanup", r)
        self.assertNotIn("result", r)
        self.assertEqual(REF, r["requested_binding"]["native_ref"])
        self.assertTrue(r["native_identity_observed"])
        self.assertTrue(r["device_identity_observed"])

    def test_lower_than_cold_baseline_can_have_enough_current_capacity(self):
        r = assess(sample=device(free_bytes=512))
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertEqual("ended", r["managed_cleanup"]["state"])

    def test_live_members_dominate_terminal_marker_and_sufficient_capacity(self):
        r = assess(inspection=ended(members=[{"pid": 124, "start": 457}]))
        self.assertEqual("live", r["managed_cleanup"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertFalse(r["can_authorize_handoff"])

    def test_capacity_shortage_does_not_invent_residue_or_failed_cleanup(self):
        r = assess(sample=device(free_bytes=255))
        self.assertEqual("insufficient", r["observed_capacity"]["state"])
        self.assertEqual("ended", r["managed_cleanup"]["state"])

    def test_system_and_cgroup_hard_limits_each_cap_capacity_without_reclaim(self):
        for row in (limits(system_available_bytes=255), limits(cgroup_max_bytes=1279),
                    limits(cgroup_current_bytes=9000)):
            with self.subTest(row=row):
                self.assertEqual("insufficient", assess(memory=row)["observed_capacity"]["state"])

    def test_unlimited_visible_cgroup_still_obeys_system_and_device_minimum(self):
        r = assess(memory=limits(cgroup_max_bytes=None, system_available_bytes=256))
        self.assertIsNone(r["observed_capacity"]["visible_cgroup_hard_unused_bytes"])
        self.assertEqual(256, r["observed_capacity"]["available_bytes"])
        self.assertEqual("enough", r["observed_capacity"]["state"])

    def test_each_native_binding_component_mismatch_cannot_establish_ended(self):
        for b in (replace(BIND, claim_id="01AAAAAAAAAAAAAAAAAAAAAA04"), replace(BIND, fence=2),
                  replace(BIND, supervisor_pid=124), replace(BIND, supervisor_start=457),
                  replace(BIND, directory_inode=790), replace(BIND, boot_id="aaaaaaaa-aaaa-4aaa-8aaa-00000000000a")):
            with self.subTest(b=b):
                self.assertEqual("unknown", assess(binding=b)["managed_cleanup"]["state"])

    def test_unknown_supervisor_can_coexist_with_enough_observed_capacity(self):
        r = assess(inspection={"state": "unverifiable"})
        self.assertEqual("unknown", r["managed_cleanup"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])

    def test_ended_requires_valid_unique_empty_membership_and_terminal_status(self):
        for updates in ({"job_ended": False}, {"job_exit": None}, {"job_signal": 9},
                        {"job_exit": True}, {"job_exit": -1}, {"job_exit": 256}, {"job_signal": False},
                        {"error": "redacted"}, {"members": None}, {"members": [None]},
                        {"members": [{"pid": 124, "start": True}]},
                        {"members": [{"pid": 124, "start": 4}] * 2},
                        {"members": [{"pid": 123, "start": 456}]}):
            with self.subTest(updates=updates):
                self.assertEqual("unknown", assess(inspection=ended(**updates))["managed_cleanup"]["state"])

    def test_failed_exit_signal_and_preexec_abort_are_terminal_without_success_claim(self):
        for row in (ended(job_exit=23), ended(job_exit=None, job_signal=9),
                    ended(job_exit=None, job_aborted=True), ended(job_exit=126, job_failed_to_start=True)):
            with self.subTest(row=row):
                self.assertEqual("ended", assess(inspection=row)["managed_cleanup"]["state"])

    def test_stale_future_naive_and_malformed_timestamps_are_unknown_per_fact(self):
        for stamp in ("2026-10-28T14:59:49Z", "2026-10-28T15:00:01Z", "2026-10-28T15:00:00", None, [], "x"):
            with self.subTest(stamp=stamp):
                r = assess(inspection=ended(observed_at=stamp))
                self.assertEqual("unknown", r["managed_cleanup"]["state"])
                self.assertEqual("enough", r["observed_capacity"]["state"])
                self.assertEqual("unknown", assess(sample=device(observed_at=stamp))["observed_capacity"]["state"])
                self.assertEqual("unknown", assess(memory=limits(observed_at=stamp))["observed_capacity"]["state"])

    def test_wrong_device_boot_total_and_counter_types_never_establish_capacity(self):
        for updates in ({"boot_id": "wrong"}, {"gpu_uuid": "wrong"}, {"total_bytes": 999},
                        {"free_bytes": True}, {"free_bytes": -1}, {"free_bytes": 8193},
                        {"total_bytes": False}, {"free_bytes": float("nan")}, {"free_bytes": 2**64}):
            with self.subTest(updates=updates):
                self.assertEqual("unknown", assess(sample=device(**updates))["observed_capacity"]["state"])

    def test_missing_or_invalid_visible_cgroup_cannot_be_replaced_by_device_capacity(self):
        for row in ({}, limits(boot_id="wrong"), limits(cgroup_inode=True), limits(cgroup_current_bytes=True),
                    limits(scope="guessed"), limits(cgroup_max_bytes=-1), limits(system_available_bytes=2**64)):
            with self.subTest(row=row):
                self.assertEqual("unknown", assess(memory=row)["observed_capacity"]["state"])

    def test_container_pid_namespace_and_foreign_occupancy_remain_separate_facts(self):
        r = assess(sample=device(inventory_namespace_qualified=False, unknown_gpu_pids=[]))
        self.assertEqual("unknown", r["visible_compute"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertEqual("ended", r["managed_cleanup"]["state"])
        self.assertEqual("unclassified", assess(sample=device(unknown_gpu_pids=[999]))["visible_compute"]["state"])
        self.assertEqual("unknown", assess(sample=device(unknown_gpu_pids=[True]))["visible_compute"]["state"])

    def test_inputs_and_binding_are_not_mutated_or_reused_as_cached_proof(self):
        i, d, m = ended(), device(), limits(); saved = deepcopy((i, d, m))
        r = assessment.assess(BIND, i, d, m, memory_mode="unified", required_bytes=128, reserve_bytes=128, _test_now=NOW)
        r["requested_binding"]["fence"] = 999
        self.assertEqual(saved, (i, d, m))
        self.assertEqual(1, assess()["requested_binding"]["fence"])

    def test_bad_configuration_rejected(self):
        for updates in ({"fence": True}, {"total_bytes": False}, {"supervisor_pid": 2**31},
                        {"directory_inode": 0}, {"gpu_uuid": "x"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                replace(BIND, **updates)
        for req, reserve in ((True, 1), (0, 1), (1, -1), (2**63, 1)):
            with self.subTest(required=req, reserve=reserve), self.assertRaises(ValueError):
                assessment.assess(BIND, ended(), device(), limits(), memory_mode="unified", required_bytes=req, reserve_bytes=reserve, _test_now=NOW)
        for at in ([], False, 0, ""):
            with self.subTest(at=at), self.assertRaises(ValueError):
                assessment.assess(BIND, ended(), device(), limits(), memory_mode="unified",
                                  required_bytes=128, reserve_bytes=128, _test_now=at)

    def test_provider_assessment_has_no_native_control_or_authority_mutation(self):
        p = make_provider(memory_mode="unified"); original = p.declare()
        with patch.object(p, "_host"), patch.object(p, "_rpc", return_value=ended()) as rpc:
            p.probe = Mock(memory_snapshot=lambda: device(observed_at=utc_now()),
                           compute_snapshot=lambda owned: device(observed_at=utc_now()))
            r = p.assess_container(REF, limits_probe=lambda: limits(observed_at=utc_now()))
        self.assertEqual("ended", r["managed_cleanup"]["state"])
        rpc.assert_called_once_with(REF)
        self.assertEqual(set(), p.created)
        self.assertEqual({}, p.children)
        self.assertEqual(original, p.declare())
        self.assertFalse(p.memory_qualified)

    def test_provider_host_refusal_does_not_probe_or_accept_capacity(self):
        p = make_provider(memory_mode="unified"); p.probe = Mock()
        with patch.object(p, "_host", side_effect=RuntimeError("must not leak")), patch.object(p, "_rpc") as rpc:
            r = p.assess_container(REF)
        self.assertEqual("unknown", r["managed_cleanup"]["state"])
        self.assertEqual("unknown", r["observed_capacity"]["state"])
        rpc.assert_not_called(); p.probe.memory_snapshot.assert_not_called(); p.probe.compute_snapshot.assert_not_called()
        self.assertFalse(r["native_identity_observed"])
        self.assertFalse(r["device_identity_observed"])
        self.assertNotIn("must not leak", str(r))

    def test_provider_observation_errors_do_not_conflate_cleanup_and_capacity(self):
        p = make_provider(memory_mode="unified")
        p.probe = Mock(memory_snapshot=lambda: device(observed_at=utc_now()),
                       compute_snapshot=lambda owned: device(observed_at=utc_now()))
        with patch.object(p, "_host"), patch.object(p, "_rpc", side_effect=RuntimeError("private")):
            r = p.assess_container(REF, limits_probe=lambda: limits(observed_at=utc_now()))
        self.assertEqual("unknown", r["managed_cleanup"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertNotIn("private", str(r))

    def test_visible_limits_observes_only_namespace_root_and_no_reclaim_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); proc = root / "proc"; cg = root / "cgroup"
            (proc / "self").mkdir(parents=True); (proc / "sys/kernel/random").mkdir(parents=True); cg.mkdir()
            (proc / "self/cgroup").write_text("0::/\n"); (proc / "meminfo").write_text("MemAvailable: 16 kB\n")
            (proc / "sys/kernel/random/boot_id").write_text(BOOT)
            (cg / "memory.max").write_text("10000"); (cg / "memory.current").write_text("9999")
            (cg / "memory.high").write_text("max")
            (cg / "cgroup.procs").write_text(str(os.getpid()) + "\n")
            r = assessment.visible_limits(proc, cg)
            self.assertEqual(16384, r["system_available_bytes"])
            self.assertEqual(10000, r["cgroup_max_bytes"])
            self.assertEqual(9999, r["cgroup_current_bytes"])
            self.assertEqual("unavailable", r["hidden_ancestor_limits"])
            (cg / "memory.max").write_text("max")
            self.assertIsNone(assessment.visible_limits(proc, cg)["cgroup_max_bytes"])
            for bad in ("-1", "True", "1.5", str(2**64)):
                (cg / "memory.max").write_text(bad)
                with self.assertRaises(ValueError): assessment.visible_limits(proc, cg)
            (cg / "memory.max").write_text("10000")
            (cg / "cgroup.procs").write_text("1\n")
            with self.assertRaises(ValueError): assessment.visible_limits(proc, cg)
            (cg / "cgroup.procs").write_text(str(os.getpid()) + "\n")
            (proc / "self/cgroup").write_text("0::/user.slice\n")
            with self.assertRaises(ValueError): assessment.visible_limits(proc, cg)

    def test_each_non_dict_observation_does_not_erase_other_facts(self):
        for value in ([], "invalid", True):
            self.assertEqual("ended", assess(memory=value)["managed_cleanup"]["state"])
            self.assertEqual("ended", assess(sample=value)["managed_cleanup"]["state"])
            self.assertEqual("enough", assess(inspection=value)["observed_capacity"]["state"])

    def test_discrete_device_request_is_not_a_host_memory_request(self):
        r = assess(memory={}, memory_mode="discrete", sample=device(free_bytes=4096))
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertEqual("discrete", r["observed_capacity"]["memory_mode"])
        self.assertIn("host RAM request not sized", r["observed_capacity"]["scope"])
        self.assertEqual("simulated-device-counters", r["observed_capacity"]["measurement"])
        self.assertNotIn("unified observer creates a transient CUDA context", r["limitations"])

    def test_memory_high_is_a_pressure_fact_not_a_hard_limit(self):
        r = assess(memory=limits(cgroup_high_bytes=1200))
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertTrue(r["observed_capacity"]["throttling_possible"])
        self.assertEqual(1200, r["observed_capacity"]["visible_cgroup_high_bytes"])
        self.assertFalse(assess()["observed_capacity"]["throttling_possible"])

    def test_inventory_failure_keeps_independently_observed_capacity(self):
        p = make_provider(memory_mode="unified")
        p.probe = Mock(memory_snapshot=lambda: device(observed_at=utc_now()))
        p.probe.compute_snapshot.side_effect = RuntimeError("inventory private error")
        with patch.object(p, "_host"), patch.object(p, "_rpc", return_value=ended()):
            r = p.assess_container(REF, limits_probe=lambda: limits(observed_at=utc_now()))
        self.assertEqual("ended", r["managed_cleanup"]["state"])
        self.assertEqual("enough", r["observed_capacity"]["state"])
        self.assertEqual("unknown", r["visible_compute"]["state"])
        self.assertNotIn("inventory private error", str(r))

    def test_split_real_probe_keeps_counters_when_reported_pid_cannot_be_resolved(self):
        import linux_procscan
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp); (proc / "sys/kernel/random").mkdir(parents=True)
            (proc / "sys/kernel/random/boot_id").write_text(BOOT)
            def runner(args):
                return f"{GPU}, 4096, 8192\n" if "--query-gpu=uuid,memory.free,memory.total" in args else f"999, {GPU}, 173\n"
            probe = linux_procscan.RentalDeviceProbe(GPU, runner=runner, proc=proc)
            with self.assertRaises(FileNotFoundError): probe.snapshot()
            p = make_provider(); p.baseline = replace(BASE, total_bytes=8192 * 1024**2); p.probe = probe
            with patch.object(p, "_host"), patch.object(p, "_rpc", return_value=ended()):
                r = p.assess_container(REF, limits_probe=lambda: {})
            self.assertEqual("enough", r["observed_capacity"]["state"])
            self.assertEqual("unknown", r["visible_compute"]["state"])

    def test_combined_abort_and_exit_are_not_one_terminal_status(self):
        self.assertEqual("unknown", assess(inspection=ended(job_aborted=True))["managed_cleanup"]["state"])

    def test_package_entrypoint_loads_the_optional_assessment_from_its_own_siblings(self):
        import sdk_provider
        module = sdk_provider._provider("linux_procscan")
        p = module.ProcscanProvider(BASE, state_root=Path("/tmp/pq-assess-unit").resolve(),
            required_bytes=128, reserve_bytes=128, memory_mode="unified", probe=Mock())
        with patch.object(p, "_host", side_effect=RuntimeError("fixture")):
            self.assertFalse(p.assess_container(REF)["can_authorize_handoff"])


if __name__ == "__main__":
    unittest.main()
