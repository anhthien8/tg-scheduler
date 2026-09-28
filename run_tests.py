#!/usr/bin/env python3
"""Unified test runner for tg-scheduler.

Runs BOTH the Python suite (root test_*.py + tests/**, collected via pytest) and
the frontend JS tests (node --test). Previously only tests/ ran, silently
skipping 6 root unittest files and all JS tests.

Usage:
    python run_tests.py            # full suite (Python + JS)
    python run_tests.py --fast     # skip tests marked slow
    python run_tests.py --py-only  # Python only
    python run_tests.py --js-only  # JS only
    python run_tests.py --cov      # with coverage report
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
JS_TESTS = [
    os.path.join("tests", "test_polling.js"),
    os.path.join("tests", "test_polling_integration.js"),
    os.path.join("tests", "test_channels_cache.js"),
]
# Node-based JS tests that also live under standalone_tests/ (collected by node,
# not pytest).
JS_STANDALONE = [
    os.path.join("standalone_tests", "test_api_runtime.mjs"),
]
# Lifecycle/split-runtime tests that purge+reload global modules and mutate
# os.environ. Each file must run in its OWN pytest process to observe a clean
# interpreter, so we invoke them one file at a time.
STANDALONE_TESTS = [
    os.path.join("standalone_tests", name)
    for name in (
        "test_internal_identity.py",
        "test_ipc_vertical.py",
        "test_pool_cancel_leak.py",
        "test_reaction_watcher_safety.py",
        "test_runtime_commands.py",
        "test_split_runtime.py",
    )
]


def _pytest(common, target) -> int:
    """Run pytest in a subprocess so each target gets a fresh interpreter.

    A subprocess (not pytest.main()) is essential: the lifecycle tests purge
    and reload global modules and mutate os.environ, so reusing one interpreter
    across targets leaks state (e.g. tests/conftest's telegram_client mock)
    into the standalone run.
    """
    print(f"\n### pytest: {target} ###")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *common, target],
        cwd=ROOT,
    )
    return result.returncode


def run_python(fast: bool, cov: bool) -> int:
    """Run the Python suite: tests/ once, then each standalone file alone."""
    common = ["-v", "--tb=short", "-p", "no:cacheprovider"]
    if fast:
        common += ["-m", "not slow"]
    if cov:
        common += [
            "--cov=.",
            "--cov-report=term-missing",
            "--cov-config=pyproject.toml",
            "--cov-fail-under=0",
        ]

    rc = _pytest(common, "tests")
    for rel in STANDALONE_TESTS:
        rc = max(rc, _pytest(common, rel))
    return rc


def run_js() -> int:
    node = shutil.which("node")
    if not node:
        print("! node not found — skipping JS tests (install Node.js to run them)")
        return 0
    rc = 0
    for rel in JS_TESTS + JS_STANDALONE:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print(f"! missing JS test: {rel}")
            continue
        print(f"\n=== node --test {rel} ===")
        result = subprocess.run([node, "--test", path], cwd=ROOT)
        rc = rc or result.returncode
    return rc


def main() -> None:
    argv = sys.argv[1:]
    fast = "--fast" in argv
    py_only = "--py-only" in argv
    js_only = "--js-only" in argv
    cov = "--cov" in argv

    print("=" * 60)
    print("Starting full test suite for tg-scheduler...")
    print("=" * 60)

    codes = {}
    if not js_only:
        codes["python"] = run_python(fast, cov)
    if not py_only:
        codes["js"] = run_js()

    print("=" * 60)
    for name, code in codes.items():
        print(f"  {name:8s}: {'PASS' if code == 0 else 'FAIL'} (exit {code})")
    print("=" * 60)

    sys.exit(max(codes.values()) if codes else 0)


if __name__ == "__main__":
    main()
