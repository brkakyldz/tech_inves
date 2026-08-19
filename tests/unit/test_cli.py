"""Tests the CLI's handling of an unknown/typo'd --ticker.

Before this fix, `score --ticker <unknown>` raised a bare, unhandled
KeyError: score_watchlist() silently drops tickers that aren't in
data/watchlist.yaml from its result dict (they never enter its `universe`),
so `score_watchlist(...)[ticker]` in _cmd_score blew up with no user-facing
message. `debug-ticker --ticker <unknown>` already raised a clear ValueError
from scoring/debug.py, but main() didn't catch it, so it still surfaced as an
unhandled traceback instead of a clean CLI error.
"""

from __future__ import annotations

import pytest

from techinves.cli import ScoringExcludedTickerError, UnknownTickerError, _require_known_ticker, main


def test_require_known_ticker_raises_for_unknown_ticker():
    with pytest.raises(UnknownTickerError, match="Unknown ticker 'NOTAREALTICKER'"):
        _require_known_ticker("NOTAREALTICKER")


def test_require_known_ticker_accepts_watchlist_ticker():
    # MSFT is Cohort A in data/watchlist.yaml -- should not raise.
    _require_known_ticker("MSFT")


def test_require_known_ticker_raises_distinct_error_for_scoring_excluded_ticker():
    # RKLB is in data/watchlist.yaml (cohort B) but listed under
    # scoring_excluded (ADR 0005 §5) -- this must NOT raise UnknownTickerError,
    # since RKLB genuinely is in the watchlist, just not scoreable.
    with pytest.raises(ScoringExcludedTickerError, match="RKLB is in the watchlist but excluded"):
        _require_known_ticker("RKLB")


def test_scoring_excluded_and_unknown_ticker_errors_are_distinguishable():
    # The two failure modes must produce different exception types (and
    # different messages) so a caller/user can tell "RKLB" and "ZZZZ" apart.
    with pytest.raises(ScoringExcludedTickerError) as excluded_exc:
        _require_known_ticker("RKLB")
    with pytest.raises(UnknownTickerError) as unknown_exc:
        _require_known_ticker("ZZZZ")
    assert str(excluded_exc.value) != str(unknown_exc.value)
    assert not issubclass(ScoringExcludedTickerError, UnknownTickerError)
    assert not issubclass(UnknownTickerError, ScoringExcludedTickerError)


def test_cli_score_unknown_ticker_reports_clean_error_not_keyerror(capsys, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    exit_code = main(["score", "--ticker", "NOTAREALTICKER"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Unknown ticker 'NOTAREALTICKER'" in err
    assert "not in watchlist" in err


def test_cli_debug_ticker_unknown_ticker_reports_clean_error(capsys, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    exit_code = main(["debug-ticker", "--ticker", "NOTAREALTICKER"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not in the watchlist" in err


def test_cli_score_excluded_ticker_reports_distinct_error(capsys, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    exit_code = main(["score", "--ticker", "RKLB"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "excluded from financial scoring" in err
    # Must not be misreported as a plain unknown ticker -- RKLB IS in the
    # watchlist (ADR 0005 §5), just not scoreable.
    assert "Unknown ticker" not in err


def test_cli_debug_ticker_excluded_ticker_reports_distinct_error(capsys, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "test-key")
    exit_code = main(["debug-ticker", "--ticker", "RKLB"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "excluded from financial scoring" in err
    assert "is not in the watchlist" not in err
