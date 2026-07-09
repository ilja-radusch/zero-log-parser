# Vehicle config: manual speed amendment

Zero MBB logs record `motor_rpm` but no road speed. Speed is a linear function
of motor RPM — `speed_kmh = factor × motor_rpm` — where the factor depends on the
vehicle's wheel circumference and final-drive (belt) ratio. Supply that factor
per vehicle and the parser adds `speed_kmh` / `speed_mph` to every entry that
carries `motor_rpm`, across all output formats (txt, csv, tsv, json, `--unnest`).

## Config file

JSON, keyed by **model code** (as read from the log header, e.g. `DS11`), with an
optional `default` section used when the model does not match. A starting point
ships as `zero-vehicles.example.json` — copy it to `zero-vehicles.json` to use
auto-discovery:

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

1. `speed_kmh_per_rpm` present → use it directly.
2. Otherwise derive from wheel + gearing:
   `factor = wheel_circumference_m × 60 / 1000 ÷ gear_ratio`.

Vehicle match order: exact model code → `default` section → no amendment.

## CLI

```bash
# Explicit config file
zero-log-parser log.bin --vehicle-config zero-vehicles.json -f json

# Auto-discovery (used when --vehicle-config is omitted):
#   ./zero-vehicles.json  then  ~/.config/zero-log-parser/vehicles.json
zero-log-parser log.bin -f json

# Inline one-off factor, overrides any config
zero-log-parser log.bin --speed-factor 0.0284 -f csv
```

Precedence: `--speed-factor` > matched model section > `default` section > none.
When no factor resolves, no speed fields are added and output is unchanged.

## Finding the factor for your bike

If you do not know the belt ratio, calibrate empirically from the log itself:
average speed over a ride = odometer distance ÷ time; divide by the mean
`motor_rpm` over the same interval to get `speed_kmh_per_rpm`. A Zero DS 2022
(`DS11`) on standard wheels calibrates to ≈ `0.0284`.
