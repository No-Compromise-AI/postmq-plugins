"""Offline public-source assembly and synthetic provider controls."""
import hashlib
import contextlib
import importlib.util
import ipaddress
import json
import plistlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import types

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REFERENCE_UUIDS = {
    # Public Microsoft protocol-document URL, not an installation identifier.
    "96702c7d-6f5f-4965-8f29-5fdbea73278e",
}
spec = importlib.util.spec_from_file_location("public_adapter_builder", ROOT / "tools/build_resource_adapter.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SourceBundles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()

    def test_every_target_assembles_from_public_sources_and_verifies(self):
        for target in builder.CATALOG:
            with self.subTest(target=target):
                output = self.parent / target
                result = builder.assemble(target, output)
                manifest = builder.verify(output, result["manifest_sha256"])
                self.assertEqual(manifest["target"], target)
                self.assertFalse(result["installed"])
                self.assertFalse(result["resource_registered"])
                self.assertFalse(result["job_started"])
                for file in output.glob("*.py"):
                    compile(file.read_bytes(), file.name, "exec")

    def test_source_tampering_is_refused_even_when_manifest_stays_pinned(self):
        output = self.parent / "macos"
        result = builder.assemble("macos", output)
        provider = output / "provider.py"
        provider.write_bytes(provider.read_bytes() + b"\n# changed source\n")
        with self.assertRaisesRegex(ValueError, "adapter source changed: provider.py"):
            builder.verify(output, result["manifest_sha256"])

    def test_rewritten_manifest_does_not_replace_an_external_pin(self):
        output = self.parent / "linux"
        result = builder.assemble("linux", output)
        path = output / "adapter-manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["adapter_version"] = "999.0.0"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "selected manifest changed"):
            builder.verify(output, result["manifest_sha256"])

    def test_existing_output_is_preserved_without_overwrite(self):
        output = self.parent / "existing"
        output.mkdir()
        marker = output / "owner-data.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(ValueError, "output already exists"):
            builder.assemble("windows", output)
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual([p.name for p in output.iterdir()], [marker.name])

    def test_unknown_target_and_extra_file_are_refused(self):
        output = self.parent / "unknown"
        with self.assertRaisesRegex(ValueError, "unknown adapter target"):
            builder.assemble("unqualified-device", output)
        self.assertFalse(output.exists())
        result = builder.assemble("windows", output)
        (output / "unexpected.py").write_text("raise RuntimeError('must not load')\n")
        with self.assertRaisesRegex(ValueError, "unexpected bundle files"):
            builder.verify(output, result["manifest_sha256"])

    def test_catalog_covers_every_shipped_platform_source(self):
        for target, directories in builder.CATALOG.items():
            for folder, names in directories.items():
                self.assertEqual(set(names), {p.name for p in (ROOT / folder).glob("*.py")}, target)

    def test_normal_temporary_parent_alias_is_resolved(self):
        # /tmp is a normal directory on Linux and an OS symlink on macOS.
        with tempfile.TemporaryDirectory(dir="/tmp") as parent:
            output = Path(parent) / "bundle"
            result = builder.assemble("macos", output)
            self.assertEqual(builder.verify(output, result["manifest_sha256"])["target"], "macos")

    def test_source_symlink_is_refused(self):
        output = self.parent / "macos"
        result = builder.assemble("macos", output)
        path = output / "provider.py"
        original = path.read_bytes()
        path.unlink()
        external = self.parent / "external.py"
        external.write_bytes(original)
        try:
            path.symlink_to(external)
        except (OSError, NotImplementedError) as error:
            # Windows without symlink privilege still must refuse a non-file.
            path.mkdir()
            self.assertIsInstance(error, (OSError, NotImplementedError))
        with self.assertRaisesRegex(ValueError, "regular source file required"):
            builder.verify(output, result["manifest_sha256"])


class PublicBoundary(unittest.TestCase):
    def test_adapter_tree_contains_code_and_synthetic_fixtures_only(self):
        paths = [p for p in (ROOT / "resources").rglob("*") if p.is_file()]
        paths += [ROOT / p for p in ["tools/build_resource_adapter.py", "tests/test_resource_adapters.py",
                                    "README.md", "docs/README.md", "docs/publishing.md"]]
        for path in paths:
            if path.is_relative_to(ROOT / "resources"):
                self.assertIn(path.suffix, {".py", ".c", ".md"}, "operational profiles are not public source artifacts")
            source = path.read_text()
            self.assertNotRegex(source, r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY")
            self.assertNotRegex(source, r"\bpmq_[A-Za-z0-9_-]{16,}\b")
            self.assertNotRegex(source, r"\b(?:sk-ant-|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{16,}\b")
            for address in re.findall(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])", source):
                value = ipaddress.ip_address(address)
                self.assertTrue(value.is_loopback or value.is_unspecified
                                or any(value in ipaddress.ip_network(n) for n in ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"]),
                                "use documentation addresses, never operational network addresses")
            for home in re.findall(r"/(?:Users|home)/([A-Za-z0-9_.-]+)", source):
                self.assertIn(home, {"example", "owner", "test", "admin", "runner"}, "private home path")
            for identifier in re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])", source):
                self.assertTrue(identifier.startswith("aaaaaaaa-aaaa-4aaa-8aaa-")
                                or identifier == "00000000-0000-0000-0000-000000000000"
                                or identifier in PUBLIC_REFERENCE_UUIDS, "non-synthetic UUID literal")
            for sid in re.findall(r"S-1-5-21(?:-[0-9]+){4}", source):
                self.assertTrue(sid.startswith("S-1-5-21-1-2-3-"), "non-synthetic account SID")
            for value in re.findall(r"\b01[0-9A-HJKMNP-TV-Z]{24}\b", source):
                self.assertTrue(value.startswith("01AAAA"), "public fixture requires synthetic account/claim identifiers")


class PortableProviderControls(unittest.TestCase):
    def test_linux_configuration_alias_and_conflicts(self):
        path = ROOT / "resources/host/linux/src/evidence.py"
        spec = importlib.util.spec_from_file_location("public_linux_configuration", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        generic = "example-current-owner.json"
        legacy = "example-legacy-owner.json"
        self.assertEqual(module.selected_config({"POSTMQ_LINUX_CONFIG": generic}), generic)
        self.assertEqual(module.selected_config({"POSTMQ_GB10_CONFIG": legacy}), legacy)
        self.assertEqual(module.selected_config({"POSTMQ_LINUX_CONFIG": generic,
                                                "POSTMQ_GB10_CONFIG": generic}), generic)
        with self.assertRaisesRegex(ValueError, "conflicting Linux"):
            module.selected_config({"POSTMQ_LINUX_CONFIG": generic, "POSTMQ_GB10_CONFIG": legacy})
        for environment in ({}, {"POSTMQ_LINUX_CONFIG": ""}, {"POSTMQ_LINUX_CONFIG": 1}):
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "must select"):
                module.selected_config(environment)

    def test_mac_identity_cache_is_per_instance_and_returns_independent_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "bundle"
            builder.assemble("macos", output)
            spec = importlib.util.spec_from_file_location("public_cached_mac_entry", output / "entry.py")
            entry = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(entry)
            native = entry.module("native")
            identity = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
            def observe(argv, **kwargs):
                if argv[0].endswith("ioreg"):
                    return plistlib.dumps([{"IOPlatformUUID": identity}])
                self.assertEqual(argv[:3], ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"])
                return identity + "\n"
            with mock.patch.object(native.subprocess, "check_output", side_effect=observe) as calls:
                first = native.Darwin.__new__(native.Darwin)
                first._host_identity = None
                seen = first.host()
                seen["host_uuid"] = "caller-mutated"
                for _ in range(10):
                    self.assertEqual(first.host(), {"host_uuid": identity, "boot_uuid": identity})
                self.assertEqual(calls.call_count, 2)
                second = native.Darwin.__new__(native.Darwin)
                second._host_identity = None
                self.assertEqual(second.host()["host_uuid"], identity)
                self.assertEqual(calls.call_count, 4)
    def test_shared_lifecycle_admission_races_and_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            source = ROOT / "resources/host/common/src/lifecycle"
            for path in source.glob("*.py"):
                shutil.copyfile(path, target / path.name)
            shutil.copyfile(ROOT / "resources/host/common/tests/test_lifecycle.py", target / "test_lifecycle.py")
            result = subprocess.run([sys.executable, "-I", "-B", str(target / "test_lifecycle.py")],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stderr, r"Ran [1-9][0-9]* tests")
            self.assertNotIn("skipped=", result.stderr)

    def run_controls(self, target, source_tests, test_names):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "bundle"
            builder.assemble(target, output)
            for name in source_tests:
                shutil.copyfile(ROOT / "resources/host" / target / "tests" / name, output / name)
            if target == "linux":
                shutil.copyfile(ROOT / "resources/nvidia/tests/cuda_abi_fixture.c", output / "cuda_abi_fixture.c")
            code = """from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:])
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.testsRun and result.wasSuccessful() and not result.skipped else 1)
"""
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(output), *test_names],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_linux_synthetic_refusal_and_inventory_controls(self):
        tests = ["test_provider.py", "test_guarded.py", "test_linux_procscan.py", "test_container_assessment.py",
                 "test_cuda_driver_probe.py", "test_observer_context_check.py"]
        names = ["test_provider", "test_guarded", "test_linux_procscan.PureRentalTests",
                 "test_container_assessment", "test_cuda_driver_probe", "test_observer_context_check"]
        self.run_controls("linux", tests, names)

    def test_macos_synthetic_custody_refusals(self):
        self.run_controls("macos", ["checks.py"], ["checks.Controls"])

    def test_windows_kernel_parser_without_native_process_control(self):
        self.run_controls("windows", ["test_windows_kernel.py"],
                          ["test_windows_kernel.Parser", "test_windows_kernel.Identifier"])

    def test_isolated_nvidia_diagnostic_help_loads_without_a_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / "bundle"
            builder.assemble("linux", output)
            result = subprocess.run([sys.executable, "-I", "-B", str(output / "observer_context_check.py"), "--help"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--gpu-uuid", result.stdout)


if sys.platform == "linux":
    class LinuxOwnedProcesses(unittest.TestCase):
        # This class is deliberately absent on foreign OSes. It reports a real
        # Linux run, never a green stand-in or a skipped required check.
        def test_owned_fork_controls(self):
            PortableProviderControls.run_controls(self, "linux", ["test_guard.py"], ["test_guard"])
else:
    print("Linux-owned fork controls are not executed on this OS; hosted Ubuntu CI runs them.", file=sys.stderr)


class CurrentSDKContract(unittest.TestCase):
    def test_real_sdk_loads_all_factories_and_accepts_their_declarations(self):
        sdk_path = ROOT / "plugins/postmq-claim/tools/postmq_claim.py"
        ownership = json.loads((ROOT / ".postmq-generated.json").read_bytes())
        self.assertEqual(hashlib.sha256(sdk_path.read_bytes()).hexdigest(),
                         ownership["files"][sdk_path.relative_to(ROOT).as_posix()]["sha256"])
        spec = importlib.util.spec_from_file_location("public_current_claim_sdk", sdk_path)
        sdk = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = sdk
        spec.loader.exec_module(sdk)

        def load(path, name):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        claim = "01AAAAAAAAAAAAAAAAAAAAAA01"
        identity = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
        with tempfile.TemporaryDirectory(prefix="pqa-", dir="/tmp") as temporary:
            root = Path(temporary).resolve()
            for target in builder.CATALOG:
                builder.assemble(target, root / target)
            baseline = {"boot_id": identity, "gpu_uuid": "GPU-" + identity, "free_bytes": 1024**3,
                        "total_bytes": 2*1024**3, "tolerance_bytes": 1024**2,
                        "observed_at": "2000-01-01T00:00:00.000Z"}
            linux = root / "linux"
            config = root / "linux-owner.json"
            config.write_text(json.dumps({"memory_mode": "discrete", "baseline": baseline,
                "required_bytes": 1, "reserve_bytes": 1, "native_state_root": str(root / "rental")}))
            providers = []
            with mock.patch.dict("os.environ", {"POSTMQ_LINUX_CONFIG": str(config)}, clear=True):
                providers.append((sdk.load_provider(str(linux / "sdk_provider.py") + ":factory"),
                                  f"scope:{claim}:1:{identity}:1:2:3"))
                providers.append((sdk.load_provider(str(linux / "sdk_provider.py") + ":rental_factory"),
                                  f"procscan:{claim}:1:{identity}:1:2:3"))

            mac = root / "macos"
            entry = load(mac / "entry.py", "public_contract_mac_entry")
            provider_module = entry.module("provider")
            home = root / "mac-home"
            home.mkdir(mode=0o700)
            host = {"host_uuid": identity, "boot_uuid": identity}
            config = root / "mac-owner.json"
            config.write_text(json.dumps({"expected_host": host, "max_run_seconds": 10,
                "minimum_free_percent": 10, "owner_mode": "background",
                "allowed_executables": {"/bin/echo": "0"*64}}))
            config.chmod(0o600)
            with mock.patch.object(provider_module, "Darwin", return_value=types.SimpleNamespace(host=lambda:dict(host))), \
                 mock.patch.object(provider_module.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(home))), \
                 mock.patch.dict("os.environ", {"POSTMQ_MACOS_CONFIG": str(config)}, clear=True):
                providers.append((sdk.load_provider(str(mac / "entry.py") + ":factory"),
                                  f"mac1:{claim}:1:{identity}:1:" + "a"*64))

            windows = root / "windows"
            entry = load(windows / "entry.py", "public_contract_windows_entry")
            for name in [Path(n).stem for n in builder.CATALOG["windows"]["resources/host/windows/src"] if n != "__init__.py"]:
                self.assertIsNotNone(entry.module(name))
            provider_module = entry.module("windows_provider")
            native = entry.module("windows_identity")
            host = {"machine_guid": identity, "boot_identifier": identity, "owner_sid": "S-1-5-21-1-2-3-1001"}
            native_root = root / "windows-owner"
            native_root.mkdir(mode=0o700)
            config = {"expected_host": host, "max_run_seconds": 10, "job_memory_bytes": 64*1024**2,
                "minimum_available_bytes": 64*1024**2, "gpu_uuid": "GPU-"+identity,
                "minimum_free_vram_bytes": 64*1024**2,
                "nvidia_smi": {"path": "C:/Example/nvidia-smi.exe", "sha256": "0"*64},
                "owner_mode": "background", "allowed_executables": {"C:/Example/runtime.exe": "0"*64}}
            (native_root / "owner.json").write_text(json.dumps(config))

            class FakeApi:
                def root(self):return native_root
                def json_read(self, path):return json.loads(path.read_bytes())
                def directory(self, path):
                    path = Path(path)
                    if not path.is_relative_to(native_root):raise ValueError("fixture directory escaped")
                    path.mkdir(mode=0o700, exist_ok=True)
                    return path

            with mock.patch.object(provider_module, "StateApi", return_value=FakeApi()), \
                 mock.patch.object(native, "host", return_value=host), \
                 mock.patch.dict("os.environ", {}, clear=True):
                providers.append((sdk.load_provider(str(windows / "entry.py") + ":factory"),
                                  f"win1:{claim}:1:" + "a"*64))

            for provider, reference in providers:
                with self.subTest(provider=type(provider).__name__):
                    declaration = sdk.declared(sdk.Bounded(provider))
                    self.assertTrue(declaration["provider_host_key"])
                    self.assertIn(declaration["receipt_provider"], {"generic", "linux-cgroup", "linux-procscan-v1"})
                    self.assertTrue(sdk.valid_journal_ref(reference))
                    if reference.startswith(("scope:", "procscan:")):
                        self.assertEqual(provider.journal_ref({"ref": reference, "claim_id": claim, "fence": 1}),
                                         reference)
                    else:
                        self.assertIsNotNone(sys.modules[type(provider).__module__].REF.fullmatch(reference))
                    parsed = sdk.parse_entry(f"launched {claim} 1 {reference}")
                    self.assertEqual((parsed.claim, parsed.fence, parsed.ref), (claim, 1, reference))


if __name__ == "__main__":
    unittest.main()
