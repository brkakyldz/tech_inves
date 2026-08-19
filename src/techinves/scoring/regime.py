"""Profitable vs. unprofitable-growth regime detection --
report_scoring_metadology.md Section 6: "A company is assigned to the
'unprofitable growth regime' if GAAP operating income has been negative for
the last four quarters."
"""

from __future__ import annotations

from techinves.models import RawFinancialFacts, Regime


def detect_regime(facts: RawFinancialFacts) -> Regime:
    last_4q = [q for q in facts.quarterly[:4] if q.operating_income is not None]
    if len(last_4q) == 4:
        if all(q.operating_income < 0 for q in last_4q):
            return Regime.UNPROFITABLE_GROWTH
        return Regime.PROFITABLE

    # Insufficient quarterly history (e.g. recent IPO) -- fall back to the
    # latest annual operating income rather than guessing.
    if facts.annual and facts.annual[0].operating_income is not None:
        return (
            Regime.UNPROFITABLE_GROWTH
            if facts.annual[0].operating_income < 0
            else Regime.PROFITABLE
        )

    # No usable data at all: default to the base (profitable) metric set.
    # The resulting low coverage % will surface the data gap on its own.
    return Regime.PROFITABLE
