import json
import pytest
from config.btc_maker import get_default_params, write_overrides, read_overrides
import config.btc_maker as btc_maker_module


def test_get_default_params_has_required_keys():
    params = get_default_params()
    for key in ("gamma", "k", "tau", "q_max", "signal_gain",
                "requote_threshold_ticks", "requote_min_interval_ms", "vol_breaker"):
        assert key in params, f"Missing key: {key}"


def test_get_default_params_values_match_class_defaults():
    params = get_default_params()
    assert params["gamma"] == 0.1
    assert params["tau"] == 10.0
    assert params["signal_gain"] == 0.30
    assert params["requote_min_interval_ms"] == 500


def test_write_and_read_overrides_roundtrip(tmp_path, monkeypatch):
    overrides_file = str(tmp_path / "overrides.json")
    monkeypatch.setattr(btc_maker_module, "OVERRIDES_PATH", overrides_file)
    write_overrides({"gamma": 0.15, "tau": 12.0})
    result = read_overrides()
    assert result == {"gamma": 0.15, "tau": 12.0}


def test_read_overrides_missing_returns_empty_dict(tmp_path, monkeypatch):
    overrides_file = str(tmp_path / "overrides.json")
    monkeypatch.setattr(btc_maker_module, "OVERRIDES_PATH", overrides_file)
    assert read_overrides() == {}


def test_write_overrides_creates_valid_json(tmp_path, monkeypatch):
    overrides_file = str(tmp_path / "overrides.json")
    monkeypatch.setattr(btc_maker_module, "OVERRIDES_PATH", overrides_file)
    write_overrides({"signal_gain": 0.45})
    data = json.loads(open(overrides_file).read())
    assert data == {"signal_gain": 0.45}
