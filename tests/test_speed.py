"""Unit tests for vehicle-specific speed amendment (speed.py)."""

import json

import pytest

from zero_log_parser.speed import (
    amend_entries,
    discover_config,
    load_vehicle_config,
    resolve_factor,
)


class FakeEntry:
    """Minimal stand-in for ProcessedLogEntry (only structured_data matters)."""

    def __init__(self, structured_data):
        self.structured_data = structured_data


SAMPLE_CONFIG = {
    "default": {"speed_kmh_per_rpm": 0.03},
    "DS11": {
        "description": "Zero DS 2022",
        "speed_kmh_per_rpm": 0.0284,
        "wheel_circumference_m": 2.01,
        "gear_ratio": 4.31,
    },
    "PHYS": {"wheel_circumference_m": 2.01, "gear_ratio": 4.31},
    "EMPTY": {"description": "no usable params"},
}


def test_factor_wins_over_physical():
    # DS11 has both an explicit factor and physical params; explicit wins.
    assert resolve_factor(SAMPLE_CONFIG, "DS11", None) == 0.0284


def test_derived_from_physical():
    # PHYS has only wheel + ratio -> derived factor ~= 0.028.
    factor = resolve_factor(SAMPLE_CONFIG, "PHYS", None)
    assert factor == pytest.approx(2.01 * 60 / 1000 / 4.31)
    assert factor == pytest.approx(0.02799, abs=1e-4)


def test_default_fallback_on_unknown_model():
    assert resolve_factor(SAMPLE_CONFIG, "NOPE", None) == 0.03


def test_no_match_no_default():
    cfg = {"DS11": {"speed_kmh_per_rpm": 0.0284}}
    assert resolve_factor(cfg, "OTHER", None) is None


def test_empty_section_falls_through_to_default():
    # EMPTY has no usable params -> falls through to the default section.
    assert resolve_factor(SAMPLE_CONFIG, "EMPTY", None) == 0.03


def test_inline_override_beats_config():
    assert resolve_factor(SAMPLE_CONFIG, "DS11", 0.05) == 0.05


def test_inline_override_without_config():
    assert resolve_factor(None, None, 0.0284) == 0.0284


def test_resolve_none_when_nothing_available():
    assert resolve_factor(None, "DS11", None) is None


def test_amend_adds_both_fields():
    entries = [FakeEntry({"motor_rpm": 1000})]
    count = amend_entries(entries, 0.0284)
    assert count == 1
    sd = entries[0].structured_data
    assert sd["speed_kmh"] == 28.4
    assert sd["speed_mph"] == pytest.approx(28.4 * 0.621371, abs=0.05)


def test_amend_skips_entries_without_motor_rpm():
    entries = [
        FakeEntry({"state_of_charge_percent": 80}),
        FakeEntry(None),
        FakeEntry({"motor_rpm": 2000}),
    ]
    count = amend_entries(entries, 0.0284)
    assert count == 1
    assert "speed_kmh" not in entries[0].structured_data
    assert "speed_kmh" in entries[2].structured_data


def test_amend_ignores_bool_motor_rpm():
    # bool is a subclass of int; must not be treated as an rpm value.
    entries = [FakeEntry({"motor_rpm": True})]
    assert amend_entries(entries, 0.0284) == 0


def test_amend_noop_on_none_factor():
    entries = [FakeEntry({"motor_rpm": 1000})]
    assert amend_entries(entries, None) == 0
    assert "speed_kmh" not in entries[0].structured_data


def test_load_vehicle_config_roundtrip(tmp_path):
    path = tmp_path / "vehicles.json"
    path.write_text(json.dumps(SAMPLE_CONFIG))
    loaded = load_vehicle_config(str(path))
    assert loaded["DS11"]["speed_kmh_per_rpm"] == 0.0284


def test_load_vehicle_config_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_vehicle_config(str(tmp_path / "nope.json"))


def test_load_vehicle_config_not_object(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        load_vehicle_config(str(path))


def test_discover_config_prefers_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert discover_config() is None
    (tmp_path / "zero-vehicles.json").write_text("{}")
    assert discover_config() == str(tmp_path / "zero-vehicles.json")
