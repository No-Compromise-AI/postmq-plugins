#!/usr/bin/env python3
"""Offline public-repository checks; never execute plugins or contact a live service."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


PLUGINS = {"postmq-session", "postmq-wake", "postmq-claim"}
ROOT = Path(__file__).resolve().parents[1]
LICENSE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
CHECK_COMMANDS = [
    "python3 tools/run_tests.py",
    "python3 tools/check_public_repo.py",
    "git show --format= --check --diff-merges=first-parent HEAD",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    require(isinstance(value, dict), f"expected JSON object: {path.name}")
    return value


def check_workflow(workflow: dict) -> None:
    """A deliberately closed workflow contract: additions require policy review."""
    require(set(workflow) == {"name", "on", "permissions", "jobs"}, "workflow keys")
    require(workflow["name"] == "Public repository checks", "workflow name")
    require(workflow["on"] == {"pull_request": {}, "push": {"branches": ["main"]}}, "workflow triggers")
    require(workflow["permissions"] == {"contents": "read"}, "workflow read-only permissions")
    require(set(workflow["jobs"]) == {"public-checks"}, "workflow job inventory")
    job = workflow["jobs"]["public-checks"]
    require(set(job) == {"name", "runs-on", "timeout-minutes", "steps"}, "job keys")
    require(job["name"] == "Public repository checks", "required check name")
    require(job["runs-on"] == "ubuntu-24.04", "disposable hosted runner required")
    require(type(job["timeout-minutes"]) is int and 1 <= job["timeout-minutes"] <= 10, "bounded job timeout")
    steps = job["steps"]
    require(isinstance(steps, list) and len(steps) == 4, "workflow step inventory")
    checkout = steps[0]
    require(set(checkout) == {"name", "uses", "with"}, "checkout keys")
    require(re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"]) is not None, "checkout full official action SHA")
    require(checkout["with"] == {"persist-credentials": False, "fetch-depth": 2}, "checkout must not retain credentials or override ref; fetch two commits")
    for step, command in zip(steps[1:], CHECK_COMMANDS):
        require(set(step) == {"name", "run"}, "check step keys")
        require(step["run"] == command, "required check command")
    require("${{" not in json.dumps(workflow), "workflow expressions are not permitted")


def generated_path(name: str) -> bool:
    if not isinstance(name, str) or not name:
        return False
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or "\\" in name
        or ":" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or str(path) != name
        or any(part in {".", "..", ".git"} for part in path.parts)
    ):
        return False
    return name in {".claude-plugin/marketplace.json", ".claude-plugin/README.md"} or (
        len(path.parts) >= 3 and path.parts[0] == "plugins" and path.parts[1] in PLUGINS
    )


def check_generated(root: Path, manifest: dict) -> None:
    require(set(manifest) == {"schema_version", "generator", "plugin_versions", "files"}, "ownership manifest keys")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1, "ownership schema version")
    require(manifest["generator"] == "postmq/claude-plugin", "canonical generator")
    require(set(manifest["plugin_versions"]) == PLUGINS, "plugin version inventory")
    files = manifest["files"]
    require(isinstance(files, dict) and ".claude-plugin/marketplace.json" in files, "marketplace must be owned")
    for name, metadata in files.items():
        require(generated_path(name), f"publisher ownership outside allowed namespace: {name}")
        path = root / name
        require(not any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)), f"generated symlink: {name}")
        require(path.is_file(), f"missing generated file: {name}")
        require(set(metadata) == {"sha256", "executable"}, f"generated metadata keys: {name}")
        require(isinstance(metadata["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is not None, f"generated digest shape: {name}")
        require(type(metadata["executable"]) is bool, f"generated executable flag: {name}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"], f"generated digest mismatch: {name}")
        require(bool(path.stat().st_mode & 0o111) == metadata["executable"], f"generated executable mode mismatch: {name}")
    actual = set()
    for folder in (root / ".claude-plugin", root / "plugins"):
        require(folder.is_dir() and not folder.is_symlink(), f"generated directory missing or symlink: {folder.name}")
        for path in folder.rglob("*"):
            require(not path.is_symlink(), f"generated symlink: {path.relative_to(root)}")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
    require(actual == set(files), f"generated inventory mismatch: unowned={sorted(actual - set(files))}; missing={sorted(set(files) - actual)}")
    marketplace = load_json(root / ".claude-plugin/marketplace.json")
    require(marketplace["name"] == "postmq", "marketplace identity changed")
    entries = marketplace["plugins"]
    require(len(entries) == len(PLUGINS) and {p["name"] for p in entries} == PLUGINS, "marketplace plugin identities changed")
    for entry in entries:
        name = entry["name"]
        require(entry["source"] == f"./plugins/{name}", "plugin installation source changed")
        plugin = load_json(root / f"plugins/{name}/.claude-plugin/plugin.json")
        kit = load_json(root / f"plugins/{name}/postmq-kit.json")
        version = manifest["plugin_versions"][name]
        require(isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version) is not None, f"plugin version shape: {name}")
        require(plugin["name"] == kit["plugin"] == name, f"plugin identity drift: {name}")
        require(plugin["version"] == kit["kit_version"] == version, f"plugin version drift: {name}")
        require(kit["marketplace"] == "postmq" and kit["client"] == "claude-code", f"kit identity drift: {name}")


def markdown_text(text: str) -> str:
    return re.sub(r"(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$", "", text)


def anchors(path: Path) -> set[str]:
    values: set[str] = set()
    occurrences: dict[str, int] = {}
    for title in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", markdown_text(path.read_text(encoding="utf-8"))):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        number = occurrences.get(slug, 0)
        occurrences[slug] = number + 1
        values.add(f"{slug}-{number}" if number else slug)
    return values


def check_links(root: Path, path: Path) -> None:
    text = markdown_text(path.read_text(encoding="utf-8"))
    # Our maintained Markdown uses inline links, angle links and reference definitions.
    targets = re.findall(r"\]\(<?([^\s)>]+)>?(?:\s+\"[^\"]*\")?\)", text)
    targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", text)
    for target in targets:
        url = urlsplit(target)
        if url.scheme in {"https", "http", "mailto"}:
            continue
        require(not url.scheme and not url.netloc, f"unsupported link scheme: {path.name}: {target}")
        destination = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
        require(destination.is_relative_to(root.resolve()), f"link escapes repository: {path.name}: {target}")
        require(destination.exists(), f"broken local link: {path.name}: {target}")
        if url.fragment:
            require(destination.suffix.lower() == ".md" and unquote(url.fragment) in anchors(destination), f"broken Markdown anchor: {path.name}: {target}")


def check_security_config(root: Path) -> None:
    settings = load_json(root / ".github/repository-settings.json")
    require(settings["workflow_permissions"] == {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False}, "default workflow permissions")
    require(settings["actions"] == {"enabled": True, "allowed_actions": "selected", "sha_pinning_required": True}, "Actions allowlist/pinning")
    require(settings["selected_actions"] == {"github_owned_allowed": False, "verified_allowed": False, "patterns_allowed": ["actions/checkout@*", "actions/setup-python@*"]}, "selected action inventory")
    require(settings["fork_pr_contributor_approval"] == {"approval_policy": "all_external_contributors"}, "external fork approval")
    for feature in ("vulnerability_alerts", "automated_security_fixes", "private_vulnerability_reporting"):
        require(settings[feature] is True, f"security feature: {feature}")
    for feature in ("secret_scanning", "secret_scanning_push_protection"):
        require(settings["repository"]["security_and_analysis"][feature] == {"status": "enabled"}, f"secret protection: {feature}")
    rulesets = {}
    for name in ("main-integrity", "main-review", "immutable-tags"):
        value = load_json(root / f".github/rulesets/{name}.json")
        require(value["name"] == name and value["enforcement"] == "active", f"active ruleset: {name}")
        include = ["refs/tags/**"] if name == "immutable-tags" else ["~DEFAULT_BRANCH"]
        require(value["conditions"] == {"ref_name": {"include": include, "exclude": []}}, f"ruleset scope: {name}")
        require(value["target"] == ("tag" if name == "immutable-tags" else "branch"), f"ruleset target: {name}")
        rulesets[name] = value
    integrity = rulesets["main-integrity"]
    require(integrity["bypass_actors"] == [], "integrity must have no bypass")
    rules = {r["type"]: r for r in integrity["rules"]}
    require(len(rules) == len(integrity["rules"]) and set(rules) == {"deletion", "non_fast_forward", "required_linear_history", "pull_request", "required_status_checks"}, "integrity rule inventory")
    require(rules["pull_request"]["parameters"]["required_review_thread_resolution"] is True, "resolved conversations required")
    checks = rules["required_status_checks"]["parameters"]
    require(checks == {"required_status_checks": [{"context": "Public repository checks", "integration_id": 15368}], "strict_required_status_checks_policy": True, "do_not_enforce_on_create": False}, "strict app-bound required check")
    review = rulesets["main-review"]
    require(review["bypass_actors"] == [{"actor_id": 283503720, "actor_type": "User", "bypass_mode": "pull_request"}], "review exception must remain owner PR-only")
    require(len(review["rules"]) == 1 and review["rules"][0]["type"] == "pull_request", "review rules inventory")
    params = review["rules"][0]["parameters"]
    require(params == {"required_approving_review_count": 1, "dismiss_stale_reviews_on_push": True, "require_code_owner_review": True, "require_last_push_approval": True, "required_review_thread_resolution": True, "allowed_merge_methods": ["squash"]}, "code-owner review policy")
    tags = rulesets["immutable-tags"]
    require(tags["bypass_actors"] == [] and tags["rules"] == [{"type": "deletion"}, {"type": "update"}], "immutable tags without bypass")


def check_repository(root: Path) -> int:
    paths = [p for p in root.rglob("*") if ".git" not in p.relative_to(root).parts and "__pycache__" not in p.parts]
    require(not any(p.is_symlink() for p in paths), "repository symlinks are not allowed")
    workflows = sorted((root / ".github/workflows").iterdir())
    require([p.name for p in workflows] == ["public-checks.yml"], "workflow file inventory")
    check_workflow(load_json(workflows[0]))
    dependabot = load_json(root / ".github/dependabot.yml")
    require(dependabot == {"version": 2, "updates": [{"package-ecosystem": "github-actions", "directory": "/", "schedule": {"interval": "weekly"}, "open-pull-requests-limit": 5}]}, "dependency update policy")
    require((root / "CLAUDE.md").read_text() == "@AGENTS.md\n", "Claude must import the single policy")
    require(hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest() == LICENSE_SHA256, "official unmodified Apache 2.0 license")
    require((root / ".github/CODEOWNERS").read_text().splitlines()[-1] == "* @nocompromiseai", "maintainer code ownership")
    check_generated(root, load_json(root / ".postmq-generated.json"))
    check_security_config(root)
    for path in paths:
        if not path.is_file():
            continue
        if path.suffix == ".json":
            load_json(path)
        if path.suffix == ".md":
            check_links(root, path)
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=path.relative_to(root).as_posix())
        if path.suffix == ".sh":
            subprocess.run(["bash", "-n", str(path)], check=True)
    return len([p for p in paths if p.is_file()])


def main() -> int:
    try:
        count = check_repository(ROOT)
    except (ValueError, OSError, KeyError, TypeError, SyntaxError, subprocess.CalledProcessError) as error:
        print(f"Public repository checks failed: {error}", file=sys.stderr)
        return 1
    print(f"Public repository checks passed ({count} files; no live integrations exercised).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
