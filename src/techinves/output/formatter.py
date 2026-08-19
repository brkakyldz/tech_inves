"""Renders a ScoreBlock into the exact text format specified in
report_scoring_metadology.md Section 9.
"""

from __future__ import annotations

from techinves.models import CategoryName, Regime, ScoreBlock
from techinves.scoring.debug import TickerDebugReport

_CATEGORY_LABELS: dict[CategoryName, str] = {
    "valuation": "Valuation",
    "growth": "Growth",
    "quality": "Profitability & Quality",
    "financial_health": "Financial Health",
}

_CATEGORY_ORDER: tuple[CategoryName, ...] = ("valuation", "growth", "quality", "financial_health")


_NO_DATA = "n/a"


def _num(value: float | None, decimals: int = 1) -> str:
    """Renders a score, or `n/a` when it was never computed. Never substitutes
    0 for a missing value -- see ADR 0001 clause 6.
    """
    return _NO_DATA if value is None else f"{value:.{decimals}f}"


def format_score_block(block: ScoreBlock) -> str:
    categories_by_name = {c.name: c for c in block.categories}
    regime_label = "Profitable" if block.regime == Regime.PROFITABLE else "Unprofitable growth"

    lines = [
        f"Company: {block.ticker}",
        f"Cohort: {block.cohort.value}  |  Regime: {regime_label}",
        "",
        f"COMPOSITE SCORE: {_num(block.composite_score)}  (band: {block.composite_band})",
    ]
    for name in _CATEGORY_ORDER:
        cat = categories_by_name[name]
        label = _CATEGORY_LABELS[name]
        lines.append(f"  {label:<24}: {_num(cat.score)}  (weight {cat.weight * 100:.0f}%)")

    percentile = (
        _NO_DATA if block.sector_percentile is None else f"{block.sector_percentile:.0f}th percentile"
    )
    lines += [
        "",
        f"RISK INDICATOR: {_num(block.risk.score)}  (band: {block.risk.band})",
        f"  Altman Z'' zone: {block.risk.altman_zone.value}",
        f"  Piotroski F-Score: {block.risk.piotroski_f if block.risk.piotroski_f is not None else 'N/A'}",
        "",
        f"SECTOR PERCENTILE: {percentile}  (Cohort {block.cohort.value}, n={block.cohort_size})",
        f"DATA COVERAGE: {block.coverage_pct * 100:.0f}%   |   Warnings applied: "
        + (", ".join(block.warnings) if block.warnings else "none"),
    ]
    return "\n".join(lines)


def format_debug_report(report: TickerDebugReport) -> str:
    """Single-ticker, no-percentile inspection view -- see scoring/debug.py.
    Not a Section 9 score block; intentionally labeled as such so it's never
    mistaken for a real (cohort-normalized) score.
    """
    regime_label = "Profitable" if report.regime == Regime.PROFITABLE else "Unprofitable growth"
    facts = report.facts

    lines = [
        f"[DEBUG] Single-ticker inspection -- NOT a full methodology score (no cohort percentiles)",
        f"Company: {report.ticker}",
        f"Cohort: {report.cohort.value}  |  Regime: {regime_label}",
        f"Annual periods fetched: {len(facts.annual)}  |  Quarterly periods fetched: {len(facts.quarterly)}",
        f"Missing endpoints: {', '.join(facts.missing_endpoints) if facts.missing_endpoints else 'none'}",
        "",
        "Raw metric values (pre-percentile):",
    ]
    for m in sorted(report.metrics, key=lambda m: (m.category, m.name)):
        if m.available:
            value = f"{m.raw_value:.4g}" if m.raw_value is not None else "n/a"
            lines.append(f"  [{m.category:<16}] {m.name:<32} = {value}  ({m.direction})")
        else:
            lines.append(
                f"  [{m.category:<16}] {m.name:<32} = UNAVAILABLE"
                f"  ({m.reason_unavailable or 'no reason given'})"
            )
    return "\n".join(lines)


def format_watchlist_report(blocks: dict[str, ScoreBlock]) -> str:
    """Groups blocks by cohort, sorted by ticker within each cohort."""
    by_cohort: dict[str, list[ScoreBlock]] = {}
    for block in blocks.values():
        by_cohort.setdefault(block.cohort.value, []).append(block)

    sections = []
    for cohort_code in sorted(by_cohort):
        cohort_blocks = sorted(by_cohort[cohort_code], key=lambda b: b.ticker)
        section = "\n\n".join(format_score_block(b) for b in cohort_blocks)
        sections.append(f"=== Cohort {cohort_code} ===\n\n{section}")

    return "\n\n\n".join(sections)
