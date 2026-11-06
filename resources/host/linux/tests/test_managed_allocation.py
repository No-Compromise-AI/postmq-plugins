"""Synthetic scope/identity controls; these do not certify a physical device.

Run through the public test runner, which assembles the flat Linux bundle with
its NVIDIA sibling dependencies. An unassembled source directory is not an
installed provider and cannot produce an allocation binding.
"""
import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

from evidence import Baseline, utc_now
import managed_allocation as managed
import linux_procscan as legacy
import allocation_scope as scope

CLAIM = "01AAAAAAAAAAAAAAAAAAAAAA03"
BOOT = "aaaaaaaa-aaaa-4aaa-8aaa-000000000009"
GPU = "GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000006"
BASE = Baseline(BOOT, GPU, 4096, 8192, 16, "2026-10-28T00:00:00Z")
SCOPE = "a" * 32


def observation(**updates):
    return {"state": "gone", "job_ended": True, "job_exit": 0, "job_signal": None,
            "members": [], "scope_seal": SCOPE, **updates}


def sample(**updates):
    return {"boot_id": BOOT, "gpu_uuid": GPU, "total_bytes": BASE.total_bytes,
            "free_bytes": 1, "observed_at": utc_now(), "scope_seal": SCOPE,
            "allocation_inventory_qualified": True, "unknown_gpu_pids": [], **updates}


class AllocationVerdicts(unittest.TestCase):
    def test_uma_managed_cleanup_does_not_require_cold_physical_memory(self):
        self.assertEqual("verified_clean", managed.clean_verdict(BASE, observation(), sample(), SCOPE)[0])
        # This is a new meaning/name. Existing UMA receipts remain unknown.
        self.assertEqual("unknown", legacy.clean_verdict(BASE, observation(), sample(), memory_qualified=False)[0])

    def test_fresh_headroom_never_substitutes_for_terminal_owned_work(self):
        for change, expected in [({"state": "alive", "members": [{"pid": 1, "start": 1}]}, "cleanup_failed"),
                                 ({"job_ended": False}, "unknown"), ({"job_exit": None}, "unknown"),
                                 ({"job_exit": 0, "job_signal": 9}, "unknown"),
                                 ({"job_signal": "invalid"}, "unknown"),
                                 ({"state": "unverifiable"}, "unknown"),
                                 ({"scope_seal": "b" * 32}, "unknown")]:
            with self.subTest(change=change):
                self.assertEqual(expected, managed.clean_verdict(BASE, observation(**change), sample(free_bytes=8192), SCOPE)[0])

    def test_host_namespace_or_empty_nvml_list_is_not_allocation_inventory(self):
        for change in ({"allocation_inventory_qualified": False}, {"unknown_gpu_pids": None},
                       {"scope_seal": "b" * 32}, {"boot_id": "wrong"}, {"gpu_uuid": "wrong"},
                       {"observed_at": "2000-01-01T00:00:00Z"}):
            with self.subTest(change=change):
                self.assertEqual("unknown", managed.clean_verdict(BASE, observation(), sample(**change), SCOPE)[0])
        self.assertEqual("cleanup_failed", managed.clean_verdict(BASE, observation(), sample(unknown_gpu_pids=[99]), SCOPE)[0])

    def test_version_and_scope_are_not_interchangeable(self):
        ref = f"procscan2:{CLAIM}:1:{BOOT}:123:456:789:{SCOPE}"
        self.assertEqual((CLAIM, 1, BOOT, 123, 456, 789), managed.parts(ref, SCOPE))
        with self.assertRaises(legacy.RentalError):
            legacy.parts(ref)
        for candidate in (ref.replace("procscan2:", "procscan:", 1), ref[:-1] + "b", ref + "\n"):
            with self.assertRaises(legacy.RentalError):
                managed.parts(candidate, SCOPE)


class Admission(unittest.TestCase):
    def make(self):
        with patch.object(scope, "binding", return_value=SCOPE):
            p = managed.AllocationProvider(BASE, state_root=Path("/tmp/pq-allocation-unit").resolve(),
                required_bytes=128, reserve_bytes=128, probe=Mock(), memory_mode="unified",
                provider="vast", instance_id="123")
        p.probe.snapshot.return_value = sample(free_bytes=4096)
        return p

    def limits(self, **updates):
        return {"observed_at": utc_now(), "boot_id": BOOT,
                "system_available_bytes": 4096, "cgroup_current_bytes": 64,
                "cgroup_max_bytes": 8192, "cgroup_high_bytes": None, "cgroup_inode": 123,
                "scope": "visible-cgroup-v2-namespace-root", **updates}

    def test_clean_scope_and_capacity_remain_independent(self):
        p = self.make()
        declaration = p.declare()
        self.assertEqual("linux-procscan-v2", declaration["receipt_provider"])
        self.assertEqual("provider_instance", declaration["identity_kind"])
        self.assertEqual(f"vast:123:{BOOT}", declaration["identity_key"])
        self.assertEqual(declaration["identity_key"], declaration["provider_host_key"])
        self.assertEqual({"advisory"}, set(declaration["enforcement"].values()))
        claim = {"claim_id": CLAIM, "fence": 1, "state": "granted"}
        with patch.object(p, "_host"), patch.object(managed, "visible_limits", return_value=self.limits()):
            self.assertEqual("admitted", p.preflight(claim)["result"])
        for change in ({"system_available_bytes": 128}, {"cgroup_max_bytes": 256},
                       {"cgroup_high_bytes": 256}, {"observed_at": "2000-01-01T00:00:00Z"},
                       {"boot_id": "wrong"}, {"cgroup_current_bytes": True}, {"cgroup_inode": None}):
            with self.subTest(change=change), patch.object(p, "_host"), \
                 patch.object(managed, "visible_limits", return_value=self.limits(**change)):
                self.assertEqual("refused", p.preflight(claim)["result"])

        # Old free memory below its baseline is not residue in this contract.
        self.assertEqual("verified_clean", p.cleanup_verdict(observation(), sample())[0])

    def test_sdk_default_stop_is_bounded_and_invalid_grace_never_reaches_native(self):
        p = self.make()
        ref = f"procscan2:{CLAIM}:1:{BOOT}:123:456:789:{SCOPE}"
        p.created.add(ref)
        handle = {"ref": ref, "claim_id": CLAIM, "fence": 1}
        with patch.object(p, "_rpc", return_value={"state": "gone"}) as native:
            p.stop(handle, 30)
            native.assert_called_once_with(ref, "stop", grace=10.0)
        for grace in (True, -1, float("inf"), float("nan"), "30"):
            with self.subTest(grace=grace), patch.object(p, "_rpc") as native:
                with self.assertRaises(legacy.RentalError):
                    p.stop(handle, grace)
                native.assert_not_called()

    def test_changed_scope_and_unclassified_visible_holder_refuse(self):
        p = self.make()
        claim = {"claim_id": CLAIM, "fence": 1, "state": "granted"}
        for change in ({"scope_seal": "b"*32}, {"unknown_gpu_pids": [99]},
                       {"allocation_inventory_qualified": False}, {"total_bytes": 9}):
            p.probe.snapshot.return_value = sample(free_bytes=4096, **change)
            with patch.object(p, "_host"), patch.object(managed, "visible_limits", return_value=self.limits()):
                self.assertEqual("refused", p.preflight(claim)["result"])

    def test_live_native_failure_dominates_failed_probe_and_retains_guard(self):
        p = self.make(); p.drain_seconds = 0
        ref = f"procscan2:{CLAIM}:1:{BOOT}:123:456:789:{SCOPE}"
        p.probe.snapshot.side_effect = PermissionError
        with patch.object(p, "_rpc", return_value=observation(state="alive", members=[{"pid": 99, "start": 9}])) as rpc:
            outcome, detail, tail = p._verdict(ref)
            self.assertEqual("cleanup_failed", outcome)
            self.assertIn("cleanup_failed", tail[-1]["summary"])
            self.assertEqual(1, rpc.call_count)  # no dismissal

    def test_active_telemetry_exempts_only_a_freshly_authenticated_guard(self):
        p = self.make()
        ref = f"procscan2:{CLAIM}:1:{BOOT}:123:456:789:{SCOPE}"
        p.created.add(ref)
        members = [{"pid": 99, "start": 9}]
        with patch.object(p, "_host"), patch.object(p, "_rpc", return_value=observation(state="alive", members=members)) as rpc:
            p.telemetry()
            rpc.assert_called_once_with(ref)
            p.probe.snapshot.assert_called_once_with(members, guard=(123, 456))
        p.probe.snapshot.reset_mock()
        with patch.object(p, "_host"), patch.object(p, "_rpc", side_effect=legacy.RentalError):
            self.assertEqual("unknown", p.telemetry()[1])
            p.probe.snapshot.assert_not_called()


class AllocationInventory(unittest.TestCase):
    def test_exactly_one_visible_gpu_and_all_character_aliases_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "dri").mkdir(); (root / "nvidia-caps").mkdir()
            (root / "nvidia0").write_text("not a device")
            with self.assertRaises(scope.native.NativeUnknown):
                scope.devices(root)
            (root / "nvidia0").unlink(); (root / "nvidia0").symlink_to(Path(os.devnull))
            (root / "dri/renderD128").symlink_to(Path(os.devnull))
            (root / "nvidia-caps/nvidia-cap1").symlink_to(Path(os.devnull))
            self.assertEqual({"nvidia0", "dri/renderD128", "nvidia-caps/nvidia-cap1"}, set(scope.devices(root)))
            (root / "nvidia1").symlink_to(Path(os.devnull))
            with self.assertRaises(scope.native.NativeUnknown):
                scope.devices(root)

    def test_slow_descriptor_inventory_is_unknown_instead_of_stamped_fresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "99" / "fd").mkdir(parents=True)
            with patch.object(scope.time, "monotonic", side_effect=[0, 2]):
                with self.assertRaises(scope.native.NativeUnknown):
                    scope.visible_holders({"fixture": [1, 3]}, proc=root)

    def test_device_alias_is_a_holder_but_an_unrelated_file_is_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); folder = root / "99" / "fd"; folder.mkdir(parents=True)
            (folder / "5").symlink_to(Path(os.devnull))
            info = Path(os.devnull).stat()
            devices = {"fixture": [os.major(info.st_rdev), os.minor(info.st_rdev)]}
            with patch.object(scope.native, "process", return_value={"start": 123}):
                self.assertEqual([99], scope.visible_holders(devices, proc=root))
                self.assertEqual([], scope.visible_holders(devices, proc=root, guard=(99, 123)))
                # A different birth is never the selected supervisor exemption.
                self.assertEqual([99], scope.visible_holders(devices, proc=root, guard=(99, 124)))
                (folder / "5").unlink(); (folder / "5").write_text("ordinary file")
                self.assertEqual([], scope.visible_holders(devices, proc=root))

    def test_missing_or_replaced_process_is_not_an_empty_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "99" / "fd").mkdir(parents=True)
            with patch.object(scope.native, "process", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    scope.visible_holders({"fixture": [1, 3]}, proc=root)
            with patch.object(scope.native, "process", side_effect=[{"start": 1}, {"start": 2}]):
                with self.assertRaises(scope.native.NativeUnknown):
                    scope.visible_holders({"fixture": [1, 3]}, proc=root)

    def test_scope_seal_changes_with_namespace_and_refuses_hidden_proc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); proc = root / "proc"; cg = root / "cgroup"; cg.mkdir()
            for part in ("self/ns", "1/ns", "sys/kernel/random"):
                (proc / part).mkdir(parents=True)
            (proc / "sys/kernel/random/boot_id").write_text(BOOT)
            (proc / "self/cgroup").write_text("0::/\n")
            (proc / "self/ns/pid").symlink_to("pid:[123]")
            (proc / "1/ns/pid").symlink_to("pid:[123]")
            (proc / "self/ns/mnt").symlink_to("mnt:[456]")
            mount = f"1 0 0:1 / {proc} rw - proc proc rw\n"
            (proc / "self/mountinfo").write_text(mount)
            profile = {"provider": "vast", "instance_id": "123", "gpu_uuid": GPU}
            with patch.dict(os.environ, {"CONTAINER_ID": "123"}), \
                 patch.object(scope, "devices", return_value={"nvidia0": [195, 0]}), \
                 patch.object(scope.native, "process", return_value={"start": 9}):
                first = scope.binding(profile, proc=proc, cgroup=cg)
                (proc / "self/ns/mnt").unlink(); (proc / "self/ns/mnt").symlink_to("mnt:[457]")
                self.assertNotEqual(first, scope.binding(profile, proc=proc, cgroup=cg))
                (proc / "self/mountinfo").write_text(mount.rstrip() + ",hidepid=2\n")
                with self.assertRaises(scope.native.NativeUnknown):
                    scope.binding(profile, proc=proc, cgroup=cg)


if __name__ == "__main__":
    unittest.main()
