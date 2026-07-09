# Zero Log Parser - Plotting Optimization Plan

**Status**: ✅ Implemented (Phase 0 + Phase 1/2) on branch `plotting-optimization`  
**Priority**: Medium  
**Measured Impact**: Plot-load path ~2x faster (round-trip was 53–64% of load time — see Phase 0 Results).  
**Created**: August 2025  
**Revised**: July 2026 (assessment against current code)  
**Implemented**: July 2026  
**Version**: Post-v2.3.0 centralized processing refactoring  

## Phase 0 Results (measured)

Benchmarked the load path on real logs (`_get_processed_entries` decode vs CSV round-trip):

| File | entries | decode | CSV write | read_csv | iterrows+json.loads | round-trip share |
|------|---------|--------|-----------|----------|---------------------|------------------|
| MBB  | 2204 | 0.030s | 0.016s | 0.004s | 0.032s | **63.7%** |
| BMS  | 2318 | 0.047s | 0.017s | 0.002s | 0.035s | **53.4%** |

The round-trip is dominated by the `pandas.iterrows()` + `json.loads()` expansion, not the
binary decode. Direct build from `ProcessedLogEntry` objects reproduces the decode-only time
(MBB 0.031s vs 0.082s total; BMS 0.050s vs 0.102s total) — **~2x faster load, round-trip fully
eliminated**. Decision gate (>15%) passed decisively; the prior 10–20% guess was too low.

## Implementation Summary

- `ZeroLogPlotter.__init__` gained an additive `log_data=None` parameter (public `str` path
  unchanged) enabling direct in-memory loading.
- `_load_from_binary` now decodes once into `LogData` and calls `_load_from_logdata` — no more
  binary → CSV → `read_csv` → `json.loads` round-trip and no temp file.
- New `_load_from_logdata` / `_build_dataframe_from_entries` consume `ProcessedLogEntry.structured_data`
  natively (Phase 2 folded in — no JSON re-parse).
- Multi-file merge (`_merge_using_logdata`) plots directly from the merged `LogData`; latest-date
  basename now derived from entries (`_extract_latest_date_from_logdata`). Dead
  `_extract_latest_date_from_csv` removed; temp CSV and its **§1b leak eliminated**.
- `_merge_csv_files_simple` (CSV-only fallback) now unlinks its temp file in a `finally` block,
  closing its success-path leak.
- Verified: all 8 plot types produce **identical output** (traces/x/y, incl. matching pre-existing
  exceptions) between the old CSV path and the new direct path across MBB/BMS/ring-buffer logs.

## Executive Summary

The plotting path can be simplified by consuming the centralized `ProcessedLogEntry` objects directly instead of round-tripping through CSV. The current implementation does **not** decode the binary twice — `parse_log()` decodes once — but it does pay a **serialize→deserialize round-trip**: in-memory structured data is written to a temporary CSV, re-read with `pd.read_csv()`, and the conditions column is re-parsed with `json.loads()`. That round-trip, its temp files (one of which leaks — see below), and the JSON re-parse are the avoidable costs. The binary decode itself is unchanged, so gains are bounded by the round-trip's share of total time, not by "halving" the work.

## Current Architecture Analysis

### Current Data Flow
1. **Binary files** → `parse_log()` → CSV conversion → `pd.read_csv()` → JSON parsing → plotting
2. **Multiple files** → `LogData.merge()` → `emit_tabular_decoding()` → temp CSV → `pd.read_csv()` → plotting
3. **CSV files** → direct `pd.read_csv()` → JSON parsing → plotting

### Current Issues & Inefficiencies

#### 1. **CSV Serialize/Deserialize Round-Trip** (not double binary decode)
- **Issue**: Binary is decoded **once** by `parse_log()` → `_collect_and_process_entries()`. The in-memory structured data is then serialized to a temp CSV, re-read via `pd.read_csv()`, and the conditions field re-parsed with `json.loads()`.
- **Impact**: Overhead = cost of CSV write + read + JSON parse. This is a fraction of total runtime because binary decode dominates (~6500 entries/file). **Measure in Phase 0** before assuming a magnitude.
- **Correction**: Earlier drafts claimed "parsed twice / 50-100% overhead." That is inaccurate — verified `parse_log` decodes the binary a single time (`plotting.py:411-419`).
- **Root Cause**: Plotting predates and doesn't consume the centralized `ProcessedLogEntry` objects.

#### 1b. **Temp-file leak (bug)**
- **Issue**: Multi-file merge path creates two `NamedTemporaryFile(delete=False)` handles (`plotting.py:152` and `:174`) but only one is `os.unlink`'d (`:220`). The other leaks.
- **Impact**: Orphaned `.csv` files accumulate in the temp dir per multi-file plot run.
- **Fix**: Removing the CSV round-trip eliminates all temp files; until then, unlink both.

#### 2. **Structured Data Re-parsing**
- **Issue**: CSV output contains JSON-encoded structured data that gets re-parsed during plotting
- **Impact**: Unnecessary JSON parsing overhead + potential parsing errors
- **Root Cause**: Plotting predates the `ProcessedLogEntry` with native structured data access

#### 3. **Temporary File Management**
- **Issue**: Binary → CSV conversion requires temporary files that need cleanup
- **Impact**: I/O overhead + cleanup complexity
- **Root Cause**: Plotting relies on CSV as intermediate format instead of direct memory access

#### 4. **Memory Usage**
- **Issue**: Full DataFrame storage + JSON re-parsing creates memory overhead
- **Impact**: Higher memory usage especially for large multi-file datasets
- **Root Cause**: No direct access to centralized `ProcessedLogEntry` objects

#### 5. **Feature Access Limitations**
- **Issue**: Plotting can't access enhanced structured data formatting from TXT output
- **Impact**: Missed opportunity for better plot labels and units
- **Root Cause**: Only accesses raw structured data, not formatted versions

## Optimization Opportunities

### 1. **Direct ProcessedLogEntry Integration** 
- **Opportunity**: Bypass CSV conversion by directly consuming `ProcessedLogEntry` objects
- **Benefit**: ~50% performance improvement, eliminate temporary files
- **Implementation**: Create `_load_from_processed_entries()` method

### 2. **Native Structured Data Access**
- **Opportunity**: Use `entry.structured_data` directly instead of JSON re-parsing
- **Benefit**: Better performance + access to parsed numeric types
- **Implementation**: Access structured data objects directly from entries

### 3. **Enhanced Unit Formatting**
- **Opportunity**: Leverage TXT formatting logic for better plot labels
- **Benefit**: Consistent unit display across output formats
- **Implementation**: Import and use the `format_structured_data()` function

### 4. **Memory Optimization**
- **Opportunity**: Stream processing instead of full DataFrame materialization
- **Benefit**: Lower memory usage for large datasets
- **Implementation**: Lazy evaluation of plot data

### 5. **Multi-file Processing Optimization**
- **Opportunity**: Direct merger results instead of CSV round-trip
- **Benefit**: Faster multi-file plotting
- **Implementation**: Accept merged `ProcessedLogEntry` list directly

## Detailed Implementation Plan

### **Phase 0: Benchmark first** ⭐ **DO BEFORE ANYTHING**

Do not commit to speed/memory targets until measured. Establish a baseline so the phases below are justified by numbers, not guesses.

```python
# Time and memory-profile the current path vs a throwaway direct path:
#   - single binary file (~6500 entries)
#   - multi-file merge (largest realistic set)
# Break down: binary decode vs CSV write vs pd.read_csv vs json.loads.
```

Decision gate: if the CSV round-trip is <15% of total runtime, Phase 1 is a cleanliness/bug-fix win, not a performance win — scope accordingly and drop the perf headline. Phases 3-4 proceed only if Phase 0 shows real memory/time pressure on actual datasets.

### **Phase 1: Direct ProcessedLogEntry Integration** ⭐ **HIGH VALUE (cleanliness + bug fix)**

Also fixes the temp-file leak (§1b) by removing CSV temp files entirely. Fold Phase 2 (structured-data access) into this phase — consuming `entry.structured_data` directly *is* the mechanism, not a separate step.

#### 1.1 Create New Data Loading Path
```python
def _load_from_processed_entries(self, processed_entries: List[ProcessedLogEntry]):
    """Load plotting data directly from processed entries (bypass CSV)."""
    # Convert ProcessedLogEntry objects to plot-ready format
    # Apply time filtering directly on ProcessedLogEntry objects
    # Separate by message type for plotting
```

#### 1.2 Add LogData Direct Interface  
```python
def _load_from_logdata(self, log_data: LogData):
    """Load data directly from LogData using centralized processing."""
    processed_entries = log_data._collect_and_process_entries(
        start_time=self.start_time, end_time=self.end_time
    )
    self._load_from_processed_entries(processed_entries)
```

#### 1.3 Update Constructor
```python
def __init__(self, input_source: Union[str, LogData, List[ProcessedLogEntry]], ...):
    # Support multiple input types: file paths, LogData objects, or ProcessedLogEntry lists
```

**Files to modify**:
- `src/zero_log_parser/plotting.py`: Main ZeroLogPlotter class
- `src/zero_log_parser/plot_cli.py`: CLI interface updates

### **Phase 2: Structured Data Access Optimization**

#### 2.1 Eliminate JSON Re-parsing
```python
def _extract_structured_fields(self, processed_entries):
    """Extract structured data fields directly without JSON parsing."""
    for entry in processed_entries:
        if entry.structured_data:
            # Direct access to parsed structured data
            yield entry.structured_data
        # No JSON parsing required!
```

#### 2.2 Enhanced Data Type Support
- Access numeric types directly (no string→float conversion)
- Better handling of missing fields
- Type-safe field access

**Files to modify**:
- `src/zero_log_parser/plotting.py`: Update `_load_from_csv()` method
- All `plot_*` methods: Use structured data directly

### **Phase 3: Performance & Memory Optimizations**

#### 3.1 Lazy DataFrame Construction
```python
def _build_plot_dataframes(self, processed_entries):
    """Build DataFrames on-demand for specific plot types only."""
    # Only materialize DataFrames for requested plot types
    # Reduce memory usage for large datasets
```

#### 3.2 Streaming Multi-file Processing
```python
@classmethod
def from_multiple_logdata(cls, log_data_objects: List[LogData], ...):
    """Direct multi-file processing without CSV round-trip."""
    # Process multiple LogData objects directly
    # Merge ProcessedLogEntry lists in memory
```

**Files to modify**:
- `src/zero_log_parser/plotting.py`: Optimize memory usage
- `src/zero_log_parser/plot_cli.py`: Multi-file processing

### **Phase 4: Enhanced Features**

#### 4.1 Smart Unit Formatting
```python
def _format_plot_labels(self, structured_data: dict):
    """Generate plot labels with proper units using TXT formatting logic."""
    # Leverage the format_structured_data() function from TXT output
    # Consistent unit display across formats
```

#### 4.2 Improved Error Handling
- Better handling of malformed structured data
- Graceful fallbacks for missing fields
- Enhanced logging and diagnostics

**Files to modify**:
- `src/zero_log_parser/plotting.py`: Enhanced plot labeling
- All `plot_*` methods: Better axis labels and units

## Expected Benefits

### **Performance Improvements** (bounded by round-trip share — confirm in Phase 0)
- **Binary files**: gain = CSV-write + read + `json.loads` time as a fraction of total. Realistic guess ~10-20%, **not** 50%. Binary decode is unchanged.
- **Multi-file processing**: larger relative gain than single-file (more serialization), still measure.
- **Memory usage**: some reduction from avoiding a full DataFrame + re-parsed JSON; magnitude unmeasured.

> Prior draft's 50%/30%/25% targets were removed as unsubstantiated. Replace with measured baselines from Phase 0.

### **Feature Enhancements**
- **Native structured data**: Type-safe access to all structured fields
- **Better plot labels**: Consistent unit formatting across all outputs
- **Enhanced reliability**: No JSON parsing errors

### **Architecture Benefits**
- **Consistent with refactoring**: Leverages centralized processing architecture
- **Future-proofing**: Direct access to any new ProcessedLogEntry features
- **Maintainability**: Single source of truth for data processing

### **Backward Compatibility**
- **Existing CSV plotting**: Preserved as fallback
- **API compatibility**: Mostly unchanged, **but** widening the constructor to `Union[str, LogData, List[ProcessedLogEntry]]` (`plotting.py:35`) is a public-signature change. Keep the `str` path as default and additive-only to avoid breaking callers.
- **Gradual migration**: Can be implemented incrementally

## Implementation Notes

### Dependencies
- Requires the centralized processing refactoring (v2.3.0+) to be completed
- `ProcessedLogEntry` dataclass must be available
- `_collect_and_process_entries()` method must be accessible

### Testing Strategy
- **Unit tests**: Test each phase independently
- **Integration tests**: Compare old vs new plotting results for identical output
- **Performance tests**: Benchmark improvements with real datasets
- **Memory tests**: Verify memory usage reduction

### Rollout Strategy
- **Phase 1**: Focus on binary file performance (highest impact)
- **Phase 2**: Improve structured data access
- **Phase 3**: Memory optimizations for large datasets
- **Phase 4**: Enhanced features and polish

### Risk Assessment
- **Low risk**: All changes maintain backward compatibility
- **Incremental**: Can be implemented and tested phase by phase
- **Rollback**: Original CSV-based approach remains as fallback

## Files to Modify

### Primary Files
- `src/zero_log_parser/plotting.py` - Main plotting logic
- `src/zero_log_parser/plot_cli.py` - CLI interface
- `src/zero_log_parser/core.py` - May need ProcessedLogEntry export

### Testing Files
- Create `test_plotting_optimization.py` - Performance and compatibility tests
- Update existing plotting tests

### Documentation Files
- Update `CLAUDE.md` - Add plotting optimization notes
- Update `README.md` - Update plotting performance claims

## Success Metrics

### Performance Targets (set from Phase 0 baseline — no pre-committed numbers)
- [ ] Phase 0 baseline captured (decode vs CSV-write vs read vs json.loads breakdown)
- [ ] Binary file plotting: beat baseline by the measured round-trip share
- [ ] Multi-file plotting: beat baseline by measured round-trip share
- [ ] Memory usage: measured reduction vs baseline
- [ ] Temp file elimination: 100% (no more CSV temporary files) — also closes the §1b leak

### Quality Targets
- [ ] 100% backward compatibility maintained
- [ ] All existing plot types produce identical output
- [ ] Enhanced plot labels with proper units
- [ ] Improved error handling and diagnostics

---

**Next Steps**: 
1. **Phase 0**: benchmark current path on real logs; break down where time goes. Decision gate.
2. Implement Phase 1 (direct `ProcessedLogEntry`, folds in Phase 2) + fix the §1b temp-file leak — worth doing for correctness/cleanliness regardless of Phase 0 numbers.
3. Validate backward compatibility (constructor stays additive) with identical-output comparison tests.
4. Revisit Phases 3-4 only if Phase 0 shows memory/time pressure on actual datasets.