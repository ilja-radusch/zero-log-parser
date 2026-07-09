"""Regression tests for P3: standalone script utils import fallback.

The standalone `zero_log_parser.py` must be able to load `zero_log_parser.utils`
helpers (time filtering, timezone application) in three environments:

  1. pip-installed package
  2. run from the repo-root CWD (src/ layout reachable)
  3. run as a standalone script from ANY cwd, not installed

Prior to the fix, sites used only `from src.zero_log_parser.utils import ...`,
which resolves only when CWD == repo root. Running the documented standalone
command from a foreign CWD failed with:
    "Time filtering requires package installation"
"""

import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # worktree root
SCRIPT = os.path.join(REPO, "zero_log_parser.py")


def _load_script_module():
    """Load zero_log_parser.py as a module by file path (independent of CWD)."""
    spec = importlib.util.spec_from_file_location("_zlp_script_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_standalone_time_filter_import_from_foreign_cwd(tmp_path):
    """Reproduce P3: --start when the repo root is NOT on sys.path must not hard-fail.

    Running ``python3 zero_log_parser.py`` by direct file path injects the script's
    own directory (the repo root) into ``sys.path[0]``, which happens to make the
    ``src.`` import resolve and masks the bug. The faithful reproduction of the
    reported symptom -- ``pip``-installed console entry point run from a foreign
    CWD -- has NO repo root on ``sys.path``. We reproduce that here by driving the
    script through ``runpy.run_path`` from a clean CWD (sys.path[0] == tmp CWD, not
    the repo root), with ``PYTHONPATH`` stripped and package NOT installed.
    """
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"\x00" * 32)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    driver = (
        "import runpy, sys\n"
        f"sys.argv = ['zero_log_parser.py', {str(dummy)!r}, '--start', '2020-01-01',"
        f" '-o', {str(tmp_path / 'out.txt')!r}]\n"
        f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    combined = r.stdout + r.stderr
    assert "Time filtering requires package installation" not in combined, combined
    assert "zero_log_parser.utils" not in combined or "Traceback" not in combined, combined


def test_get_timezone_offset_resolves_from_foreign_cwd(tmp_path):
    """P3 (same class): basic parsing calls get_timezone_offset unconditionally.

    With no repo root on sys.path (installed / foreign-CWD), the old 2-tier import
    silently pass-ed and left get_timezone_offset undefined, so any parse raised
    ``NameError: name 'get_timezone_offset' is not defined`` before doing any work.
    """
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"\x00" * 32)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    driver = (
        "import runpy, sys\n"
        f"sys.argv = ['zero_log_parser.py', {str(dummy)!r}, '-o', {str(tmp_path / 'out.txt')!r}]\n"
        f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    combined = r.stdout + r.stderr
    assert "name 'get_timezone_offset' is not defined" not in combined, combined
    assert "NameError" not in combined, combined


def test_load_utils_returns_module_with_all_functions():
    """`_load_utils()` must return a module exposing all four helpers."""
    mod = _load_script_module()
    assert hasattr(mod, "_load_utils"), "expected module-level _load_utils helper"
    utils = mod._load_utils()
    assert utils is not None, "_load_utils() returned None"
    for name in (
        "parse_time_range",
        "parse_time_filter_start",
        "parse_time_filter_end",
        "apply_timezone_to_datetime",
        "get_timezone_offset",
    ):
        assert hasattr(utils, name), f"utils module missing {name}"
