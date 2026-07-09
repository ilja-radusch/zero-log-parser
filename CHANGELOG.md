# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Vehicle-specific speed amendment on export.** Zero MBB logs record
  `motor_rpm` but no road speed; the parser can now derive `speed_kmh` /
  `speed_mph` for every entry carrying `motor_rpm`.
  - `--vehicle-config PATH` loads a JSON config keyed by model code (e.g.
    `DS11`), with a `default` fallback section. Each section supplies either an
    explicit `speed_kmh_per_rpm` factor (wins) or `wheel_circumference_m` +
    `gear_ratio` to derive it.
  - Auto-discovery of `./zero-vehicles.json` or
    `~/.config/zero-log-parser/vehicles.json` when the flag is omitted.
  - `--speed-factor FLOAT` inline override, highest precedence.
  - Speed fields flow into txt/csv/tsv/json and `--unnest`; TXT renders `km/h`
    and `mph` units. Output is unchanged when no factor resolves.
  - See `docs/vehicle_config.md`.
