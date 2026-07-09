"""Golden-output regression guard.

Runs the standalone entry point over every sample .bin in log_data/ for each
output format, and asserts the produced file is byte-identical to a recorded
baseline. Baselines are recorded on first run into tests/golden/ (gitignored,
never committed — sample logs contain real VINs). Skips entirely when no
sample bins are present so CI without fixtures stays green.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, FORMATS

pytestmark = pytest.mark.usefixtures("sample_bins")

# The JSON emitter stamps a wall-clock `generated_at`; neutralize it so the
# guard tracks parsing behavior, not the clock.
_GENERATED_AT = re.compile(rb'("generated_at":\s*")[^"]*(")')


def _normalize(data: bytes, fmt: str) -> bytes:
    if fmt == "json":
        data = _GENERATED_AT.sub(rb"\1<normalized>\2", data)
    return data


def _run(bin_path: Path, out_path: Path, fmt: str) -> None:
    # Invoke via the standalone entry to exercise the real user path.
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "zero_log_parser.py"),
            str(bin_path),
            "-f",
            fmt,
            "-o",
            str(out_path),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_output_matches_golden(sample_bins, golden_dir, tmp_path):
    recorded = []
    failures = []
    for bin_path in sample_bins:
        for fmt in FORMATS:
            name = f"{bin_path.stem}.{fmt}"
            produced = tmp_path / name
            _run(bin_path, produced, fmt)
            produced_bytes = _normalize(produced.read_bytes(), fmt)
            baseline = golden_dir / name
            if not baseline.exists():
                baseline.write_bytes(produced_bytes)  # record on first run
                recorded.append(name)
                continue
            if produced_bytes != baseline.read_bytes():
                failures.append(name)
    assert not failures, f"output drifted from golden baseline: {failures}"
    if recorded:
        # First-run recording is not a pass/fail signal; surface it for visibility.
        print(f"recorded {len(recorded)} golden baselines: {recorded}")
