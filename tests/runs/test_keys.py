"""`techinves.runs.keys` -- the single key-presence check shared by
`api/routers/runs.py`, `GET /v1/meta`, `api/main.py`'s seed-on-empty step and
the `pipeline.run` CLI (Faz 6, ADR 0010 §7-8)."""

from __future__ import annotations

from techinves.runs.keys import (
    REQUIRED_KEYS_BY_TRIGGER,
    app_mode,
    missing_keys_by_trigger,
    missing_required_key,
)

ALL_REQUIRED = ("FMP_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY")


def _clear_all(monkeypatch):
    for name in (*ALL_REQUIRED, "FRED_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _set_all_required(monkeypatch):
    for name in ALL_REQUIRED:
        monkeypatch.setenv(name, "x")


def test_optional_keys_are_never_in_the_required_set():
    """ADR 0010 §8: FRED_API_KEY/EXA_API_KEY must never block a run."""
    for keys in REQUIRED_KEYS_BY_TRIGGER.values():
        assert "FRED_API_KEY" not in keys
        assert "EXA_API_KEY" not in keys


def test_app_mode_is_live_when_every_trigger_has_what_it_needs(monkeypatch):
    _clear_all(monkeypatch)
    _set_all_required(monkeypatch)
    assert app_mode() == "live"
    assert missing_keys_by_trigger() == {}
    assert missing_required_key("scores") is None
    assert missing_required_key("report") is None
    assert missing_required_key("company") is None


def test_app_mode_is_demo_when_any_trigger_is_missing_a_key(monkeypatch):
    _clear_all(monkeypatch)
    assert app_mode() == "demo"
    assert missing_keys_by_trigger() == {
        "scores": "FMP_API_KEY",
        "report": "FMP_API_KEY",
        "company": "FMP_API_KEY",
    }


def test_optional_keys_absent_never_flips_mode_to_demo(monkeypatch):
    """ADR 0010 §8, proven directly against the classification function
    rather than only through an HTTP round trip."""
    _clear_all(monkeypatch)
    _set_all_required(monkeypatch)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    assert app_mode() == "live"


def test_missing_required_key_returns_the_first_gap_in_declared_order(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    # OPENAI_API_KEY still absent -- it's second in REQUIRED_KEYS_BY_TRIGGER["report"].
    assert missing_required_key("report") == "OPENAI_API_KEY"


def test_unknown_trigger_type_has_no_requirements():
    assert missing_required_key("not-a-real-trigger") is None
