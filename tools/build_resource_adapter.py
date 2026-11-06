#!/usr/bin/env python3
"""Assemble public adapter sources, without installing or starting anything."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
CATALOG = {
    "linux": {
        "resources/host/linux/src": ["__init__.py", "sdk_provider.py", "evidence.py", "linux_cgroup.py",
            "guarded_cgroup.py", "guard.py", "trampoline.py", "linux_procscan.py", "rental_guard.py",
            "rental_native.py", "container_assessment.py", "allocation_scope.py", "managed_allocation.py"],
        "resources/nvidia/src": ["probe.py", "cuda_driver_probe.py", "observer_context_check.py", "native_cuda_checks.py"],
    },
    "macos": {"resources/host/macos/src": ["__init__.py", "entry.py", "native.py", "provider.py", "guard.py"]},
    "windows": {"resources/host/windows/src": ["__init__.py", "entry.py", "windows_job.py", "windows_state.py",
        "windows_identity.py", "windows_kernel.py", "windows_provider.py", "windows_guard.py"]},
}


def require(value, message):
    if not value:
        raise ValueError(message)


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "regular source file required")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(descriptor)
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino)
                and stat.S_ISREG(opened.st_mode), "source changed while opening")
        chunks = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
            require(sum(map(len, chunks)) <= 1024 * 1024, "source size exceeds bundle limit")
        after = os.fstat(descriptor)
        require((opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                "source changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def output_path(value):
    path = Path(value).absolute()
    require(not path.is_symlink(), "bundle directory symlink refused")
    # macOS's /tmp is a normal OS symlink to /private/tmp. Resolve the parent,
    # while refusing a symlink at the actual bundle destination.
    return path.parent.resolve(strict=True) / path.name


def hashes(files):
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}


def assemble(target, output):
    require(target in CATALOG, "unknown adapter target")
    output = output_path(output)
    require(not output.exists() and not output.is_symlink(), "output already exists; inspect rather than overwrite")
    require(output.parent.is_dir() and output.parent.resolve() == output.parent, "existing canonical parent required")
    files = {}
    for directory, names in CATALOG[target].items():
        for name in names:
            path = ROOT / directory / name
            require(path.resolve() == path and name not in files, "source path or filename collision")
            data = regular(path)
            compile(data, name, "exec")
            files[name] = data
    manifest = {"format_version": 1, "adapter_version": VERSION, "target": target, "files": hashes(files)}
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    # No mutation occurs until the entire explicit public source set validates.
    output.mkdir(mode=0o700)
    for name, data in files.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "adapter-manifest.json").open("xb") as stream:
        stream.write(raw)
    pin = hashlib.sha256(raw).hexdigest()
    verify(output, pin)
    return {"target": target, "adapter_version": VERSION, "manifest_sha256": pin, "files": len(files),
            "installed": False, "resource_registered": False, "job_started": False}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate manifest key")
        result[key] = value
    return result


def verify(output, pin):
    output = output_path(output)
    require(output.resolve() == output, "canonical bundle directory required")
    require(isinstance(pin, str) and re.fullmatch("[0-9a-f]{64}", pin), "external manifest SHA256 required")
    raw = regular(output / "adapter-manifest.json")
    require(hashlib.sha256(raw).hexdigest() == pin, "selected manifest changed")
    manifest = json.loads(raw, object_pairs_hook=unique)
    require(set(manifest) == {"format_version", "adapter_version", "target", "files"}, "manifest fields differ")
    require(type(manifest["format_version"]) is int and manifest["format_version"] == 1
            and manifest["adapter_version"] == VERSION, "unsupported bundle version")
    target = manifest["target"]
    require(target in CATALOG, "unknown bundle target")
    expected = {name for names in CATALOG[target].values() for name in names}
    require(isinstance(manifest["files"], dict) and set(manifest["files"]) == expected,
            "closed adapter source inventory differs")
    require({p.name for p in output.iterdir()} == expected | {"adapter-manifest.json"}, "unexpected bundle files")
    for name, digest in manifest["files"].items():
        require(hashlib.sha256(regular(output / name)).hexdigest() == digest, "adapter source changed: " + name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser("assemble")
    create.add_argument("target", choices=CATALOG)
    create.add_argument("output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("output", type=Path)
    check.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if args.operation == "assemble":
        print(json.dumps(assemble(args.target, args.output), sort_keys=True))
    else:
        manifest = verify(args.output, args.manifest_sha256)
        print(json.dumps({"verified": True, "target": manifest["target"], "files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
