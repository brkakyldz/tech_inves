"""Mock scores/financials input, standing in for the real scoring engine
(src/techinves, out of scope for this package -- see pipeline/__init__.py).
Shape loosely follows report_scoring_metadology.md's score block; only the
fields the synthesis/verifier nodes actually reference are populated."""

from __future__ import annotations

MOCK_SCORES: dict[str, dict] = {
    "NVDA": {
        "composite_score": 87.5,
        "risk_score": 62.0,
        "coverage_pct": 91,
        "cohort": "A",
    },
    "PLTR": {
        "composite_score": 71.0,
        "risk_score": 48.5,
        "coverage_pct": 55,
        "cohort": "B",
    },
}

MOCK_FINANCIALS: dict[str, dict] = {
    "NVDA": {
        "forward_pe": 34.2,
        "ev_ebitda": 28.9,
        "net_debt_to_ebitda": -0.4,
    },
    "PLTR": {
        "forward_pe": 61.7,
        "ev_ebitda": 55.1,
        "net_debt_to_ebitda": -1.2,
    },
}
