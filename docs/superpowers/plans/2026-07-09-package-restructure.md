# Package Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invert the package/monolith dependency and split the 3659-line `zero_log_parser.py` into focused package modules, so the tool works robustly whether pip-installed, run from repo root, or run as a standalone script from any cwd — with byte-identical output.

**Architecture:** All parsing logic moves *into* `src/zero_log_parser/` as importable modules (leaf → root: `constants`, `binary`, `parsing`, `gen2`, `gen3`, `emit`, `models`, `runner`). The root `zero_log_parser.py` becomes a thin, import-inert entry shim. `core.py`'s dynamic `importlib` loader, the monolith's 3-tier `_load_utils()` fallback, and the version-guess `try/except` are all deleted and replaced with plain relative imports. A golden-output test harness locks behavior before any code moves and gates every subsequent step.

**Tech Stack:** Python 3.10+ stdlib only (core), `pytest` (tests), `setuptools` src-layout, `ruff`/`black`/`mypy`.

## Global Constraints

- Python floor: `>=3.10`. No new runtime dependencies (core stays stdlib-only). Copied verbatim from `pyproject.toml`: `dependencies = []`.
- Output must remain **byte-identical** for txt/csv/tsv/json across all sample logs at every task boundary. The golden harness (Task 1) is the gate — no task is done until it is green.
- Do not commit `.bin` sample logs or golden snapshots derived from them — they contain real VINs (`log_data/` is gitignored). Golden baselines live under a gitignored dir and are recorded locally.
- Public import API preserved: `from zero_log_parser import LogData, parse_log` must keep working after `pip install -e .`.
- Standalone invocation preserved: `python3 zero_log_parser.py <file>.bin ...` must keep working from any cwd.
- Console scripts preserved: `zero-log-parser`, `zlp`, `zero-plotting`.
- No behavior/feature changes. This is a pure structural refactor.
- Commit after every task. Conventional Commits. No mention of AI tooling in messages.

## Decisions locked during planning

1. **Root file keeps the name `zero_log_parser.py`** (documented standalone entry) but becomes **import-inert**: it does real work only under `if __name__ == "__main__"`. Consequence: `import zero_log_parser` from an *uninstalled* repo-root cwd yields the inert shim, not the API. This is standard src-layout behavior — API consumers install the package. Documented in README.
2. **`emit_*` extraction** (Task 8) is the highest-risk step and is isolated as its own task with its own golden gate. `emit_*` become free functions in `emit.py`; `LogData.emit_*` methods delegate to them (signatures unchanged).
3. **Dedupe** `console_logger`, `default_parsed_output_for`, `is_log_file_path`, `logger_for_input` between the monolith and `utils.py`. **Verified they are NOT interchangeable** — the utils versions differ behaviorally:
   - `default_parsed_output_for` — identical; safe to drop the monolith copy.
   - `is_log_file_path` — utils accepts `.bin` **and** `.log`, case-insensitively; monolith is `.bin`-only. Different acceptance set.
   - `console_logger` — different signature (`verbose: bool` vs `verbosity_level=1, verbose=None`), different handler/level behavior.
   - `logger_for_input` — monolith returns a bare `logging.getLogger(bin_file)` (no handler → effectively silent); utils version attaches a StreamHandler at INFO. Swapping changes what prints to **stderr**.
   Because the golden harness only diffs the output **file**, stderr drift passes silently. Resolution: the standalone/runner path **keeps the monolith `console_logger`/`logger_for_input`/`is_log_file_path` semantics** (move them into `utils.py` under distinct names or as the canonical versions the runner imports), or reconcile deliberately and add a stderr check to the Task 9 smoke. Do NOT blindly adopt the utils versions.

## File Structure (target)

```
zero_log_parser.py            # ~12-line import-inert entry shim
src/zero_log_parser/
  __init__.py                 # __version__ (single source), public API re-exports
  constants.py                # ZERO_TIME_FORMAT, MBB_TIMESTAMP_GMT_OFFSET, EMPTY_CSV_VALUE, CSV_DELIMITER
  binary.py                   # BinaryTools, convert_*, hex_of_value, display_bytes_hex, print_value_tabular, is_vin
  parsing.py                  # improve_message_parsing, determine_log_level
  gen2.py                     # Gen2
  gen3.py                     # Gen3
  emit.py                     # emit_tabular / emit_json / emit_zero_compatible (free functions)
  models.py                   # ProcessedLogEntry, MismatchingVinError, LogFile, LogData
  runner.py                   # parse_log, parse_multiple_logs, generate_merged_output_name
  core.py                     # thin back-compat re-export shim (no importlib)
  cli.py, plot_cli.py, plotting.py, utils.py   # existing (imports repointed)
tests/
  conftest.py                 # sample-bin discovery + golden dir fixtures
  test_golden_output.py       # record-then-compare byte-identical guard
  golden/                     # gitignored recorded baselines
  test_p3_utils_import.py     # existing
```

**Dependency direction (imports only ever point down this list):**
`constants` → `utils` → `binary` → `parsing` → `gen2`/`gen3` → `emit` → `models` → `runner` → `core`/`cli`.

`utils.py` has **no internal imports** (verified) — it is a true leaf and may be imported by any module without creating a cycle. `models.py` (LogData) and `runner.py` (main-side helpers) both import from `.utils`.

---

### Task 1: Golden-output test harness (safety net, built FIRST)

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_golden_output.py`
- Modify: `.gitignore` (add `tests/golden/`)

**Interfaces:**
- Consumes: current `zero_log_parser.parse_log(bin_file, output_file, output_format=...)` via subprocess `python3 zero_log_parser.py`.
- Produces: `sample_bins` fixture (list of `Path`), `golden_dir` fixture (`Path`), and a parametrized test that records a baseline on first run and asserts byte-identity thereafter.

- [ ] **Step 1: Stage local sample bins (local-only, not committed)**

The worktree has no `.bin` files (gitignored). Copy a representative subset from the main checkout for local runs:

```bash
mkdir -p log_data
cp /Users/Shared/zero-log-parser/log_data/20240715_15.08_538DZAZ82PCN25101_MBB.bin \
   /Users/Shared/zero-log-parser/log_data/20240715_15.08_538DZAZ82PCN25101_BMS1.bin \
   /Users/Shared/zero-log-parser/log_data/538SDLZB2NCB19168_BmsD0_2025-08-03.bin \
   /Users/Shared/zero-log-parser/log_data/538SDLZB2NCB19168_MbbD_2025-08-02.bin \
   log_data/
git check-ignore log_data/20240715_15.08_538DZAZ82PCN25101_MBB.bin   # must print the path (ignored)
```

Expected: `git check-ignore` prints each path (confirming they will NOT be committed).

- [ ] **Step 2: Write conftest fixtures**

```python
# tests/conftest.py
import shutil
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "log_data"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
FORMATS = ["txt", "csv", "tsv", "json"]


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
```

- [ ] **Step 3: Write the golden test (record-then-compare)**

```python
# tests/test_golden_output.py
import subprocess
import sys
from pathlib import Path
import pytest

from conftest import REPO_ROOT, FORMATS

pytestmark = pytest.mark.usefixtures("sample_bins")


def _run(bin_path: Path, out_path: Path, fmt: str):
    # Invoke via the standalone entry to exercise the real user path.
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "zero_log_parser.py"),
         str(bin_path), "-f", fmt, "-o", str(out_path)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )


def _cases(sample_bins):
    return [(b, f) for b in sample_bins for f in FORMATS]


def test_output_matches_golden(sample_bins, golden_dir, tmp_path):
    failures = []
    for bin_path, fmt in _cases(sample_bins):
        name = f"{bin_path.stem}.{fmt}"
        produced = tmp_path / name
        _run(bin_path, produced, fmt)
        baseline = golden_dir / name
        if not baseline.exists():
            baseline.write_bytes(produced.read_bytes())  # record on first run
            continue
        if produced.read_bytes() != baseline.read_bytes():
            failures.append(name)
    assert not failures, f"output drifted from golden baseline: {failures}"
```

- [ ] **Step 4: Record baselines on the UNMODIFIED tree, then confirm stable**

Run: `.venv/bin/pytest tests/test_golden_output.py -v`
Expected: PASS (records baselines).
Run again: `.venv/bin/pytest tests/test_golden_output.py -v`
Expected: PASS (compares against just-recorded baselines — proves determinism; if the parser emits timestamps/paths that vary run-to-run, fix the harness to normalize before proceeding).

- [ ] **Step 5: Add golden dir to .gitignore**

Add line `tests/golden/` to `.gitignore`.
Run: `git check-ignore tests/golden/x.txt`
Expected: prints `tests/golden/x.txt`.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_golden_output.py .gitignore
git commit -m "test: add golden-output harness to guard the restructure"
```

---

### Task 2: Single-source the version

**Files:**
- Modify: `pyproject.toml` (`[project]` version → dynamic)
- Modify: `src/zero_log_parser/__init__.py` (keep `__version__`)
- Modify: `zero_log_parser.py:49-53` (delete version try/except)

**Interfaces:**
- Produces: `zero_log_parser.__version__` as the sole version string; `PARSER_VERSION` in the monolith sourced from it (temporary, until the monolith is emptied in later tasks).

- [ ] **Step 1: Make version dynamic in pyproject**

Replace `version = "2.3.0-dev"` under `[project]` with `dynamic = ["version"]` and add:

```toml
[tool.setuptools.dynamic]
version = {attr = "zero_log_parser.__version__"}
```

- [ ] **Step 2: Set the single source in `__init__.py`**

Ensure `src/zero_log_parser/__init__.py` contains exactly one assignment `__version__ = "2.3.0-dev"` (already present — confirm, do not duplicate).

- [ ] **Step 3: Repoint the monolith's version import**

In `zero_log_parser.py`, replace lines 49-53:

```python
# Parser version - try to import from package, fallback to hardcoded
try:
    from src.zero_log_parser import __version__ as PARSER_VERSION
except ImportError:
    PARSER_VERSION = "2.2.0"  # Fallback version
```

with:

```python
from zero_log_parser import __version__ as PARSER_VERSION
```

(After Task 4 the monolith runs only via the shim with `src/` on `sys.path`, so this resolves cleanly. If executed before Task 4 from repo root, the root file shadows the package — acceptable transiently; Task 4 fixes it.)

- [ ] **Step 4: Reinstall and verify version resolves once**

Run: `.venv/bin/pip install -q -e ".[dev]" && .venv/bin/python -c "import zero_log_parser as z; print(z.__version__)"`
Expected: prints `2.3.0-dev` (note: from repo-root cwd this imports the root file until Task 4; run from `/tmp` to confirm the package: `cd /tmp && <venv>/bin/python -c "import zero_log_parser as z; print(z.__version__)"`).

- [ ] **Step 5: Golden gate + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS.

```bash
git add pyproject.toml src/zero_log_parser/__init__.py zero_log_parser.py
git commit -m "build: single-source package version via dynamic metadata"
```

---

### Task 3: Extract leaf modules — `constants.py` + `binary.py`

**Files:**
- Create: `src/zero_log_parser/constants.py`
- Create: `src/zero_log_parser/binary.py`
- Modify: `zero_log_parser.py` (replace moved defs with imports)

**Interfaces:**
- Produces:
  - `constants.py`: `ZERO_TIME_FORMAT: str`, `MBB_TIMESTAMP_GMT_OFFSET: int`, `EMPTY_CSV_VALUE: str`, `CSV_DELIMITER: str`.
  - `binary.py`: `class BinaryTools`, `convert_mv_to_v(milli_volts: int) -> float`, `convert_ratio_to_percent(numerator, denominator) -> float`, `convert_bit_to_on_off(bit: int) -> str`, `hex_of_value(value)`, `display_bytes_hex(x)`, `print_value_tabular(value, omit_units=False)`, `is_vin(vin: str) -> bool`.
- Consumes: `binary.py` imports `EMPTY_CSV_VALUE` from `.constants`.

- [ ] **Step 1: Create `constants.py`**

Move the four constant definitions (`zero_log_parser.py:56`, `:58`, `:655`, `:656`) verbatim into `src/zero_log_parser/constants.py`.

- [ ] **Step 2: Create `binary.py`**

Move `BinaryTools` (366-530), `is_vin` (531-538), `convert_mv_to_v`/`convert_ratio_to_percent`/`convert_bit_to_on_off`/`hex_of_value`/`display_bytes_hex` (626-654), `print_value_tabular` (659-688) verbatim. Add at top:

```python
from .constants import EMPTY_CSV_VALUE, CSV_DELIMITER
```

Include the module imports each moved symbol needs (`struct`, `string`, `codecs`, `typing`), copied from the monolith header.

- [ ] **Step 3: Replace monolith defs with imports**

In `zero_log_parser.py`, delete the moved definitions and add near the top (after stdlib imports):

```python
from zero_log_parser.constants import (
    ZERO_TIME_FORMAT, MBB_TIMESTAMP_GMT_OFFSET, EMPTY_CSV_VALUE, CSV_DELIMITER,
)
from zero_log_parser.binary import (
    BinaryTools, is_vin, convert_mv_to_v, convert_ratio_to_percent,
    convert_bit_to_on_off, hex_of_value, display_bytes_hex, print_value_tabular,
)
```

- [ ] **Step 4: Golden gate**

Run: `.venv/bin/pytest -q`
Expected: PASS (byte-identical output; imports resolve).

- [ ] **Step 5: Commit**

```bash
git add src/zero_log_parser/constants.py src/zero_log_parser/binary.py zero_log_parser.py
git commit -m "refactor: extract constants and binary helpers into package modules"
```

---

### Task 4: Standalone sys.path bootstrap + collapse `_load_utils` + move REV constants + drop `core.py` importlib loader

**Files:**
- Modify: `zero_log_parser.py` (top-of-file `sys.path`; simplify `_load_utils`; move REV constants)
- Modify: `src/zero_log_parser/constants.py` (add REV0–REV4)
- Modify: `src/zero_log_parser/core.py` (delete importlib dynamic loader)

**Interfaces:**
- Produces: `constants.REV0..REV4` (five values); monolith `get_timezone_offset` sourced from `.utils`; `_load_utils()` reduced to a trivial normal-import wrapper (still used by `LogData` and `main()` until Tasks 8/9); `core.py` free of `importlib.util` path-walking.

**IMPORTANT (from review):** `_load_utils()` has **5 live callers** — `zero_log_parser.py:2637`, `:2670` (inside `LogData`, not moved until Task 8) and `:3618`, `:3628`, `:3637` (inside `main()`, not moved until Task 9). Do **NOT** delete `_load_utils` here — that would NameError every gate in Tasks 5–8. Reduce it to a wrapper instead.

- [ ] **Step 1: Guarantee `src/` is importable when run standalone**

At the very top of `zero_log_parser.py` (before the package imports added in Task 3), insert:

```python
import os, sys
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
```

- [ ] **Step 2: Reduce `_load_utils()` to a trivial wrapper and use direct import for the top-level offset**

Replace the whole 3-tier `_load_utils()` body (`:69-102`) with:

```python
def _load_utils():
    """The utils module is now a normal package import (src/ is on sys.path)."""
    import zero_log_parser.utils as _u
    return _u
```

Replace the module-level `_utils_module = _load_utils()` + `get_timezone_offset` fallback block (`:105-125`) with:

```python
from zero_log_parser.utils import get_timezone_offset
```

The 5 `_utils = _load_utils()` call sites keep working unchanged (they get the real utils module). They are cleaned up when their surrounding code moves (Task 8 for LogData, Task 9 for main).

- [ ] **Step 3: Move REV constants into `constants.py`**

Move all FIVE — `REV0..REV4` at `zero_log_parser.py:2373-2377` (note `REV4 = 4` exists and is used by `LogData` at `:2500, :2701, :2879, :2891`) — into `constants.py`, preserving the trailing comments. In the monolith, replace with:

```python
from zero_log_parser.constants import REV0, REV1, REV2, REV3, REV4
```

- [ ] **Step 4: Delete the importlib loader in `core.py`; re-export only moved leaves**

Replace `core.py` with a minimal, correct facade of what has actually moved so far — no `importlib`, no `__getattr__` placeholder:

```python
"""Public API facade. New code should import from the focused modules directly.
LogData/LogFile/parse_* are wired in here in Tasks 8-9."""
from .binary import BinaryTools, is_vin
from .constants import REV0, REV1, REV2, REV3, REV4
# TODO(Task 8-9): add LogData, LogFile, ProcessedLogEntry, MismatchingVinError,
# Gen2, Gen3, parse_log, parse_multiple_logs, generate_merged_output_name.
```

Anything currently importing the not-yet-moved names from `.core` (e.g. `plotting.py`, `cli.py`) must, for the interim, import them from the top-level `zero_log_parser` module (the monolith) — they resolve because `src/` is on the path and the console scripts install the package. The clean `.core` re-exports land in Task 9.

- [ ] **Step 5: Verify — pytest + leaf import from foreign cwd**

Run: `.venv/bin/pytest -q`
Expected: PASS.
Run: `cd /tmp && <venv>/bin/python -c "from zero_log_parser.binary import BinaryTools; from zero_log_parser.constants import REV4; print('ok', REV4)"`
Expected: `ok 4`.

- [ ] **Step 6: Standalone smoke from a foreign cwd (incl. a time-filter run that exercises `_load_utils` in LogData)**

```bash
cd /tmp && <venv>/bin/python <worktree>/zero_log_parser.py <sample>.bin -f json -o /tmp/out.json
cd /tmp && <venv>/bin/python <worktree>/zero_log_parser.py <sample>.bin --start "2020-01-01" -f csv -o /tmp/out.csv
```
Expected: both succeed, no `_load_utils`/NameError, valid output (the `--start` run proves `LogData`'s `_load_utils` calls at :2637/:2670 still resolve).

- [ ] **Step 7: Commit**

```bash
git add zero_log_parser.py src/zero_log_parser/core.py src/zero_log_parser/constants.py
git commit -m "refactor: drop dynamic loader and utils import fallback; add standalone sys.path bootstrap; centralize REV constants"
```

---

### Task 5: Extract `parsing.py`

**Files:**
- Create: `src/zero_log_parser/parsing.py`
- Modify: `zero_log_parser.py`

**Interfaces:**
- Produces: `improve_message_parsing(event_text, conditions_text=None, verbosity_level=1, logger=None) -> tuple`, `determine_log_level(message: str) -> str`.
- Consumes: whatever these use from `.binary`/`.constants` (add imports as the moved code requires; resolve by running the gate).

- [ ] **Step 1: Move `improve_message_parsing` (195-309) and `determine_log_level` (310-365) into `parsing.py`.** Add required imports (`re`, `json`, and any `from .binary import ...` the bodies reference).

- [ ] **Step 2: Replace in monolith with `from zero_log_parser.parsing import improve_message_parsing, determine_log_level`.**

- [ ] **Step 3: Golden gate.** Run: `.venv/bin/pytest -q` — Expected: PASS.

- [ ] **Step 4: Commit** — `git commit -am "refactor: extract message parsing into parsing.py"`

---

### Task 6: Extract `gen2.py`

**Files:**
- Create: `src/zero_log_parser/gen2.py`
- Modify: `zero_log_parser.py`

**Interfaces:**
- Produces: `class Gen2` with classmethods `parse_entry(entries, read_pos, ...)` and `interpolate_missing_timestamps(collected_entries, logger)` (exact signatures at `zero_log_parser.py:2505`, `:2535` call sites — preserve).
- Consumes: `from .binary import BinaryTools, print_value_tabular, convert_*`, `from .parsing import improve_message_parsing`, `from .constants import ...`.

- [ ] **Step 1: Move `Gen2` (689-2241) into `gen2.py`.** Add the import block for every module-level symbol its body references (derive from grep; the gate will surface any miss as `NameError`).

- [ ] **Step 2: Replace in monolith with `from zero_log_parser.gen2 import Gen2`.**

- [ ] **Step 3: Golden gate.** Run: `.venv/bin/pytest -q` — Expected: PASS.

- [ ] **Step 4: Commit** — `git commit -am "refactor: extract Gen2 parser into gen2.py"`

---

### Task 7: Extract `gen3.py`

**Files:**
- Create: `src/zero_log_parser/gen3.py`
- Modify: `zero_log_parser.py`

**Interfaces:**
- Produces: `class Gen3` (no `LogData`/`Gen2` back-references — confirmed by review).
- Consumes: `from .binary import BinaryTools`, `from .parsing import improve_message_parsing, determine_log_level` (Gen3 uses both at `zero_log_parser.py:2364-2365` — do NOT omit `.parsing`), `from .constants import ...` as its body requires.

- [ ] **Step 1: Move `Gen3` (2242-2372) into `gen3.py` with required imports (incl. `.parsing`).**

- [ ] **Step 2: Replace in monolith with `from zero_log_parser.gen3 import Gen3`.**

- [ ] **Step 3: Golden gate.** Run: `.venv/bin/pytest -q` — Expected: PASS.

- [ ] **Step 4: Commit** — `git commit -am "refactor: extract Gen3 parser into gen3.py"`

---

### Task 8: Extract `emit.py` + `models.py` (LogData, LogFile, ProcessedLogEntry, errors)

This is the highest-risk task. Split into two commits.

**Files:**
- Create: `src/zero_log_parser/emit.py`
- Create: `src/zero_log_parser/models.py`
- Modify: `zero_log_parser.py`, `src/zero_log_parser/core.py`, `src/zero_log_parser/plotting.py`

**CRITICAL (from review):** the three emit methods touch many `self.*` attributes, **not** just the collector, and they use **two different collectors**. Verified `self.*` usage across `zero_log_parser.py:3009-3261`:
`self.log_file` (×8), `self.timezone_offset` (×3), `self._get_processed_entries` (×2, tabular+json), `self._collect_and_process_entries` (×1, zero_compatible only), `self.output_time_field`, `self.output_line_number_field`, `self.header_info`, `self.header_divider`. Plus module global `PARSER_VERSION` at `:3093` (json). Therefore emit free functions **cannot** take only the entries list — they take the entries plus the metadata the body reads.

**Interfaces:**
- `emit.py` produces free functions. Signatures carry the needed context explicitly:
  - `emit_tabular(entries, output_file, *, log_file, out_format='tsv', logger=None, unnest=False) -> None`
  - `emit_json(entries, output_file, *, log_file, timezone_offset, parser_version, logger=None) -> None`
  - `emit_zero_compatible(entries, output_file, *, log_file, header_info, timezone_offset, output_time_field, output_line_number_field, header_divider, logger=None) -> None`
  - (Confirm the exact metadata each body reads while extracting; the list above is the reviewed set. If a body reads an attribute not listed, add it as a keyword arg — never reintroduce `self`.)
  - `emit.py` gets `PARSER_VERSION` via `from . import __version__ as PARSER_VERSION` (do not rely on a monolith global).
- `models.py` produces: `class ProcessedLogEntry` (dataclass, 129-193), `class MismatchingVinError` (61-67), `class LogFile` (539-625), `class LogData` (**2381-3371** — `parse_log` starts at 3373; `_get_vin`/`_merge_entries`/`__add__`/`__iadd__`/`__radd__` at 3262-3371 are LogData methods that STAY in models).
- `LogData.emit_*` keep their current signatures and delegate, passing `self.*` in explicitly. `LogData` keeps `_get_processed_entries` and `_collect_and_process_entries`.

- [ ] **Step 1 (commit A): Extract emit bodies to `emit.py` as free functions.** For each method, the body **after** the collector call moves to a free function; the collector call stays in the method. Note the collector differs: tabular/json use `self._get_processed_entries(start_time, end_time)`; zero_compatible uses `self._collect_and_process_entries(logger, start_time, end_time)`. Emit-body line ranges: tabular `3009-3077`, json `3078-3145`, zero_compatible `3146-3261`. Rewrite the methods, e.g.:

```python
def emit_tabular_decoding(self, output_file, out_format='tsv', logger=None,
                          start_time=None, end_time=None, unnest=False):
    entries = self._get_processed_entries(start_time, end_time)
    from zero_log_parser.emit import emit_tabular
    emit_tabular(entries, output_file, log_file=self.log_file,
                 out_format=out_format, logger=logger, unnest=unnest)

def emit_json_decoding(self, output_file, logger=None, start_time=None, end_time=None):
    entries = self._get_processed_entries(start_time, end_time)
    from zero_log_parser.emit import emit_json
    emit_json(entries, output_file, log_file=self.log_file,
              timezone_offset=self.timezone_offset,
              parser_version=PARSER_VERSION, logger=logger)

def emit_zero_compatible_decoding(self, output_file, logger=None, start_time=None, end_time=None):
    entries = self._collect_and_process_entries(logger, start_time, end_time)
    from zero_log_parser.emit import emit_zero_compatible
    emit_zero_compatible(entries, output_file, log_file=self.log_file,
                         header_info=self.header_info, timezone_offset=self.timezone_offset,
                         output_time_field=self.output_time_field,
                         output_line_number_field=self.output_line_number_field,
                         header_divider=self.header_divider, logger=logger)
```

(These `LogData.emit_*` rewrites are applied in place in the monolith now; the whole class moves to `models.py` in Step 4.)

- [ ] **Step 2: Golden gate (emit refactor is the risky one).** Run: `.venv/bin/pytest -q` — Expected: PASS byte-identical. If any format drifts, `git diff` the emit function against the original method body and reconcile before continuing.

- [ ] **Step 3: Commit A** — `git commit -am "refactor: extract output emitters into emit.py as free functions"`

- [ ] **Step 4 (commit B): Move `ProcessedLogEntry`, `MismatchingVinError`, `LogFile`, `LogData` into `models.py`** with imports: `from .binary import ...`, `from .parsing import ...`, `from .gen2 import Gen2`, `from .gen3 import Gen3`, `from .emit import emit_tabular, emit_json, emit_zero_compatible`, `from .constants import ...`.

- [ ] **Step 5: In the monolith, replace those class defs with** `from zero_log_parser.models import ProcessedLogEntry, MismatchingVinError, LogFile, LogData`.

- [ ] **Step 6: Repoint `plotting.py`** `from .core import LogData, LogFile, MismatchingVinError` — verify `core.py` now re-exports these (next step) so no change needed, OR point directly at `.models`.

- [ ] **Step 7: Golden gate.** Run: `.venv/bin/pytest -q` — Expected: PASS.

- [ ] **Step 8: Commit B** — `git commit -am "refactor: move LogData/LogFile/ProcessedLogEntry into models.py"`

---

### Task 9: Extract `runner.py`, finalize `core.py`, dedupe against `utils.py`, collapse the shim

**Files:**
- Create: `src/zero_log_parser/runner.py`
- Rewrite: `src/zero_log_parser/core.py`
- Rewrite: `zero_log_parser.py` (final ~12-line shim)
- Modify: `src/zero_log_parser/cli.py` (imports), `src/zero_log_parser/utils.py` (dedupe target)

**Interfaces:**
- `runner.py` produces: `parse_log(...)`, `parse_multiple_logs(...)`, `generate_merged_output_name(bin_files, output_format='txt')` — exact signatures from `zero_log_parser.py:3373`, `:3475`, `:3447`.
- `utils.py` is the single home for `console_logger`, `default_parsed_output_for`, `is_log_file_path`, `logger_for_input`.
- `core.py` re-exports the stable public API: `LogData, LogFile, BinaryTools, Gen2, Gen3, REV0..REV3, is_vin, MismatchingVinError, parse_log, parse_multiple_logs, generate_merged_output_name`.

- [ ] **Step 1: Dedupe the duplicated IO helpers — reconcile, do NOT blindly adopt utils versions (see Decision 3).** Verified verdicts:
  - `default_parsed_output_for` — identical → delete monolith copy, import from `.utils`.
  - `is_log_file_path`, `console_logger`, `logger_for_input` — behaviorally DIFFERENT (acceptance set, signature, stderr handler). The standalone/runner path must preserve **monolith** semantics so output and stderr do not drift. Make `utils.py` host the canonical versions the runner uses (keep monolith behavior; if `cli.py`'s current use of the utils variants relied on `.log` acceptance or the INFO handler, verify that path still behaves — capture stderr in Step 6). Note every reconciled delta in the commit body.

- [ ] **Step 2: Move `parse_log` (3373), `parse_multiple_logs` (3475), `generate_merged_output_name` (3447) into `runner.py`,** importing `from .models import LogData, LogFile, MismatchingVinError`, `from .utils import console_logger, default_parsed_output_for, logger_for_input`, `from .constants import ...`.

- [ ] **Step 3: Rewrite `core.py` as the clean public facade:**

```python
"""Public API facade for zero_log_parser."""
from .binary import BinaryTools, is_vin
from .constants import REV0, REV1, REV2, REV3
from .models import LogData, LogFile, ProcessedLogEntry, MismatchingVinError
from .gen2 import Gen2
from .gen3 import Gen3
from .runner import parse_log, parse_multiple_logs, generate_merged_output_name

__all__ = [
    "BinaryTools", "is_vin", "REV0", "REV1", "REV2", "REV3",
    "LogData", "LogFile", "ProcessedLogEntry", "MismatchingVinError",
    "Gen2", "Gen3", "parse_log", "parse_multiple_logs", "generate_merged_output_name",
]
```

- [ ] **Step 4: Point `cli.py` `main()` as the single CLI entry.** Confirm `cli.py:main` provides every flag the monolith `main()` did (`grep` the monolith arg parser at 3578 and diff argument names against `cli.py`). Port any missing flag. The monolith `main()` is then deleted.

- [ ] **Step 5: Collapse `zero_log_parser.py` to the final inert shim:**

```python
#!/usr/bin/env python3
"""Standalone entry point for zero-log-parser.

Real code lives in the `zero_log_parser` package under src/. This file only
bootstraps sys.path so the package is importable when the script is run
directly (from any working directory) without installation, then delegates
to the package CLI. It is import-inert: importing it does nothing.
"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
    from zero_log_parser.cli import main
    main()
```

- [ ] **Step 6: Full gate — golden + pytest + three-environment smoke.**

```bash
.venv/bin/pytest -q                                                        # golden + unit
cd /tmp && <venv>/bin/zero-log-parser <sample>.bin -f json -o /tmp/a.json 2>/tmp/a.err  # installed console script
cd /tmp && <venv>/bin/python <worktree>/zero_log_parser.py <sample>.bin -f json -o /tmp/b.json 2>/tmp/b.err  # standalone, foreign cwd
cd <worktree> && .venv/bin/python zero_log_parser.py <sample>.bin -f json -o /tmp/c.json 2>/tmp/c.err         # repo-root cwd
diff /tmp/a.json /tmp/b.json && diff /tmp/b.json /tmp/c.json                # all three stdout-file identical
diff /tmp/b.err /tmp/c.err                                                  # stderr unchanged across cwd (catches logger_for_input drift)
```
Expected: all PASS, all three output files identical and identical to the Task 1 golden baseline, and stderr identical (guards the Step 1 dedupe against silent logger drift the golden file-diff misses).

- [ ] **Step 7: Commit** — `git commit -am "refactor: extract runner, finalize core facade, dedupe IO helpers, collapse root to inert shim"`

---

### Task 10: Cleanup — requirements, README, mypy/ruff/black, docs

**Files:**
- Delete: `requirements.txt` (redundant — deps live in `pyproject.toml`, core has none)
- Modify: `README.md`, `CLAUDE.md` (architecture section), `pyproject.toml` (`black`/`ruff` no longer need to target the root file specially)

- [ ] **Step 1: Remove `requirements.txt`.** The worktree skill's setup path references it; note that `pip install -e ".[dev]"` is the supported path. Update any doc that says `pip install -r requirements.txt`.

- [ ] **Step 2: Update README + CLAUDE.md** to describe the new module layout and drop the "dynamically imports and wraps the standalone script" description. Document Decision 1 (import-inert shim; install the package for API use).

- [ ] **Step 3: Run formatters/linters/type-check across the new modules.**

```bash
.venv/bin/black src/ zero_log_parser.py
.venv/bin/ruff check src/ zero_log_parser.py
.venv/bin/mypy src/
```
Fix only import-ordering / unused-import fallout from the moves (no logic changes).

- [ ] **Step 4: Final golden gate + full smoke (repeat Task 9 Step 6).** Expected: PASS, byte-identical.

- [ ] **Step 5: Commit** — `git commit -am "chore: drop redundant requirements.txt, refresh docs, lint the split modules"`

---

## Self-Review notes

- **Spec coverage:** Robustness (Tasks 2,4,9 — kill fallbacks/loader, inert shim, three-env smoke) ✔; full module split (Tasks 3,5,6,7,8,9) ✔; golden safety net (Task 1, gated every task) ✔; requirements update (Task 10) ✔.
- **Ordering risk:** `core.py` cannot cleanly re-export `LogData`/`parse_log` until Tasks 8-9. Task 4 explicitly defers those re-exports and keeps interim imports working; Task 9 delivers the clean facade. Flagged inline.
- **Byte-identity risk:** concentrated in Task 8 (emit extraction) — isolated as its own commit with a dedicated gate.
- **Sample-bin / VIN privacy:** golden baselines are gitignored and recorded locally; harness skips when no bins present so CI stays green. Noted in Global Constraints.
- **Open verification:** Task 1 Step 4 must confirm output is deterministic run-to-run (no embedded wall-clock/tmp paths). If not, normalize in the harness before any code moves — otherwise every later gate is unreliable.

## Adversarial review incorporated (2026-07-09)

Plan revised after a code-verified adversarial review. Fixes applied:
- **`.utils` coupling** — `LogData` (`:2637,:2670`) and `main()` (`:3618,:3628,:3637`) call `_load_utils()`. Task 4 no longer deletes it (would NameError Tasks 5–8); reduced to a normal-import wrapper, deleted in Task 9. `utils` added to the dependency order (verified leaf, no internal imports).
- **`REV4` exists** (`:2377`, used at `:2500,:2701,:2879,:2891`) — Task 4 now moves REV0–REV4 (all five); `core.py` facade exports REV4.
- **emit extraction premise corrected** — the three emitters touch `self.log_file/timezone_offset/header_info/header_divider/output_*_field`, `PARSER_VERSION`, and **two** collectors (`_get_processed_entries` vs `_collect_and_process_entries`). Task 8 free-function signatures now carry that context explicitly.
- **IO-helper dedupe** — `is_log_file_path`/`console_logger`/`logger_for_input` differ behaviorally from `utils.py`; Task 9 preserves monolith semantics and adds a stderr diff to the smoke (golden file-diff misses stderr).
- **Line ranges tightened** — Gen3 `2242-2372`, LogData `2381-3371`, emit bodies `3009-3077 / 3078-3145 / 3146-3261`; `_get_vin`/merge/`__add__` (`3262-3371`) stay in `models.py`.
- **Task 4 `__getattr__` placeholder removed** — muddled interim `core.py` design replaced with a clean leaf-only re-export.
- **Gen3 imports `.parsing`** (uses `improve_message_parsing`/`determine_log_level` at `:2364-2365`).
- **cli.py is a superset** of monolith `main()` (adds `-t`, `--version`) — no missing flags; note additions in Task 9 commit body.
