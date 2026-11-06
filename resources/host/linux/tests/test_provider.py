"""CPU tests only. Fake kernel evidence is never a native fixture run log."""

from dataclasses import asdict, replace
from pathlib import Path
import json
import errno
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from evidence import Baseline, cleanup_verdict, memory_returned
from linux_cgroup import LinuxCgroupProvider, ProviderError
from probe import NvidiaProbe, ProbeError


CLAIM = "01AAAAAAAAAAAAAAAAAAAAAA01"
BOOT = "aaaaaaaa-aaaa-4aaa-8aaa-000000000003"
OTHER_BOOT = "aaaaaaaa-aaaa-4aaa-8aaa-000000000008"
GPU = "GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000003"
BASELINE = Baseline(BOOT, GPU, 800 * 1024 ** 2, 1024 ** 3, 16 * 1024 ** 2,
                    "2026-10-27T02:00:00.000Z")


def sample(**updates):
    value = {"observed_at": "2026-10-27T02:00:01.000Z", "boot_id": BOOT, "gpu_uuid": GPU,
             "free_bytes": BASELINE.free_bytes, "total_bytes": BASELINE.total_bytes,
             "unknown_gpu_pids": []}
    return {**value, **updates}


class FakeProbe:
    def __init__(self):
        self.value = sample()
        self.calls = []

    def snapshot(self, owned_cgroup=None):
        self.calls.append(owned_cgroup)
        if isinstance(self.value, Exception):
            raise self.value
        return dict(self.value)


class FakeChild:
    pid = 321

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class NativeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.root = self.dir / "cgroup"
        self.root.mkdir()
        (self.root / "cgroup.controllers").write_text("memory pids")
        self.proc = self.dir / "proc"
        bootfile = self.proc / "sys/kernel/random/boot_id"
        bootfile.parent.mkdir(parents=True)
        bootfile.write_text(BOOT)
        self.path = self.root / "user.slice/user-1000.slice/user@1000.service/app.slice" / (
            "postmq-claim-" + CLAIM + ".scope")
        self.probe = FakeProbe()
        self.time = 0.0
        self.launched = []
        self.child = FakeChild()
        self.provider = LinuxCgroupProvider(
            BASELINE, required_bytes=100, reserve_bytes=100, charge_tolerance=10,
            drain_seconds=0.1, sample_interval=0.01, stable_samples=2,
            cgroup_root=self.root, proc_root=self.proc, probe=self.probe,
            runner=self.run_command, popen=self.launch, uid=1000,
            clock=lambda: self.time, sleep=self.sleep)

    def sleep(self, seconds):
        self.time += seconds

    def run_command(self, argv):
        if "--property=Version" in argv:
            return "Version=259\n"
        if not self.path.exists():
            return "LoadState=not-found\nControlGroup=\n"
        return "LoadState=loaded\nControlGroup=/" + str(self.path.relative_to(self.root)) + "\n"

    def make_scope(self, *, pids="", populated=0, current=0, oom=0):
        self.path.mkdir(parents=True, exist_ok=True)
        for name, text in {"cgroup.procs": pids, "cgroup.events": "populated " + str(populated),
                           "cgroup.kill": "", "memory.current": str(current), "memory.peak": "128",
                           "memory.events": "oom 0\noom_kill " + str(oom)}.items():
            (self.path / name).write_text(text + "\n")

    def launch(self, args, **kwargs):
        self.launched.append((args, kwargs))
        self.make_scope(pids="321", populated=1)
        return self.child

    def handle(self, *, owned=True):
        ref = "scope:" + CLAIM + ":7:" + BOOT + ":" + str(self.path.stat().st_ino)
        if owned:
            self.provider.owned.add(ref)
            self.provider.created[ref] = None
        return {"ref": ref, "claim_id": CLAIM, "fence": 7, "pid": 321}

    def test_declaration_is_json_safe_and_not_exclusive_physical_ownership(self):
        decl = self.provider.declare()
        json.dumps(decl)
        self.assertEqual("advisory", decl["enforcement"]["ownership"])
        self.assertEqual("advisory", decl["enforcement"]["descendant_supervision"])
        self.assertEqual("advisory", decl["enforcement"]["revocation"])
        self.assertEqual("advisory", decl["enforcement"]["memory_accounting"])
        self.assertEqual(GPU, decl["provider_host_key"])

    def test_preflight_requires_current_granted_claim(self):
        self.assertEqual("admitted", self.provider.preflight({"claim_id": CLAIM, "fence": 7,
                                                               "state": "granted"})["result"])
        for state in ["admitted", "released", "queued", None]:
            self.assertEqual("refused", self.provider.preflight({"claim_id": CLAIM, "fence": 7,
                                                                 "state": state})["result"])

    def test_preflight_unknown_process_refuses_without_calling_it_clean(self):
        self.probe.value = sample(unknown_gpu_pids=[999])
        result = self.provider.preflight({"claim_id": CLAIM, "fence": 7, "state": "granted"})
        self.assertEqual("unknown_gpu_occupancy", result["reason"])

    def test_preflight_memory_deficit_is_residue(self):
        self.probe.value = sample(free_bytes=1)
        self.assertEqual("residue", self.provider.preflight({"claim_id": CLAIM, "fence": 7,
                                                             "state": "granted"})["cleanup"])

    def test_preflight_headroom_separate_from_baseline(self):
        self.provider.required = BASELINE.free_bytes
        result = self.provider.preflight({"claim_id": CLAIM, "fence": 7, "state": "granted"})
        self.assertEqual("insufficient_headroom", result["reason"])

    def test_read_failure_refuses_and_does_not_echo_secret(self):
        self.probe.value = ProbeError("secret-value-never-print")
        result = self.provider.preflight({"claim_id": CLAIM, "fence": 7, "state": "granted"})
        self.assertEqual("preflight_unknown", result["reason"])
        self.assertNotIn("secret-value", json.dumps(result))

    def test_container_verifies_scope_before_returning_and_strips_credentials(self):
        handle = self.provider.container(CLAIM, 7, ["/bin/true"],
                                         {"PATH": "/bin", "POSTMQ_CREDENTIAL": "never-child",
                                          "POSTMQ_CREDENTIAL_FILE": "never-file"}, 12)
        json.dumps(handle)
        args, kwargs = self.launched[0]
        self.assertIn("--scope", args)
        self.assertNotIn("--service", args)
        self.assertEqual((12,), kwargs["pass_fds"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertEqual({"PATH", "CUDA_VISIBLE_DEVICES", "XDG_RUNTIME_DIR"}, set(kwargs["env"]))
        self.assertEqual(GPU, kwargs["env"]["CUDA_VISIBLE_DEVICES"])
        self.assertEqual("alive", self.provider.inspect(self.provider.journal_ref(handle))["state"])

    def test_existing_scope_is_never_adopted_by_launch(self):
        self.make_scope(pids="999", populated=1)
        with self.assertRaises(ProviderError):
            self.provider.container(CLAIM, 7, ["true"], {}, 12)
        self.assertFalse(self.launched)

    def test_ignored_sigchld_refuses_before_scope_creation(self):
        with mock.patch("linux_cgroup.signal.getsignal", return_value=signal.SIG_IGN):
            with self.assertRaises(ProviderError):
                self.provider.container(CLAIM, 7, ["true"], {}, 12)
        self.assertFalse(self.launched)

    def test_drain_window_keeps_sdk_budget_margin(self):
        with self.assertRaises(ValueError):
            LinuxCgroupProvider(BASELINE, required_bytes=1, reserve_bytes=1, drain_seconds=30)

    def test_stdio_cannot_be_the_release_pipe(self):
        for fd in (0, 1, 2):
            with self.assertRaises(ValueError):
                self.provider.container(CLAIM, 7, ["true"], {}, fd)
        self.assertFalse(self.launched)

    def test_handle_must_match_claim_and_fence(self):
        self.make_scope()
        handle = self.handle()
        handle["fence"] = 8
        with self.assertRaises(ProviderError):
            self.provider.journal_ref(handle)

    def test_orphan_in_descendant_cgroup_blocks_release(self):
        self.make_scope(populated=1)
        child = self.path / "nested"
        child.mkdir()
        (child / "cgroup.procs").write_text("998\n")
        handle = self.handle()
        self.assertEqual([998], self.provider.inspect(handle)["pids"])
        self.assertEqual("cleanup_failed", self.provider.release_receipt(handle, "completed")["cleanup"])

    def test_empty_and_stable_device_return_is_clean(self):
        self.make_scope()
        receipt = self.provider.release_receipt(self.handle(), "completed")
        self.assertEqual("verified_clean", receipt["cleanup"])
        self.assertEqual(2, len(receipt["telemetry_tail"]))

    def test_oom_and_real_memory_deficit_are_retained(self):
        self.make_scope(oom=1)
        handle = self.handle()
        self.probe.value = sample(free_bytes=1)
        self.assertEqual(1, self.provider.inspect(handle)["oom_kill"])
        self.assertEqual("residue", self.provider.release_receipt(handle, "failed")["cleanup"])

    def test_memory_charge_without_processes_is_residue(self):
        self.make_scope(current=11)
        self.assertEqual("residue", self.provider.release_receipt(self.handle(), "failed")["cleanup"])

    def test_missing_counter_is_unknown(self):
        self.make_scope()
        handle = self.handle()
        (self.path / "memory.current").unlink()
        self.assertEqual("unknown", self.provider.release_receipt(handle, "completed")["cleanup"])

    def test_live_process_dominates_missing_counter_and_device_failure(self):
        self.make_scope(pids="777", populated=1)
        handle = self.handle()
        (self.path / "memory.current").unlink()
        self.probe.value = ProbeError("cannot query")
        self.assertEqual("alive", self.provider.inspect(handle)["state"])
        self.assertEqual("cleanup_failed", self.provider.release_receipt(handle, "failed")["cleanup"])

    def test_collected_scope_is_unknown_not_zero_memory(self):
        self.make_scope()
        handle = self.handle()
        shutil.rmtree(self.path)
        self.assertEqual("unverifiable", self.provider.inspect(handle)["state"])
        self.assertEqual("unknown", self.provider.release_receipt(handle, "completed")["cleanup"])

    def test_scope_replacement_is_not_touched(self):
        self.make_scope()
        handle = self.handle()
        old = self.path.with_name("retired.scope")
        self.path.rename(old)
        self.make_scope(pids="999", populated=1)
        with self.assertRaises(ProviderError):
            self.provider.stop(handle, 0.1)
        self.assertEqual("\n", (self.path / "cgroup.kill").read_text())

    def test_forged_handle_cannot_stop_an_unowned_scope(self):
        self.make_scope()
        with self.assertRaises(ProviderError):
            self.provider.stop(self.handle(owned=False), 0.1)
        self.assertEqual("\n", (self.path / "cgroup.kill").read_text())

    def test_stop_writes_only_bound_cgroup_kill(self):
        self.make_scope()
        handle = self.handle()
        self.assertEqual("gone", self.provider.stop(handle, 0.1)["state"])
        self.assertTrue((self.path / "cgroup.kill").read_text().startswith("1"))

    def test_zero_grace_kills_then_returns_one_observation(self):
        self.make_scope(pids="321", populated=1)
        handle = self.handle()
        self.assertEqual("alive", self.provider.stop(handle, 0)["state"])
        self.assertTrue((self.path / "cgroup.kill").read_text().startswith("1"))
        self.assertEqual(0, self.time)
        for value in [True, -1, float("inf"), float("nan"), "0", None]:
            with self.assertRaises(ValueError):
                self.provider.stop(handle, value)

    def test_concurrent_inspect_and_collection_do_not_make_stop_raise(self):
        self.make_scope()
        handle = self.handle()
        entered, proceed, inspecting = threading.Event(), threading.Event(), threading.Event()
        collected = threading.Event()
        results, errors = {}, []
        kill = self.provider._kill_bound
        def paused_kill(*args):
            entered.set()
            if not proceed.wait(2):
                raise RuntimeError("test barrier timed out")
            kill(*args)
        self.provider._kill_bound = paused_kill
        def work(name):
            try:
                if name == "inspect":
                    inspecting.set()
                    if not collected.wait(2):
                        raise RuntimeError("test collection barrier timed out")
                    results[name] = self.provider.inspect(handle)
                else:
                    results[name] = self.provider.stop(handle, 0)
            except BaseException as error:
                errors.append(error)
        stop = threading.Thread(target=work, args=("stop",))
        inspect = threading.Thread(target=work, args=("inspect",))
        stop.start()
        try:
            self.assertTrue(entered.wait(2))
            inspect.start()
            self.assertTrue(inspecting.wait(2))
            shutil.rmtree(self.path)  # Simulate kernel collection, not a PID kill.
        finally:
            collected.set()
            proceed.set()
            stop.join(3)
            if inspect.ident is not None:
                inspect.join(3)
        self.assertFalse(stop.is_alive())
        self.assertFalse(inspect.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("scope_collected", results["stop"]["reason"])
        self.assertEqual("scope_collected", results["inspect"]["reason"])

    def test_boot_change_never_signals_or_reports_clean(self):
        self.make_scope()
        handle = self.handle()
        (self.proc / "sys/kernel/random/boot_id").write_text(OTHER_BOOT)
        with self.assertRaises(ProviderError):
            self.provider.stop(handle, 0.1)
        self.assertEqual("unknown", self.provider.release_receipt(handle, "completed")["cleanup"])

    def test_empty_journal_is_unknown(self):
        outcome, _, _ = self.provider.reconcile_verdict([], CLAIM, 7)
        self.assertEqual("unknown", outcome)

    def test_live_scope_dominates_absent_journal(self):
        self.make_scope(pids="999", populated=1)
        outcome, _, _ = self.provider.reconcile_verdict([], CLAIM, 7)
        self.assertEqual("cleanup_failed", outcome)
        self.assertEqual("\n", (self.path / "cgroup.kill").read_text())

    def test_reconcile_accepts_bare_journal_ref(self):
        self.make_scope()
        handle = self.handle()
        entries = [{"kind": "launched", "claim": CLAIM, "fence": 7, "journal_ref": handle["ref"]}]
        self.assertEqual("verified_clean", self.provider.reconcile_verdict(entries, CLAIM, 7)[0])
        self.assertEqual("unknown", self.provider.reconcile_verdict(entries, CLAIM, 8)[0])

    def test_symlinked_scope_cannot_escape(self):
        self.make_scope()
        handle = self.handle()
        moved = self.path.with_name("elsewhere")
        self.path.rename(moved)
        self.path.symlink_to(moved, target_is_directory=True)
        self.assertEqual("unverifiable", self.provider.inspect(handle)["state"])
        with self.assertRaises(ProviderError):
            self.provider.stop(handle, 0.1)

    def test_telemetry_reports_current_identity_and_observation_time(self):
        value, health = self.provider.telemetry()
        self.assertEqual("healthy", health)
        self.assertEqual(GPU, value["gpu_uuid"])
        self.assertIn("observed_at", value)

    def test_owned_running_job_is_not_failed_memory_return(self):
        self.make_scope(pids="321", populated=1)
        self.handle()
        self.probe.value = sample(free_bytes=1)
        value, health = self.provider.telemetry()
        self.assertEqual("unknown", health)
        self.assertEqual("owned", value["occupancy"])
        self.assertEqual('/' + str(self.path.relative_to(self.root)), self.probe.calls[-1])

    def test_collected_owned_references_are_pruned(self):
        self.make_scope()
        handle = self.handle()
        shutil.rmtree(self.path)
        self.provider.telemetry()
        self.assertNotIn(handle["ref"], self.provider.owned)
        self.assertEqual("scope_collected", self.provider.stop(handle, 0.1)["reason"])

    def test_malformed_handles_stay_unverifiable(self):
        for value in [None, 4, [], {}, {"ref": 3}, {"claim_id": CLAIM, "fence": True}]:
            self.assertEqual("unverifiable", self.provider.inspect(value)["state"])
            with self.assertRaises(ProviderError):
                self.provider.stop(value, 0.1)

    def test_unverifiable_owned_scope_does_not_become_bad_health(self):
        self.make_scope()
        self.handle()
        self.provider.runner = lambda _args: "not a service-manager observation"
        value, health = self.provider.telemetry()
        self.assertEqual("unknown", health)
        self.assertEqual("unknown", value["occupancy"])

    def test_telemetry_scope_scan_leaves_device_probe_budget(self):
        self.make_scope()
        handle = self.handle()
        self.provider.owned.add(handle["ref"].replace(":7:", ":8:"))
        runner = self.provider.runner
        def slow(argv):
            self.time += 2
            return runner(argv)
        self.provider.runner = slow
        value, health = self.provider.telemetry()
        self.assertEqual("unknown", health)
        self.assertEqual("unknown", value["occupancy"])
        self.assertEqual([], self.probe.calls)
        self.assertLess(self.time, 10)

    def test_native_commands_and_probe_run_outside_metadata_lock(self):
        def unlocked():
            got = []
            def check():
                held = self.provider._process_lock.acquire(timeout=0.1)
                got.append(held)
                if held:
                    self.provider._process_lock.release()
            worker = threading.Thread(target=check)
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual([True], got)
        runner, snapshot = self.provider.runner, self.probe.snapshot
        def checked_command(argv):
            unlocked()
            return runner(argv)
        def checked_snapshot(*args):
            unlocked()
            return snapshot(*args)
        self.provider.runner, self.probe.snapshot = checked_command, checked_snapshot
        handle = self.provider.container(CLAIM, 7, ["true"], {}, 12)
        self.provider.inspect(handle)
        self.provider.telemetry()
        self.provider.stop(handle, 0)
        self.provider.release_receipt(handle, "failed")

    def test_occupancy_prune_between_path_and_authorization_does_not_revoke_creation(self):
        self.make_scope()
        handle = self.handle()
        lookup = self.provider._path_from_ref
        def pruned(ref):
            result = lookup(ref)
            with self.provider._process_lock:
                self.provider.owned.discard(ref)
            return result
        self.provider._path_from_ref = pruned
        self.assertEqual("gone", self.provider.stop(handle, 0)["state"])
        self.assertTrue((self.path / "cgroup.kill").read_text().startswith("1"))

    def test_enodev_control_is_reobserved_and_live_scope_is_not_success(self):
        for live in (False, True):
            self.make_scope(pids="321" if live else "", populated=int(live))
            handle = self.handle()
            with mock.patch("linux_cgroup.os.write", side_effect=OSError(errno.ENODEV, "device gone")):
                if live:
                    with self.assertRaises(ProviderError):
                        self.provider.stop(handle, 0)
                else:
                    self.assertEqual("gone", self.provider.stop(handle, 0)["state"])

    def test_inactive_creation_history_is_bounded_without_dropping_live_refs(self):
        self.provider.created = {str(n): None for n in range(100)}
        self.provider.owned = {"0"}
        self.provider._reap()
        self.assertEqual(65, len(self.provider.created))
        self.assertIn("0", self.provider.created)

    def test_dead_children_are_reaped_and_metadata_bounded(self):
        class Exited:
            def poll(self):
                return 0
        self.provider.children = {pid: Exited() for pid in range(100)}
        self.provider.jobs = {str(pid): Exited() for pid in range(100)}
        self.provider.telemetry()
        self.assertEqual({}, self.provider.children)
        self.assertEqual({}, self.provider.jobs)
        self.assertEqual(64, len(self.provider.completed))

    def test_job_status_is_bound_to_ref_and_does_not_trust_handle_pid(self):
        handle = self.provider.container(CLAIM, 7, ["true"], {}, 12)
        self.assertIsNone(self.provider.inspect(handle)["job_exit"])
        self.child.code = 17
        handle["pid"] = 999  # Never used to infer the primary job's exit.
        self.assertEqual(17, self.provider.inspect(handle)["job_exit"])
        self.assertIsNone(self.provider.inspect(handle)["job_signal"])
        self.assertEqual(17, self.provider.inspect(handle["ref"])["job_exit"])
        # Native cleanup evidence remains alive independently of primary exit.
        self.assertEqual("alive", self.provider.inspect(handle)["state"])
        other = dict(handle, ref=handle["ref"].replace(":7:", ":8:"), fence=8)
        self.assertIsNone(self.provider.inspect(other)["job_exit"])

    def test_known_primary_signal_does_not_fabricate_clean_scope(self):
        handle = self.provider.container(CLAIM, 7, ["true"], {}, 12)
        self.child.code = -15
        shutil.rmtree(self.path)
        seen = self.provider.inspect(handle)
        self.assertEqual(15, seen["job_signal"])
        self.assertIsNone(seen["job_exit"])
        self.assertEqual("unverifiable", seen["state"])
        self.assertEqual("unknown", self.provider.release_receipt(handle, "failed")["cleanup"])

    def test_fresh_provider_does_not_infer_job_exit_from_empty_scope(self):
        self.make_scope()
        seen = self.provider.inspect(self.handle())
        self.assertEqual("gone", seen["state"])
        self.assertIsNone(seen["job_exit"])
        self.assertIsNone(seen["job_signal"])


class EvidenceTests(unittest.TestCase):
    def test_boundary_comparison_and_bool_rejection(self):
        self.assertTrue(memory_returned(BASELINE, sample(free_bytes=BASELINE.free_bytes-BASELINE.tolerance_bytes)))
        self.assertFalse(memory_returned(BASELINE, sample(free_bytes=BASELINE.free_bytes-BASELINE.tolerance_bytes-1)))
        self.assertIsNone(memory_returned(BASELINE, sample(free_bytes=True)))
        for key, value in [("boot_id", OTHER_BOOT), ("gpu_uuid", "wrong"), ("total_bytes", 1)]:
            self.assertIsNone(memory_returned(BASELINE, sample(**{key: value})))

    def test_live_scope_dominates_missing_device_counter(self):
        verdict, _ = cleanup_verdict(BASELINE, {"state": "alive"}, {}, 0)
        self.assertEqual("cleanup_failed", verdict)

    def test_invalid_baseline_refused(self):
        for update in [{"tolerance_bytes": BASELINE.free_bytes}, {"free_bytes": True}, {"boot_id": "wrong"},
                       {"gpu_uuid": "GPU-" + "-" * 36}]:
            with self.assertRaises((ValueError, TypeError)):
                replace(BASELINE, **update)


class TrampolineTests(unittest.TestCase):
    def released_job(self, argv, env=None):
        reader, writer = os.pipe()
        os.write(writer, b"x"); os.close(writer)
        try:
            return subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("trampoline.py")),
                                   str(reader)] + argv, pass_fds=(reader,), env=env,
                                  capture_output=True, timeout=5)
        finally:
            os.close(reader)

    def test_native_sigpipe_is_restored_and_exec_error_is_quiet(self):
        result = self.released_job(["/bin/sh", "-c", "kill -PIPE $$; exit 99"])
        self.assertEqual(-signal.SIGPIPE, result.returncode)
        result = self.released_job(["/missing-private-command-never-echo"])
        self.assertEqual(126, result.returncode)
        self.assertEqual(b"", result.stderr)
        result = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("trampoline.py")),
                                 "-1", "/missing-private-command-never-echo"], capture_output=True, timeout=5)
        self.assertEqual(125, result.returncode)
        self.assertEqual(b"", result.stderr)

    def test_manager_and_credential_variables_do_not_reach_job(self):
        keys = ["POSTMQ_CREDENTIAL", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "DBUS_SYSTEM_BUS_ADDRESS", "SSH_AUTH_SOCK", "NOTIFY_SOCKET"]
        env = dict(os.environ, **dict.fromkeys(keys, "test-only-sentinel"))
        code = "import os,sys; sys.exit(int(any(k in os.environ for k in sys.argv[1:])))"
        result = self.released_job([sys.executable, "-I", "-c", code] + keys, env)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_no_exec_until_release_and_eof_never_executes(self):
        for release in [False, True]:
            with self.subTest(release=release), tempfile.TemporaryDirectory() as temp:
                marker = Path(temp) / "ran"
                read_fd, write_fd = os.pipe()
                child = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("trampoline.py")),
                                          str(read_fd), sys.executable, "-I", "-c",
                                          "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
                                          str(marker)], pass_fds=(read_fd,))
                os.close(read_fd)
                self.assertFalse(marker.exists())
                if release:
                    os.write(write_fd, b"x")
                os.close(write_fd)
                self.assertEqual(0 if release else 125, child.wait(timeout=5))
                self.assertEqual(release, marker.exists())


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)
        path = self.proc / "sys/kernel/random/boot_id"
        path.parent.mkdir(parents=True)
        path.write_text(BOOT)
        self.apps = ""
        self.device = GPU + ", 800, 1024\n"
        self.commands = []
        self.probe = NvidiaProbe(GPU, proc_root=self.proc, runner=self.runner)

    def runner(self, argv):
        self.commands.append(argv)
        return self.apps if any("query-compute" in x for x in argv) else self.device

    def test_snapshot_binds_uuid_boot_and_bytes(self):
        value = self.probe.snapshot()
        self.assertEqual(BASELINE.free_bytes, value["free_bytes"])
        self.assertEqual(BOOT, value["boot_id"])
        self.assertEqual([], value["unknown_gpu_pids"])
        self.assertTrue(all(GPU in args for args in self.commands))

    def test_gpu_occupant_must_be_in_own_scope_subtree(self):
        self.apps = "555, " + GPU + "\n"
        (self.proc / "555").mkdir()
        (self.proc / "555/cgroup").write_text("0::/owned/nested\n")
        self.assertEqual([], self.probe.snapshot("/owned")["unknown_gpu_pids"])
        self.assertEqual([555], self.probe.snapshot("/other")["unknown_gpu_pids"])
        self.assertEqual([555], self.probe.snapshot()["unknown_gpu_pids"])

    def test_racing_unreadable_process_is_unknown(self):
        self.apps = "555, " + GPU + "\n"
        with self.assertRaises(ProbeError):
            self.probe.snapshot()

    def test_invalid_device_inventory_never_becomes_zero(self):
        for device in ["", "other, 800, 1024", GPU + ", N/A, 1024", self.device + self.device]:
            self.device = device
            with self.assertRaises(ProbeError):
                self.probe.snapshot()


if __name__ == "__main__":
    unittest.main()
