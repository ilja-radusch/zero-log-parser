import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "log_data"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
FORMATS = ["txt", "csv", "tsv", "json"]

# Make the tests dir importable so `from conftest import ...` works in test modules.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def sample_bins():
    bins = sorted(LOG_DIR.glob("*.bin")) if LOG_DIR.is_dir() else []
    if not bins:
        pytest.skip("no sample .bin files in log_data/ (gitignored)")
    return bins


@pytest.fixture(scope="session")
def golden_dir():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    return GOLDEN_DIR
