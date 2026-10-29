"""Offline regression cases for policy boundaries, not plugin runtime qualification."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_public_repo as check


class RequiredRunnerTests(unittest.TestCase):
    def test_empty_skipped_failed_and_passing_real_suites(self):
        # Bug shape: ordinary unittest returns success for skipped or empty required verification.
        cases = [
            ("empty", "", 1, "run=0, skipped=0"),
            ("skipped", "import unittest\nclass Check(unittest.TestCase):\n @unittest.skip('synthetic prerequisite absent')\n def test_required(self): self.fail('must not run')\n", 1, "run=1, skipped=1"),
            ("failed", "import unittest\nclass Check(unittest.TestCase):\n def test_required(self): self.assertEqual('actual', 'expected')\n", 1, "failures=1"),
            ("passed", "import unittest\nclass Check(unittest.TestCase):\n def test_required(self): self.assertEqual(2 + 2, 4)\n", 0, "1 tests, zero skips"),
        ]
        for name, source, code, evidence in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "test_required.py").write_text(source)
                result = subprocess.run([sys.executable, str(check.ROOT / "tools/run_tests.py"), "--start-directory", directory], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(evidence, result.stdout + result.stderr)

    def test_ci_whitespace_command_checks_the_actual_committed_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=15)
            self.assertEqual(git("init", "-q").returncode, 0)
            (root / "example.txt").write_text("synthetic trailing whitespace  \n")
            self.assertEqual(git("add", "example.txt").returncode, 0)
            self.assertEqual(git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "Disposable whitespace regression").returncode, 0)
            result = git("show", "--format=", "--check", "--diff-merges=first-parent", "HEAD")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("trailing whitespace", result.stdout)
            # A clean working tree would otherwise hide the committed defect.
            self.assertEqual(git("diff", "--check").returncode, 0)

    def test_shallow_ci_checkout_checks_the_merge_first_parent_patch(self):
        # Bug shape: checking a merge without its base can inspect the wrong tree/patch.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            def git(where, *args):
                return subprocess.run(["git", "-C", str(where), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, timeout=15)
            def succeed(where, *args):
                result = git(where, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            succeed(source, "init", "-q", "-b", "main")
            (source / "old.txt").write_text("legacy whitespace  \n")
            succeed(source, "add", "old.txt")
            succeed(source, "commit", "-qm", "Disposable base")
            succeed(source, "checkout", "-qb", "feature")
            (source / "new.txt").write_text("new defect  \n")
            succeed(source, "add", "new.txt")
            succeed(source, "commit", "-qm", "Disposable proposed change")
            succeed(source, "checkout", "-q", "main")
            succeed(source, "merge", "--no-ff", "--no-edit", "feature")
            clone = root / "clone"
            succeed(root, "clone", "--depth", "2", "--branch", "main", source.as_uri(), str(clone))
            succeed(clone, "rev-parse", "--verify", "HEAD^1")
            result = git(clone, "show", "--format=", "--check", "--diff-merges=first-parent", "HEAD")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("new.txt:1: trailing whitespace", result.stdout)
            self.assertNotIn("old.txt", result.stdout)
            self.assertEqual(git(clone, "diff", "--check").returncode, 0)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = check.load_json(check.ROOT / ".github/workflows/public-checks.yml")
        check.check_workflow(self.workflow)

    def test_safe_workflow_is_accepted(self):
        check.check_workflow(self.workflow)
        self.assertEqual(self.workflow["jobs"]["public-checks"]["name"], "Public repository checks")

    def test_unsafe_workflow_changes_are_rejected(self):
        # Bug shape: a fork can reach persistent infrastructure or privileged credentials.
        cases = [
            (("on",), {"pull_request_target": {}}, "workflow triggers"),
            (("on",), {"pull_request": {"paths": ["docs/**"]}}, "workflow triggers"),
            (("permissions",), {"contents": "write"}, "read-only permissions"),
            (("env",), {"TOKEN": "${{ secrets.TOKEN }}"}, "workflow keys"),
            (("jobs", "public-checks", "runs-on"), ["self-hosted", "linux"], "hosted runner"),
            (("jobs", "public-checks", "permissions"), {"id-token": "write"}, "job keys"),
            (("jobs", "public-checks", "if"), "false", "job keys"),
            (("jobs", "public-checks", "container"), "private/image", "job keys"),
            (("jobs", "public-checks", "timeout-minutes"), 600, "bounded job timeout"),
            (("jobs", "public-checks", "steps", 0, "uses"), "actions/checkout@main", "full official action SHA"),
            (("jobs", "public-checks", "steps", 0, "uses"), "untrusted/checkout@" + "a" * 40, "full official action SHA"),
            (("jobs", "public-checks", "steps", 0, "with", "persist-credentials"), True, "retain credentials"),
            (("jobs", "public-checks", "steps", 0, "with", "fetch-depth"), 1, "fetch two commits"),
            (("jobs", "public-checks", "steps", 0, "with", "ref"), "${{ github.event.pull_request.head.ref }}", "override ref"),
            (("jobs", "public-checks", "steps", 1, "run"), "echo success", "required check command"),
            (("jobs", "public-checks", "steps", 1, "continue-on-error"), True, "check step keys"),
            (("jobs", "public-checks", "steps", 1, "env"), {"KEY": "${{ secrets.KEY }}"}, "check step keys"),
            (("jobs", "public-checks", "steps", 1, "name"), "${{ github.event.pull_request.title }}", "expressions"),
        ]
        for path, value, reason in cases:
            with self.subTest(path=path, value=value):
                mutant = copy.deepcopy(self.workflow)
                destination = mutant
                for part in path[:-1]:
                    destination = destination[part]
                destination[path[-1]] = value
                with self.assertRaisesRegex(ValueError, reason):
                    check.check_workflow(mutant)


class GeneratedOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("plugins", ".claude-plugin"):
            shutil.copytree(check.ROOT / name, self.root / name)
        self.manifest = {
            "schema_version": 1,
            "generator": "postmq/claude-plugin",
            "plugin_versions": {},
            "files": {},
        }
        for path in self.root.rglob("*"):
            if path.is_file():
                self.record(path)
        for name in check.PLUGINS:
            plugin = check.load_json(self.root / f"plugins/{name}/.claude-plugin/plugin.json")
            self.manifest["plugin_versions"][name] = plugin["version"]
        check.check_generated(self.root, self.manifest)

    def record(self, path):
        self.manifest["files"][path.relative_to(self.root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "executable": bool(path.stat().st_mode & 0o111),
        }

    def test_real_distribution_inventory_is_accepted(self):
        check.check_generated(self.root, self.manifest)
        self.assertIn(".claude-plugin/marketplace.json", self.manifest["files"])

    def test_publisher_cannot_own_maintained_paths_or_escape(self):
        # Bug shape: a generated manifest takes ownership of public policy or docs.
        for name in ("README.md", "AGENTS.md", "LICENSE", "docs/publishing.md", "../outside", "/absolute", "plugins/../README.md", "plugins/other/file", "plugins/postmq-session//file", "plugins\\postmq-session\\file", ".postmq-generated.json"):
            with self.subTest(name=name):
                mutant = copy.deepcopy(self.manifest)
                mutant["files"][name] = {"sha256": "a" * 64, "executable": False}
                with self.assertRaisesRegex(ValueError, "outside allowed namespace"):
                    check.check_generated(self.root, mutant)

    def test_changed_generated_bytes_fail(self):
        path = self.root / ".claude-plugin/marketplace.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            check.check_generated(self.root, self.manifest)

    def test_unsafe_portable_names_are_rejected_before_filesystem_access(self):
        # Bug shape: a manifest accepts a path that the canonical publisher refuses.
        for name in ("plugins/postmq-session/name:stream", "plugins/postmq-session/control\x00", "plugins/postmq-session/control\x01", "plugins/postmq-session/control\t", "plugins/postmq-session/control\n", "plugins/postmq-session/control\x7f", "plugins/postmq-session/.git/config"):
            with self.subTest(name=repr(name)):
                self.assertFalse(check.generated_path(name), repr(name))
                mutant = copy.deepcopy(self.manifest)
                mutant["files"][name] = {"sha256": "a" * 64, "executable": False}
                with self.assertRaisesRegex(ValueError, "outside allowed namespace"):
                    check.check_generated(self.root, mutant)

    def test_present_unsafe_generated_file_is_not_legitimized_by_its_digest(self):
        for suffix in ("name:stream", "control\t", "control\n", "control\x7f", ".git/config"):
            with self.subTest(suffix=repr(suffix)):
                path = self.root / "plugins/postmq-session" / suffix
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic generated content")
                self.record(path)
                try:
                    with self.assertRaisesRegex(ValueError, "outside allowed namespace"):
                        check.check_generated(self.root, self.manifest)
                finally:
                    del self.manifest["files"][path.relative_to(self.root).as_posix()]
                    path.unlink()

    def test_changed_executable_mode_fails(self):
        path = self.root / ".claude-plugin/marketplace.json"
        path.chmod(path.stat().st_mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, "mode mismatch"):
            check.check_generated(self.root, self.manifest)

    def test_unowned_file_fails(self):
        (self.root / "plugins/postmq-session/unowned.txt").write_text("synthetic")
        with self.assertRaisesRegex(ValueError, "inventory mismatch: unowned=.*unowned.txt"):
            check.check_generated(self.root, self.manifest)

    def test_symlink_fails_even_if_target_has_same_bytes(self):
        path = self.root / ".claude-plugin/marketplace.json"
        target = self.root / "copy.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "generated symlink"):
            check.check_generated(self.root, self.manifest)

    def test_marketplace_identity_drift_fails_with_matching_hash(self):
        path = self.root / ".claude-plugin/marketplace.json"
        value = check.load_json(path)
        value["name"] = "renamed"
        path.write_text(json.dumps(value))
        self.record(path)
        with self.assertRaisesRegex(ValueError, "marketplace identity changed"):
            check.check_generated(self.root, self.manifest)

    def test_plugin_version_drift_fails_with_matching_hash(self):
        path = self.root / "plugins/postmq-session/.claude-plugin/plugin.json"
        value = check.load_json(path)
        value["version"] = "99.0.0"
        path.write_text(json.dumps(value))
        self.record(path)
        with self.assertRaisesRegex(ValueError, "plugin version drift"):
            check.check_generated(self.root, self.manifest)


class DocumentationTests(unittest.TestCase):
    def test_real_local_links_and_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.md").write_text("# Target\n\n## Same\n\n## Same\n")
            page = root / "README.md"
            page.write_text("[target](target.md#same-1)\n[external](https://example.org/)\n")
            check.check_links(root, page)
            # Bug shape: a renamed section leaves a seemingly valid file link broken.
            page.write_text("[target](target.md#missing)\n")
            with self.assertRaisesRegex(ValueError, "broken Markdown anchor"):
                check.check_links(root, page)
            page.write_text("[target](missing.md)\n")
            with self.assertRaisesRegex(ValueError, "broken local link"):
                check.check_links(root, page)
            page.write_text("[target](../outside.md)\n")
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                check.check_links(root, page)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"permissions":"read","permissions":"write"}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: permissions"):
                check.load_json(path)


class SecurityConfigurationTests(unittest.TestCase):
    def test_safe_configuration_is_accepted(self):
        check.check_security_config(check.ROOT)
        self.assertTrue((check.ROOT / ".github/rulesets/main-integrity.json").is_file())

    def test_integrity_bypass_and_weakened_required_checks_fail(self):
        # Bug shape: an owner exception accidentally bypasses required CI as well as reviews.
        mutations = [
            ("bypass", "integrity must have no bypass"),
            ("check", "strict app-bound required check"),
            ("review", "code-owner review policy"),
            ("scope", "ruleset scope"),
        ]
        for mutation, reason in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(check.ROOT / ".github", root / ".github")
                path = root / ".github/rulesets/main-integrity.json"
                data = check.load_json(path)
                if mutation == "bypass":
                    data["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}]
                elif mutation == "check":
                    next(r for r in data["rules"] if r["type"] == "required_status_checks")["parameters"]["strict_required_status_checks_policy"] = False
                elif mutation == "scope":
                    data["conditions"]["ref_name"]["exclude"] = ["refs/heads/main"]
                else:
                    path = root / ".github/rulesets/main-review.json"
                    data = check.load_json(path)
                    data["rules"][0]["parameters"]["require_code_owner_review"] = False
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, reason):
                    check.check_security_config(root)


if __name__ == "__main__":
    unittest.main()
