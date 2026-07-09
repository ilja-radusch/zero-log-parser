# Vehicle-specific speed amendment on export

Date: 2026-07-09
Status: approved

## Problem

Zero MBB logs record `motor_rpm` but no vehicle speed. Speed is derivable as a
linear factor of `motor_rpm` (`speed_kmh = factor * motor_rpm`), where the factor
depends on wheel circumference and final-drive (belt) ratio — i.e. it is
vehicle-specific. Users want speed columns in exported logs, configured per
vehicle via a config file, with an inline override for one-offs.

Empirical calibration on a Zero DS 2022 (model code `DS11`) from odometer deltas:
`factor ≈ 0.0284 km/h per rpm` (68 ride-bouts, tight cluster).

## Config file (JSON)

JSON chosen to stay dependency-free and Python 3.10 compatible (TOML `tomllib`
needs 3.11 / an extra dependency).

```json
{
  "default": { "speed_kmh_per_rpm": 0.0284 },
  "DS11": {
    "description": "Zero DS 2022, standard wheels",
    "speed_kmh_per_rpm": 0.0284,
    "wheel_circumference_m": 2.01,
    "gear_ratio": 4.31
  }
}
```

Per-vehicle factor resolution (**factor wins**):
1. If `speed_kmh_per_rpm` present → use it.
2. Else if `wheel_circumference_m` and `gear_ratio` present →
   `factor = wheel_circumference_m * 60 / 1000 / gear_ratio`.
3. Else the entry is invalid → treated as no match.

Vehicle match order:
1. Exact model code (e.g. `DS11`), read from the log header (`sys_info['Model']`).
2. Else the `default` section, if present.
3. Else no factor → no speed fields emitted, output unchanged (a single warning is logged).

## CLI

- `--vehicle-config PATH` — explicit config file.
- Auto-discovery when the flag is absent: `./zero-vehicles.json`, then
  `~/.config/zero-log-parser/vehicles.json`. First existing wins.
- `--speed-factor FLOAT` — inline km/h-per-rpm; highest precedence, bypasses config
  and model matching entirely.

Precedence: `--speed-factor` > matched config section > `default` section > none.

## Output

For every processed entry whose `structured_data` contains a numeric `motor_rpm`,
two fields are added:
- `speed_kmh = round(motor_rpm * factor, 1)`
- `speed_mph = round(speed_kmh * 0.621371, 1)`

Applied once in the centralized entry pipeline, so it flows into txt, csv, tsv,
json, and `--unnest` automatically. When no factor resolves, nothing is added and
existing output is byte-for-byte unchanged (backward compatible).

TXT formatter gains unit rendering for `*_kmh` (`km/h`) and `*_mph` (`mph`) keys.

## Code changes

- New `src/zero_log_parser/speed.py`:
  - `load_vehicle_config(path) -> dict`
  - `discover_config() -> str | None`
  - `resolve_factor(config, model_code, inline=None) -> float | None`
  - `amend_entries(entries, factor) -> int` (mutates `structured_data`, returns count)
- `LogData` (`models.py`):
  - `model_code` property → `header_info.get('Model')`.
  - `apply_speed(factor)` → amends cached `_processed_entries`, stores `self.speed_factor`
    so the lazy fallback path also amends.
  - `_collect_and_process_entries` applies `self.speed_factor` when set.
- `runner.py`: after building the (merged) `LogData`, resolve the factor from the
  model code + config/inline and call `apply_speed` before emitting. New params
  `vehicle_config` and `speed_factor` on `parse_log` / `parse_multiple_logs`.
- `cli.py`: add `--vehicle-config` and `--speed-factor`, thread through.

## Tests

- Factor-wins vs derived-from-physical resolution (assert derived ≈ 0.028).
- Model match / `default` fallback / no-match (no fields added).
- Inline override precedence over config.
- `amend_entries` adds both fields only where `motor_rpm` is numeric.
- End-to-end: parse a DS11 fixture → JSON contains `speed_kmh` / `speed_mph`.

## Docs

- `docs/vehicle_config.md`, example `zero-vehicles.json`, AGENTS.md + CHANGELOG updates.
