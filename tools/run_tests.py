#!/usr/bin/env python3
"""Run the required offline suite; empty discovery and skipped checks are failures."""

import argparse
from pathlib import Path
import sys
import unittest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", type=Path, default=Path(__file__).resolve().parents[1] / "tests")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(args.start_directory.resolve()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun == 0 or result.skipped or not result.wasSuccessful():
        print(f"Required test gate failed: run={result.testsRun}, skipped={len(result.skipped)}, failures={len(result.failures)}, errors={len(result.errors)}", file=sys.stderr)
        return 1
    print(f"Required test gate passed: {result.testsRun} tests, zero skips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
