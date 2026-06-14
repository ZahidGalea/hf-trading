from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path
import pytest
from agentops.runner import RunnerConfig, find_latest_db, get_effective_params
import config.btc_maker as btc_maker_module


def _make_db(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE order_events (
            id INTEGER PRIMARY KEY, ts_ns INTEGER NOT NULL,
            event_type TEXT NOT NULL, order_side TEXT,
            fill_qty REAL, fill_price REAL, inventory_qty REAL, reject_reason TEXT
        );
        CREATE TABLE requotes (id INTEGER PRIMARY KEY, ts_ns INTEGER NOT NULL);
    """)
    t0 = 10_000_000_000
    for i in range(10):
        conn.execute(
            "INSERT INTO order_events (ts_ns, event_type) VALUES (?, 'submitted')",
            (t0 + i * 1_000_000_000,),
        )
    conn.commit()
    conn.close()
    return str(path)


def test_find_latest_db_returns_most_recent(tmp_path):
    db1 = tmp_path / "trading_20260614_100000.db"
    db2 = tmp_path / "trading_20260614_110000.db"
    db1.touch()
    time.sleep(0.01)
    db2.touch()
    result = find_latest_db(logs_dir=str(tmp_path))
    assert result == str(db2)


def test_find_latest_db_raises_when_empty(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"No trading_\*\.db"):
        find_latest_db(logs_dir=str(tmp_path))


def test_get_effective_params_uses_defaults_when_no_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(btc_maker_module, "OVERRIDES_PATH", str(tmp_path / "overrides.json"))
    from config.btc_maker import get_default_params
    params = get_effective_params()
    defaults = get_default_params()
    assert params["gamma"] == defaults["gamma"]
    assert params["tau"] == defaults["tau"]


def test_get_effective_params_applies_overrides(tmp_path, monkeypatch):
    overrides_file = tmp_path / "overrides.json"
    overrides_file.write_text(json.dumps({"gamma": 0.25}))
    monkeypatch.setattr(btc_maker_module, "OVERRIDES_PATH", str(overrides_file))
    params = get_effective_params()
    assert params["gamma"] == 0.25
    assert params["tau"] == 10.0   # non-overridden key keeps its default


def test_runner_config_defaults():
    cfg = RunnerConfig()
    assert cfg.interval_s == 300
    assert cfg.dry_run is False
    assert cfg.max_cycles is None
    assert cfg.no_restart is False
    assert cfg.logs_dir == "logs"
