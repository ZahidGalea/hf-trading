import json
import pytest
from agentops.history import record_cycle, read_history


def test_record_creates_file(tmp_path):
    path = str(tmp_path / "history.jsonl")
    record_cycle(
        cycle=1,
        ts_iso="2026-06-14T15:00:00Z",
        session_db="logs/trading_X.db",
        duration_min=5.0,
        metrics={"net_pnl": -0.34},
        params_before={"gamma": 0.1},
        params_after={"gamma": 0.12},
        adjustment_reason="spread too narrow",
        bot_restarted=True,
        path=path,
    )
    lines = open(path).readlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["cycle"] == 1
    assert obj["metrics"]["net_pnl"] == -0.34
    assert obj["params_after"]["gamma"] == 0.12
    assert obj["bot_restarted"] is True


def test_record_appends_multiple_cycles(tmp_path):
    path = str(tmp_path / "history.jsonl")
    for i in range(3):
        record_cycle(
            cycle=i + 1,
            ts_iso="2026-06-14T15:00:00Z",
            session_db="logs/x.db",
            duration_min=5.0,
            metrics={},
            params_before={},
            params_after={},
            adjustment_reason=None,
            bot_restarted=False,
            path=path,
        )
    lines = open(path).readlines()
    assert len(lines) == 3
    assert json.loads(lines[2])["cycle"] == 3


def test_read_history_returns_all_records(tmp_path):
    path = str(tmp_path / "history.jsonl")
    record_cycle(
        cycle=1,
        ts_iso="2026-06-14T15:00:00Z",
        session_db="logs/x.db",
        duration_min=5.0,
        metrics={"net_pnl": 0.1},
        params_before={"gamma": 0.1},
        params_after={"gamma": 0.12},
        adjustment_reason="test",
        bot_restarted=True,
        path=path,
    )
    history = read_history(path)
    assert len(history) == 1
    assert history[0]["cycle"] == 1
    assert history[0]["params_after"]["gamma"] == 0.12


def test_read_history_missing_file_returns_empty(tmp_path):
    result = read_history(str(tmp_path / "nonexistent.jsonl"))
    assert result == []


def test_record_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "history.jsonl")
    record_cycle(
        cycle=1,
        ts_iso="2026-06-14T15:00:00Z",
        session_db="logs/x.db",
        duration_min=5.0,
        metrics={},
        params_before={},
        params_after={},
        adjustment_reason=None,
        bot_restarted=False,
        path=path,
    )
    assert len(open(path).readlines()) == 1
