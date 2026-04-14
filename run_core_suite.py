from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _load_test_nodes(manifest_path: Path) -> list[str]:
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    tests = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tests.append(line)
    return tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_core_suite.py",
        description="Run NATLClaw core regression suite.",
    )
    parser.add_argument(
        "--manifest",
        default="core_suite_tests.txt",
        help="Path to newline-delimited pytest node IDs.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print selected node IDs and exit.",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"Core suite manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    test_nodes = _load_test_nodes(manifest_path)
    if not test_nodes:
        print(f"Core suite manifest is empty: {manifest_path}", file=sys.stderr)
        return 2

    if args.list:
        for node in test_nodes:
            print(node)
        return 0

    cmd = [sys.executable, "-m", "pytest", "-q", *test_nodes]
    print("Running core suite:")
    for node in test_nodes:
        print(f"  - {node}")
    print("")

    result = subprocess.run(cmd)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
