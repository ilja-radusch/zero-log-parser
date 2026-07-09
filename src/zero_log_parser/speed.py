"""Vehicle-specific speed amendment.

Zero MBB logs record ``motor_rpm`` but no vehicle speed. Speed is a linear
function of motor RPM, ``speed_kmh = factor * motor_rpm``, where the factor
depends on the vehicle's wheel circumference and final-drive (belt) ratio. This
module loads a per-vehicle JSON config, resolves the km/h-per-rpm factor for a
given model code, and amends processed log entries with ``speed_kmh`` /
``speed_mph`` fields.

The config is keyed by model code (e.g. ``"DS11"``) as read from the log header,
with an optional ``"default"`` section used when no model matches.
"""

import json
import os
from typing import Optional

# Conversion constant: 1 km/h = 0.621371 mph
_KMH_TO_MPH = 0.621371

def _discovery_paths():
    """Auto-discovery search paths, first existing wins (evaluated per call)."""
    return [
        os.path.join(os.getcwd(), "zero-vehicles.json"),
        os.path.expanduser("~/.config/zero-log-parser/vehicles.json"),
    ]


def load_vehicle_config(path: str) -> dict:
    """Load and validate a vehicle config JSON file.

    Raises FileNotFoundError if the path does not exist, ValueError if the
    contents are not a JSON object.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Vehicle config not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Vehicle config must be a JSON object, got {type(data).__name__}"
        )
    return data


def discover_config() -> Optional[str]:
    """Return the first existing auto-discovery config path, or None."""
    for candidate in _discovery_paths():
        if os.path.isfile(candidate):
            return candidate
    return None


def _factor_from_section(section: dict) -> Optional[float]:
    """Resolve a km/h-per-rpm factor from a single config section.

    Factor wins: an explicit ``speed_kmh_per_rpm`` takes precedence; otherwise
    derive it from ``wheel_circumference_m`` and ``gear_ratio``. Returns None if
    neither is usable.
    """
    if not isinstance(section, dict):
        return None

    explicit = section.get("speed_kmh_per_rpm")
    if isinstance(explicit, (int, float)) and explicit > 0:
        return float(explicit)

    circ = section.get("wheel_circumference_m")
    ratio = section.get("gear_ratio")
    if (
        isinstance(circ, (int, float))
        and isinstance(ratio, (int, float))
        and circ > 0
        and ratio > 0
    ):
        # motor_rpm / ratio = wheel rpm; * circ = m/min; * 60/1000 = km/h.
        return circ * 60.0 / 1000.0 / ratio

    return None


def resolve_factor(
    config: Optional[dict],
    model_code: Optional[str],
    inline: Optional[float] = None,
) -> Optional[float]:
    """Resolve the km/h-per-rpm factor for a vehicle.

    Precedence: inline override > matched model section > ``default`` section >
    None. Returns None when nothing resolves (no speed should be emitted).
    """
    if isinstance(inline, (int, float)) and inline > 0:
        return float(inline)

    if not config:
        return None

    if model_code and model_code in config:
        factor = _factor_from_section(config[model_code])
        if factor is not None:
            return factor

    if "default" in config:
        return _factor_from_section(config["default"])

    return None


def amend_entries(entries, factor: Optional[float]) -> int:
    """Add ``speed_kmh`` / ``speed_mph`` to entries carrying ``motor_rpm``.

    Mutates each entry's ``structured_data`` dict in place. Returns the number of
    entries amended. A None/non-positive factor is a no-op.
    """
    if not factor or factor <= 0 or not entries:
        return 0

    amended = 0
    for entry in entries:
        data = getattr(entry, "structured_data", None)
        if not data:
            continue
        rpm = data.get("motor_rpm")
        if not isinstance(rpm, (int, float)) or isinstance(rpm, bool):
            continue
        speed_kmh = round(rpm * factor, 1)
        data["speed_kmh"] = speed_kmh
        data["speed_mph"] = round(speed_kmh * _KMH_TO_MPH, 1)
        amended += 1
    return amended
