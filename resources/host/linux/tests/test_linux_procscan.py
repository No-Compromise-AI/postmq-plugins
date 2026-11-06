"""Pure rental-provider refusals and opt-in Linux owned-process integration.

The Linux tests use a simulated device observation; they do not qualify CUDA
or a real coordinator. Enable them only on the selected reviewed test fixture.
"""

from dataclasses import replace
import importlib.util
import json
import os
import select
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from evidence import Baseline
import linux_procscan as provider
import rental_native as native


CLAIM = "01AAAAAAAAAAAAAAAAAAAAAA03"
BOOT = "aaaaaaaa-aaaa-4aaa-8aaa-000000000009"
GPU = "GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000006"
BASE = Baseline(BOOT, GPU, 4096, 8192, 16, "2026-10-28T00:00:00Z")
REF = f"procscan:{CLAIM}:1:{BOOT}:123:456:789"


class Device:
    def __init__(self, **updates):
        self.updates = updates

    def snapshot(self, owned=()):
        return {"boot_id": BASE.boot_id, "gpu_uuid": BASE.gpu_uuid, "free_bytes": BASE.free_bytes,
                "total_bytes": BASE.total_bytes, "unknown_gpu_pids": [],
                "inventory_namespace_qualified": True, **self.updates}


def make_provider(**kwargs):
    return provider.ProcscanProvider(BASE, state_root=Path("/tmp/pq-rental-unit").resolve(),
                                    required_bytes=128, reserve_bytes=128, probe=Device(), **kwargs)


class PureRentalTests(unittest.TestCase):
    def test_reference_binds_claim_fence_boot_pid_birth_and_directory(self):
        self.assertEqual((CLAIM, 1, BOOT, 123, 456, 789), provider.parts(REF))
        p = make_provider()
        self.assertEqual(REF, p.journal_ref({"ref": REF, "claim_id": CLAIM, "fence": 1}))
        for handle in ({"ref": REF, "claim_id": CLAIM, "fence": True},
                       {"ref": REF, "claim_id": CLAIM, "fence": 2}, {"ref": REF, "claim_id": "x", "fence": 1}):
            with self.subTest(handle=handle), self.assertRaises(provider.RentalError):
                p.journal_ref(handle)

    def test_reference_refuses_malformed_and_out_of_range_identity(self):
        for ref in (REF + "\n", REF + " trailing", REF.replace(":1:", ":0:", 1),
                    REF.replace(":123:", ":2147483648:"), REF.replace(":1:", ":9999999999999999999:", 1)):
            with self.subTest(ref=ref), self.assertRaises(provider.RentalError):
                provider.parts(ref)

    def test_declaration_does_not_claim_cgroup_containment(self):
        d = make_provider().declare()
        self.assertEqual("linux-procscan-v1", d["receipt_provider"])
        self.assertEqual(GPU, d["identity_key"])
        self.assertEqual(d["identity_key"], d["provider_host_key"])
        self.assertEqual({"advisory"}, set(d["enforcement"].values()))
        self.assertEqual("unavailable", d["capabilities"]["job_local_cgroup_limits"])
        self.assertFalse(make_provider(memory_mode="unified").declare()["capabilities"]["cleanup_memory_qualified"])

    def test_unqualified_memory_cannot_become_clean(self):
        observed = {"state": "gone", "job_ended": True}
        self.assertEqual("unknown", provider.clean_verdict(BASE, observed, Device().snapshot(), memory_qualified=False)[0])

    def test_live_orphan_dominates_good_memory(self):
        self.assertEqual("cleanup_failed", provider.clean_verdict(
            BASE, {"state": "alive", "job_ended": True}, Device().snapshot(), memory_qualified=True)[0])

    def test_missing_terminal_or_device_identity_is_unknown(self):
        for seen in ({"state": "gone"}, {"state": "unverifiable", "job_ended": True}):
            self.assertEqual("unknown", provider.clean_verdict(BASE, seen, Device().snapshot(), memory_qualified=True)[0])
        for key, value in [("boot_id", "wrong"), ("gpu_uuid", "wrong"), ("total_bytes", 999),
                           ("free_bytes", None), ("unknown_gpu_pids", None)]:
            self.assertEqual("unknown", provider.clean_verdict(BASE, {"state": "gone", "job_ended": True},
                             Device(**{key: value}).snapshot(), memory_qualified=True)[0])

    def test_residue_and_foreign_device_occupancy_are_not_clean(self):
        for data, expected in [({"free_bytes": BASE.free_bytes - 17}, "residue"),
                               ({"unknown_gpu_pids": [999]}, "cleanup_failed")]:
            self.assertEqual(expected, provider.clean_verdict(BASE, {"state": "gone", "job_ended": True},
                             Device(**data).snapshot(), memory_qualified=True)[0])

    def test_preflight_requires_granted_fenced_claim(self):
        p = make_provider()
        with patch.object(p, "_host"):
            self.assertEqual("admitted", p.preflight({"claim_id": CLAIM, "fence": 1, "state": "granted"})["result"])
            for row in ({}, {"claim_id": CLAIM, "fence": 1, "state": "expired"},
                        {"claim_id": CLAIM, "fence": True, "state": "granted"}):
                self.assertEqual("refused", p.preflight(row)["result"])

    def test_missing_occupancy_or_wrong_total_refuses_admission(self):
        p = make_provider()
        for updates in ({"unknown_gpu_pids": None}, {"total_bytes": 123}, {"unknown_gpu_pids": [999]}):
            p.probe = Device(**updates)
            with patch.object(p, "_host"):
                self.assertEqual("refused", p.preflight({"claim_id": CLAIM, "fence": 1, "state": "granted"})["result"])

    def test_clean_window_cannot_succeed_on_one_sample_or_dismiss(self):
        p = make_provider(drain_seconds=0)
        with patch.object(p, "_rpc", return_value={"state": "gone", "job_ended": True, "members": []}) as rpc:
            self.assertEqual("unknown", p._verdict(REF)[0])
            self.assertEqual([((REF,), {})], [(c.args, c.kwargs) for c in rpc.call_args_list])

    def test_nonclean_never_dismisses_native_evidence(self):
        p = make_provider(memory_mode="unified", drain_seconds=0)
        with patch.object(p, "_rpc", return_value={"state": "gone", "job_ended": True, "members": []}) as rpc:
            self.assertEqual("unknown", p._verdict(REF)[0])
            self.assertFalse(any(c.args[1:] == ("dismiss",) for c in rpc.call_args_list))

    def test_clean_window_restarts_after_observation_error(self):
        p = make_provider(drain_seconds=2)
        count = []; good = {"state": "gone", "job_ended": True, "members": []}
        samples = iter([good, good, RuntimeError("fixture gap"), good, good, good])
        def rpc(ref, op="inspect"):
            if op == "dismiss":
                return {"dismissed": True}
            count.append(1); answer = next(samples)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with patch.object(p, "_rpc", side_effect=rpc), patch.object(p, "_directory", return_value=Mock(exists=lambda: False)), \
             patch.object(provider, "process", side_effect=FileNotFoundError), \
             patch.object(provider.time, "sleep"), patch.object(provider.time, "monotonic", side_effect=[i / 100 for i in range(400)]):
            self.assertEqual("verified_clean", p._verdict(REF)[0])
        self.assertEqual(6, len(count))

    def test_failed_dismissal_requires_three_more_clean_observations(self):
        p = make_provider(drain_seconds=2); seen = []; dismissed = []
        def rpc(ref, op="inspect"):
            if op == "dismiss":
                dismissed.append(len(seen))
                return {"dismissed": len(dismissed) > 1}
            seen.append(1)
            return {"state": "gone", "job_ended": True, "members": []}
        with patch.object(p, "_rpc", side_effect=rpc), patch.object(p, "_directory", return_value=Mock(exists=lambda: False)), \
             patch.object(provider, "process", side_effect=FileNotFoundError), patch.object(provider.time, "sleep"), \
             patch.object(provider.time, "monotonic", side_effect=[i / 100 for i in range(400)]):
            self.assertEqual("verified_clean", p._verdict(REF)[0])
        self.assertEqual([3, 6], dismissed)

    def test_unproven_collection_is_unknown_and_retains_authority(self):
        p = make_provider(drain_seconds=2); p.created.add(REF)
        def rpc(ref, op="inspect"):
            return {"dismissed": True} if op == "dismiss" else {"state": "gone", "job_ended": True, "members": []}
        with patch.object(p, "_rpc", side_effect=rpc), patch.object(provider, "process", return_value={"start": 456, "state": "S"}), \
             patch.object(provider.time, "sleep"), patch.object(provider.time, "monotonic", side_effect=[i / 10 for i in range(200)]):
            self.assertEqual("unknown", p._verdict(REF)[0])
        self.assertIn(REF, p.created)

    def test_stop_socket_budget_includes_zero_grace_floor(self):
        p = make_provider()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); ref = REF.rsplit(":", 1)[0] + ":" + str(directory.stat().st_ino)
            with patch.object(p, "_host"), patch.object(p, "_directory", return_value=directory), \
                 patch.object(provider, "process", return_value={"start": 456}), \
                 patch.object(provider, "peer", return_value=(123, os.getuid(), 1)), \
                 patch.object(provider, "receive", return_value={"ref": ref}), \
                 patch.object(provider.socket, "socket") as factory:
                p._rpc(ref, "stop", grace=0)
                factory.return_value.__enter__.return_value.settimeout.assert_called_once_with(
                    provider.RPC_SECONDS + native.STOP_DRAIN_FLOOR + 1)

    def test_stop_refuses_uncreated_authority_before_rpc(self):
        p = make_provider()
        with patch.object(p, "_rpc") as rpc, self.assertRaises(provider.RentalError):
            p.stop({"ref": REF, "claim_id": CLAIM, "fence": 1}, 0)
        rpc.assert_not_called()

    def test_stop_zero_means_immediate_bound_pidfd_operation(self):
        p = make_provider(); p.created.add(REF)
        with patch.object(p, "_rpc", return_value={"state": "gone"}) as rpc:
            p.stop({"ref": REF, "claim_id": CLAIM, "fence": 1}, 0)
            rpc.assert_called_once_with(REF, "stop", grace=0)

    def test_bad_release_descriptor_never_starts_supervisor(self):
        p = make_provider()
        with tempfile.TemporaryFile() as f, patch.object(p, "_host"), patch.object(provider.subprocess, "Popen") as launch:
            with self.assertRaises(provider.RentalError):
                p.container(CLAIM, 1, [sys.executable, "-c", "pass"], {}, f.fileno())
            launch.assert_not_called()

    def test_empty_journal_has_no_clean_proof(self):
        self.assertEqual("unknown", make_provider().reconcile_verdict([], CLAIM, 1)[0])

    def test_environment_allowlist_refuses_management_tokens_even_if_explicit(self):
        data = {"PATH": "/bin", "HOME": "/tmp", "POSTMQ_X": "dummy", "JUPYTER_TOKEN": "dummy",
                "CONTAINER_API_KEY": "dummy", "APPLICATION_FLAG": "yes", "UNLISTED_TOKEN": "dummy"}
        self.assertEqual({"PATH": "/bin", "HOME": "/tmp", "APPLICATION_FLAG": "yes"},
                         native.job_environment(data, ["APPLICATION_FLAG", "JUPYTER_TOKEN", "POSTMQ_X"]))

    def test_nonblocking_release_pipe_never_launches(self):
        p = make_provider(); read, write = os.pipe()
        try:
            os.set_blocking(read, False)
            with patch.object(p, "_host"), patch.object(provider.subprocess, "Popen") as launch:
                with self.assertRaises(provider.RentalError):
                    p.container(CLAIM, 1, ["/bin/true"], {}, read)
                launch.assert_not_called()
        finally:
            os.close(read); os.close(write)

    def test_container_pid_namespace_cannot_clear_device(self):
        self.assertEqual("unknown", provider.clean_verdict(BASE, {"state": "gone", "job_ended": True},
            Device(inventory_namespace_qualified=False).snapshot(), memory_qualified=True)[0])

    def test_pinned_control_precedes_uninspectable_child_threads(self):
        rows = {10: {"pid": 10, "ppid": 1, "start": 10, "state": "S"},
                11: {"pid": 11, "ppid": 10, "start": 11, "state": "S"}}
        action = Mock()
        def child_list(pid, *_):
            if pid == 10:
                return [11]
            raise native.NativeUnknown("fixture thread inventory too large")
        with patch.object(native, "process", side_effect=lambda p, *_: rows[p]), \
             patch.object(native, "children", side_effect=child_list):
            with self.assertRaises(native.NativeUnknown):
                with native.pinned_tree(10, opener=lambda *_: os.open(os.devnull, os.O_RDONLY),
                                        sender=Mock(), on_pin=action):
                    pass
        self.assertEqual(11, action.call_args.args[1]["pid"])

    def test_noncanonical_or_shared_native_root_refused(self):
        for root in ("relative", "/tmp/../tmp/x", "/" + "x" * 100):
            with self.assertRaises(ValueError):
                provider.ProcscanProvider(BASE, state_root=root, required_bytes=1, reserve_bytes=1, probe=Device())

    def test_ancestry_change_never_exposes_signal_handles(self):
        rows = {10: {"pid": 10, "ppid": 1, "start": 10, "state": "S"},
                11: {"pid": 11, "ppid": 999, "start": 11, "state": "S"}}
        send = Mock()
        with patch.object(native, "process", side_effect=lambda p, *_: rows[p]), \
             patch.object(native, "children", side_effect=lambda p, *_: [11] if p == 10 else []):
            with self.assertRaises(native.NativeUnknown):
                with native.pinned_tree(10, opener=lambda *_: os.open(os.devnull, os.O_RDONLY), sender=send):
                    self.fail("foreign ancestry exposed")
        self.assertFalse(any(c.args[1] != 0 for c in send.call_args_list))

    def test_scheduler_state_change_does_not_change_birth_identity(self):
        calls = {}
        def seen(pid, *_):
            calls[pid] = calls.get(pid, 0) + 1
            return {"pid": pid, "ppid": 1 if pid == 10 else 10, "start": pid,
                    "state": "R" if calls[pid] == 1 else "S"}
        with patch.object(native, "process", side_effect=seen), \
             patch.object(native, "children", side_effect=lambda p, *_: [11] if p == 10 else []):
            with native.pinned_tree(10, opener=lambda *_: os.open(os.devnull, os.O_RDONLY), sender=Mock()) as pins:
                self.assertEqual([11], [r["pid"] for _, r in pins])

    def test_birth_change_refuses_pinned_tree(self):
        calls = {}
        def seen(pid, *_):
            calls[pid] = calls.get(pid, 0) + 1
            return {"pid": pid, "ppid": 1 if pid == 10 else 10,
                    "start": pid + (1 if pid == 11 and calls[pid] > 1 else 0), "state": "S"}
        with patch.object(native, "process", side_effect=seen), \
             patch.object(native, "children", side_effect=lambda p, *_: [11] if p == 10 else []):
            with self.assertRaises(native.NativeUnknown):
                with native.pinned_tree(10, opener=lambda *_: os.open(os.devnull, os.O_RDONLY), sender=Mock()):
                    pass

    def test_rpc_frames_reject_trailing_or_oversized_payload(self):
        for data in (b'{"x":1}\nextra', b'[]\n', b'x' * 100):
            a, b = socket.socketpair()
            try:
                b.sendall(data); b.shutdown(socket.SHUT_WR)
                with self.assertRaises((native.NativeUnknown, ValueError)):
                    native.receive(a, 32)
            finally:
                a.close(); b.close()


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("POSTMQ_RENTAL_NATIVE_TESTS") == "1",
                     "opt-in reviewed Linux fixture only; simulated device")
class NativeRentalTests(unittest.TestCase):
    def setUp(self):
        import guard
        guard.subreaper()  # Own fixture adopts a deliberately orphaned child after guard loss.
        self.root = Path(tempfile.mkdtemp(prefix="pqr-", dir="/tmp"))
        self.base = replace(BASE, boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.p = provider.ProcscanProvider(self.base, state_root=self.root, required_bytes=128,
                                          reserve_bytes=128, probe=Device(boot_id=self.base.boot_id), drain_seconds=2)
        self.handles = []

    def start(self, code, *, argv=None, env=None):
        read_fd, writer = os.pipe()
        self.writer = os.fdopen(writer, "wb", buffering=0)
        self.addCleanup(self.writer.close)
        try:
            h = self.p.container(CLAIM, 1, argv or [sys.executable, "-I", "-c", code],
                                 dict(os.environ) if env is None else env, read_fd)
            os.fstat(read_fd)  # It is the SDK's descriptor; provider must not close it.
        finally:
            os.close(read_fd)
        self.handles.append(h)
        return h

    def wait(self, fn):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = fn()
            if value:
                return value
            time.sleep(0.05)
        self.fail("owned native state did not reach expected boundary")

    def tearDown(self):
        # Never bypass an identity refusal. Direct dismiss is explicit own CPU
        # fixture cleanup only; it is never a clean receipt or admission reset.
        for h in self.handles:
            if h["ref"] not in self.p.created:
                continue  # A prior loop iteration already collected this exact ref.
            if not self.p._directory(CLAIM, 1).exists():
                continue
            self.p.stop(h, 1)
            self.wait(lambda: self.p.inspect(h).get("job_ended") and not self.p.inspect(h).get("members"))
            answer = self.p._rpc(h["ref"], "dismiss")
            self.assertTrue(answer["dismissed"])
            self.p.children[h["ref"]].wait(timeout=3)
        if self.root.exists():
            self.root.rmdir()

    def test_normal_exit_and_fresh_reference_inspection(self):
        h = self.start("raise SystemExit(23)")
        self.assertIsNone(self.p.inspect(h)["job_exit"])
        os.write(self.writer.fileno(), b"x")
        self.wait(lambda: self.p.inspect(h).get("job_exit") == 23)
        self.assertEqual("gone", self.p.inspect(h["ref"])["state"])
        self.assertEqual("verified_clean", self.p.release_receipt(h, "failed")["cleanup"])

    def test_eof_never_executes(self):
        marker = self.root / "must-not-exist"
        h = self.start("from pathlib import Path;Path(" + repr(str(marker)) + ").touch()")
        self.writer.close()
        self.wait(lambda: self.p.inspect(h).get("job_ended"))
        self.assertFalse(marker.exists())

    def test_stop_before_release_cannot_late_fork(self):
        marker = self.root / "must-not-exist"
        h = self.start("from pathlib import Path;Path(" + repr(str(marker)) + ").touch()")
        self.p.stop(h, 1)
        try:
            os.write(self.writer.fileno(), b"x")
        except BrokenPipeError:
            pass
        self.wait(lambda: self.p.inspect(h).get("job_ended"))
        self.assertFalse(marker.exists())

    def test_detached_orphan_is_supervised_and_pidfd_stopped(self):
        h = self.start("import os,time; p=os.fork(); "
                       "os._exit(42) if p else None; os.setsid(); time.sleep(60)")
        os.write(self.writer.fileno(), b"x")
        self.wait(lambda: self.p.inspect(h).get("job_exit") == 42)
        seen = self.p.inspect(h)
        self.assertEqual("alive", seen["state"])
        self.assertTrue(seen["members"])
        self.assertEqual("cleanup_failed", self.p.release_receipt(h, "failed")["cleanup"])
        self.p.stop(h, 1)
        self.wait(lambda: self.p.inspect(h).get("state") == "gone")

    def test_fresh_provider_reconciles_actual_sdk_entry(self):
        sdk_path = os.environ.get("POSTMQ_RENTAL_SDK")
        self.assertTrue(sdk_path, "selected real SDK path required for native recovery test")
        spec = importlib.util.spec_from_file_location("rental_test_sdk", sdk_path)
        sdk = importlib.util.module_from_spec(spec); sys.modules[spec.name] = sdk; spec.loader.exec_module(sdk)
        h = self.start("raise SystemExit(0)"); os.write(self.writer.fileno(), b"x")
        self.wait(lambda: self.p.inspect(h).get("job_ended"))
        fresh = provider.ProcscanProvider(self.base, state_root=self.root, required_bytes=128,
            reserve_bytes=128, probe=Device(boot_id=self.base.boot_id), drain_seconds=2)
        entry = sdk.parse_entry("launched " + CLAIM + " 1 " + h["ref"])
        self.assertEqual("verified_clean", fresh.reconcile_verdict([entry], CLAIM, 1)[0])
        self.assertFalse(self.p._directory(CLAIM, 1).exists())
        self.p.children[h["ref"]].wait(timeout=3)

    def live_multilevel(self):
        h = self.start("import os,time\np=os.fork()\nif p==0:\n os.setsid()\n q=os.fork()\n if q==0:os.setsid()\n time.sleep(60)\nelse:time.sleep(60)")
        os.write(self.writer.fileno(), b"x")
        self.wait(lambda: len(self.p.inspect(h).get("members", [])) == 3)
        self.wait(lambda: len({os.getsid(m["pid"]) for m in self.p.inspect(h).get("members", [])}) == 3)
        return h

    @staticmethod
    def dead_pins(pins):
        return all(NativeRentalTests.pin_dead(fd) for fd, _ in pins)

    @staticmethod
    def pin_dead(fd):
        poll = select.poll(); poll.register(fd, select.POLLIN)
        return bool(poll.poll(0))

    def test_zero_grace_stop_drains_live_multilevel_tree(self):
        h = self.live_multilevel()
        with native.pinned_tree(provider.parts(h["ref"])[3]) as pins:
            self.assertEqual(3, len(pins))
            self.p.stop(h, 0)
            self.wait(lambda: self.dead_pins(pins))
        self.assertEqual("verified_clean", self.p.release_receipt(h, "unknown")["cleanup"])

    def test_fresh_reconcile_drains_live_multilevel_tree(self):
        sdk_path = os.environ.get("POSTMQ_RENTAL_SDK"); self.assertTrue(sdk_path)
        spec = importlib.util.spec_from_file_location("rental_live_tree_sdk", sdk_path)
        sdk = importlib.util.module_from_spec(spec); sys.modules[spec.name] = sdk; spec.loader.exec_module(sdk)
        h = self.live_multilevel()
        fresh = provider.ProcscanProvider(self.base, state_root=self.root, required_bytes=128,
            reserve_bytes=128, probe=Device(boot_id=self.base.boot_id), drain_seconds=2)
        entry = sdk.parse_entry("launched " + CLAIM + " 1 " + h["ref"])
        with native.pinned_tree(provider.parts(h["ref"])[3]) as pins:
            self.assertEqual(3, len(pins))
            self.assertEqual("verified_clean", fresh.reconcile_verdict([entry], CLAIM, 1)[0])
            self.wait(lambda: self.dead_pins(pins))
        self.assertFalse(self.p._directory(CLAIM, 1).exists())
        self.p.children[h["ref"]].wait(timeout=3)

    def test_guard_loss_retains_unknown_and_never_controls_orphan(self):
        h = self.start("import os,time;p=os.fork();os._exit(42) if p else None;os.setsid();time.sleep(60)")
        os.write(self.writer.fileno(), b"x"); self.wait(lambda: self.p.inspect(h).get("job_exit") == 42)
        guard_pid = provider.parts(h["ref"])[3]
        with native.pinned_tree(guard_pid) as pins:
            self.assertEqual(1, len(pins))
            child_fd, row = pins[0]
            guard_fd = os.pidfd_open(guard_pid)
            try:
                self.assertEqual(provider.parts(h["ref"])[4], native.process(guard_pid)["start"])
                signal.pidfd_send_signal(guard_fd, signal.SIGKILL)
                self.p.children[h["ref"]].wait(timeout=3)
                self.assertEqual("unverifiable", self.p.inspect(h)["state"])
                self.assertEqual("unknown", self.p.release_receipt(h, "unknown")["cleanup"])
                from types import SimpleNamespace
                entry = SimpleNamespace(kind="launched", claim=CLAIM, fence=1, ref=h["ref"])
                self.assertEqual("unknown", self.p.reconcile_verdict([entry], CLAIM, 1)[0])
                signal.pidfd_send_signal(child_fd, 0)  # No fallback killed the original owned orphan.
            finally:
                # Explicit fixture authority pinned BEFORE the destructive fault;
                # never an adapter receipt or production recovery/readmission.
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                os.waitpid(row["pid"], 0)
                os.close(guard_fd)
                directory = self.p._directory(CLAIM, 1)
                self.assertEqual(provider.parts(h["ref"])[5], directory.stat().st_ino)
                (directory / "guard.sock").unlink(); directory.rmdir()
                self.handles.remove(h)

    def test_stop_under_child_churn(self):
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                h = self.start("import os\nwhile True:\n p=os.fork()\n if p==0:os._exit(0)\n os.waitpid(p,0)")
                os.write(self.writer.fileno(), b"x"); time.sleep(0.02)
                self.p.stop(h, 1)
                self.wait(lambda: self.p.inspect(h).get("state") == "gone")
                self.assertEqual("verified_clean", self.p.release_receipt(h, "unknown")["cleanup"])

    def test_stop_timeout_includes_five_second_grace(self):
        h = self.start("import time;time.sleep(60)"); os.write(self.writer.fileno(), b"x")
        with patch.object(provider, "RPC_SECONDS", 0.001):
            self.p.stop(h, 5)
        self.wait(lambda: self.p.inspect(h).get("state") == "gone")

    def test_identity_refusal_before_publication_never_executes(self):
        marker = self.root / "must-not-exist"; read, write = os.pipe()
        try:
            with patch.object(self.p, "_rpc", side_effect=provider.RentalError("fixture identity refusal")):
                with self.assertRaises(provider.RentalError):
                    self.p.container(CLAIM, 1, [sys.executable, "-c", "open(" + repr(str(marker)) + ",'w').close()"], {}, read)
            os.fstat(read)
            self.assertFalse(marker.exists())
            self.assertFalse(self.p._directory(CLAIM, 1).exists())
            self.assertFalse(self.p.failed_children)
        finally:
            os.close(read); os.close(write)

    def test_failed_exec_is_terminal_and_cleanup_observable(self):
        h = self.start("", argv=["/no-such-postmq-fixture-executable"])
        os.write(self.writer.fileno(), b"x")
        self.wait(lambda: self.p.inspect(h).get("job_ended"))
        self.assertTrue(self.p.inspect(h)["job_failed_to_start"])
        self.assertEqual(126, self.p.inspect(h)["job_exit"])
        self.assertEqual("verified_clean", self.p.release_receipt(h, "failed")["cleanup"])

    def test_job_environment_drops_management_tokens(self):
        output = self.root / "environment-keys.json"
        h = self.start("import os,json;open(" + repr(str(output)) + ",'w').write(json.dumps(sorted(os.environ)))",
                       env={"PATH": "/bin:/usr/bin", "POSTMQ_X": "dummy", "CONTAINER_API_KEY": "dummy",
                            "JUPYTER_TOKEN": "dummy", "UNLISTED_TOKEN": "dummy"})
        os.write(self.writer.fileno(), b"x"); self.wait(lambda: self.p.inspect(h).get("job_exit") == 0)
        names = json.loads(output.read_text()); output.unlink()
        self.assertFalse(set(names) & {"POSTMQ_X", "CONTAINER_API_KEY", "JUPYTER_TOKEN", "UNLISTED_TOKEN"})


if __name__ == "__main__":
    unittest.main()
