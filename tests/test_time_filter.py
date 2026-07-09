"""Regression test for the --start/--end/--start-end time filter.

The filter compared a float epoch (`ProcessedLogEntry.sort_timestamp`) against a
datetime boundary, raising:
    TypeError: '<' not supported between instances of 'float' and 'datetime.datetime'
for the csv/json/txt paths (which go through `_get_processed_entries`).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, FORMATS

pytestmark = pytest.mark.usefixtures("sample_bins")


def _run(bin_path: Path, out_path: Path, fmt: str, *extra):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "zero_log_parser.py"),
         str(bin_path), "-f", fmt, "-o", str(out_path), *extra],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )


def test_start_filter_does_not_crash(sample_bins, tmp_path):
    """--start must filter, not raise a float/datetime TypeError, for every format."""
    bin_path = sample_bins[0]
    for fmt in FORMATS:
        out = tmp_path / f"early.{fmt}"
        r = _run(bin_path, out, fmt, "--start", "2000-01-01")
        combined = r.stdout + r.stderr
        assert "not supported between instances of" not in combined, combined
        assert r.returncode == 0, combined


def test_start_end_shorthand_does_not_crash(sample_bins, tmp_path):
    """--start-end shorthand path must also filter without a TypeError."""
    bin_path = sample_bins[0]
    out = tmp_path / "period.csv"
    r = _run(bin_path, out, "csv", "--start-end", "January 2000")
    combined = r.stdout + r.stderr
    assert "not supported between instances of" not in combined, combined
    assert r.returncode == 0, combined
