"""Reads a run's scores/financials from the techinves DB instead of
pipeline/fixtures/mock_data.py (Faz 5, reports/research/REPORTS_AND_PIPELINE_INTEGRATION_PLAN.md
§5). Reuses `techinves.db.session`'s connection layer only -- not the scoring
engine itself -- so the API and this pipeline read the same DB-committed
numbers instead of computing them twice. `pipeline/config.py`'s
"never import the scoring engine" boundary is unaffected: this module never
imports `techinves.scoring.*`.

Shape returned is a superset of `pipeline/fixtures/mock_data.py`'s
`MOCK_SCORES` / `MOCK_FINANCIALS` (same base keys, plus categories/risk
detail/regime/warnings per REPORT_SPEC.md §5.2) so
`pipeline/synthesis/prompts.py`'s `format_scores_block()` and the
`{{ticker.field}}` placeholder contract need no changes -- callers that only
read the original four fields are unaffected.

All display-precision rounding happens here, at the DB->pipeline boundary,
per REPORT_SPEC.md §7 -- neither the prompt nor the frontend performs
arithmetic or rounding on these values.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from techinves.db.models import CompanyRow, ScoreHistoryRow
from techinves.db.session import get_sessionmaker


async def _load_async(
    tickers: list[str], run_id: str | None
) -> tuple[dict[str, dict], dict[str, dict]]:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        # ADR 0010 §2: scores are keyed on the run that produced them. A
        # report run names no score run of its own -- it reads whatever the
        # last `scores` run left as current, which is exactly what
        # `companies.current_score_id` tracks. Passing `run_id` explicitly
        # pins the read to one historical run instead.
        join_on = (
            (ScoreHistoryRow.company_id == CompanyRow.id) & (ScoreHistoryRow.run_id == run_id)
            if run_id is not None
            else ScoreHistoryRow.id == CompanyRow.current_score_id
        )
        stmt = (
            select(CompanyRow, ScoreHistoryRow)
            .outerjoin(ScoreHistoryRow, join_on)
            .where(CompanyRow.ticker.in_(tickers))
            .options(
                selectinload(CompanyRow.cohort),
                selectinload(ScoreHistoryRow.categories),
                selectinload(ScoreHistoryRow.risk),
            )
        )
        rows = (await session.execute(stmt)).all()

    scores: dict[str, dict] = {}
    financials: dict[str, dict] = {}
    for company, score in rows:
        if score is None:
            # No score row for this ticker in this run -- REPORT_SPEC.md §6:
            # never silently dropped, always carries an explicit reason.
            scores[company.ticker] = {
                "missing": True,
                "reason": "data unavailable this run",
                "cohort": company.cohort.code,
            }
            financials[company.ticker] = {}
            continue

        categories = [
            {
                "category_name": cat.category_name,
                "score": _round_or_none(cat.score, 0),
                "weight": cat.weight,
                "coverage": cat.coverage,
                "metrics": [_round_metric(m) for m in (cat.metrics or [])],
            }
            for cat in score.categories
        ]
        risk = score.risk
        risk_block = (
            {
                "score": _round_or_none(risk.score, 0),
                "band": risk.band,
                "altman_z": _round_or_none(risk.altman_z, 2),
                "altman_zone": risk.altman_zone,
                "piotroski_f": risk.piotroski_f,
                "net_debt_ebitda": _round_or_none(risk.net_debt_ebitda, 1),
                "interest_coverage": _round_or_none(risk.interest_coverage, 1),
                "cash_runway_months": _round_or_none(risk.cash_runway_months, 1),
                "burn_multiple": _round_or_none(risk.burn_multiple, 2),
                "dilution_yoy_pct": _round_or_none(risk.dilution_yoy_pct, 1),
                "components_used": risk.components_used,
            }
            if risk is not None
            else None
        )

        scores[company.ticker] = {
            "composite_score": _round_or_none(score.composite_score, 0),
            "composite_band": score.composite_band,
            "risk_score": None if risk is None else _round_or_none(risk.score, 0),
            "risk_band": risk.band if risk is not None else None,
            "coverage_pct": round(score.coverage_pct * 100, 1),
            "cohort": company.cohort.code,
            "sector_percentile": _round_or_none(score.sector_percentile, 0),
            "sector_percentile_band": score.sector_percentile_band,
            "cohort_size": score.cohort_size,
            "extended_cohort": score.extended_cohort,
            "low_reliability": score.low_reliability,
            "regime": score.regime,
            "distress_ceiling_applied": score.distress_ceiling_applied,
            "warnings": score.warnings,
            "categories": categories,
            "risk": risk_block,
        }

        valuation_metrics = next(
            (cat.metrics for cat in score.categories if cat.category_name == "valuation"), []
        )
        raw_by_name = {m["name"]: m.get("raw_value") for m in valuation_metrics}
        financials[company.ticker] = {
            "forward_pe": _round_or_none(raw_by_name.get("forward_pe"), 1),
            "ev_ebitda": _round_or_none(raw_by_name.get("ev_ebitda"), 1),
            "net_debt_to_ebitda": _round_or_none(
                risk.net_debt_ebitda if risk is not None else None, 1
            ),
        }

    return scores, financials


def _round_metric_raw(value: float | None) -> float | int | None:
    """Display precision for a metric's raw value.

    Unlike every other field here, metrics span four orders of magnitude in
    one table -- interest coverage ~71, EV/EBITDA ~19, an SBC-adjusted FCF
    yield ~0.015 -- so a single fixed `ndigits` either buries the ratios in
    noise or collapses the yields to `0.01`. Precision therefore follows the
    value's scale.
    """
    if value is None:
        return None
    magnitude = abs(float(value))
    if magnitude >= 10:
        return _round_or_none(value, 1)
    if magnitude >= 1:
        return _round_or_none(value, 2)
    return _round_or_none(value, 3)


def _round_metric(metric: dict) -> dict:
    """One `categories[*].metrics` entry at display precision.

    This list was the one field that reached the pipeline as the scoring
    engine's raw floats -- `raw_value=19.038476037006337`,
    `percentile=80.3030303030303` -- and the score-attribution appendix
    (`pipeline/synthesis/render.py`) printed them verbatim into a published
    report. Rounded here rather than at render time, per the module
    docstring: this is the DB->pipeline boundary, and the verifier's
    number-leak scan derives its allowed-number set from exactly this dict,
    so rounding anywhere downstream would read as a fabricated figure.
    """
    rounded = dict(metric)
    if "raw_value" in rounded:
        rounded["raw_value"] = _round_metric_raw(rounded["raw_value"])
    if "percentile" in rounded:
        rounded["percentile"] = _round_or_none(rounded["percentile"], 1)
    if "weight_used" in rounded:
        rounded["weight_used"] = _round_or_none(rounded["weight_used"], 2)
    return rounded


def _round_or_none(value: float | None, ndigits: int) -> float | int | None:
    if value is None:
        return None
    if ndigits == 0:
        return int(round(value))
    return round(value, ndigits)


def load_scores_and_financials(
    tickers: list[str], run_id: str | None = None
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Sync wrapper -- `pipeline/run.py`'s `run_pipeline()` is sync.

    `run_id=None` (the normal case) reads each company's current score,
    whichever score run produced it."""
    return asyncio.run(_load_async(tickers, run_id))
