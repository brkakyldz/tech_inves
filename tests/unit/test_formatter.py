from datetime import datetime, timezone

from techinves.models import (
    AltmanZone,
    CategoryScore,
    Cohort,
    MetricPercentile,
    Regime,
    RiskSubScore,
    ScoreBlock,
)
from techinves.output.formatter import format_score_block


def _score_block() -> ScoreBlock:
    def cat(name, score, weight):
        return CategoryScore(
            name=name,
            score=score,
            weight=weight,
            metrics=[MetricPercentile(name="m", raw_value=1.0, percentile=80.0, weight_used=1.0)],
            coverage=1.0,
        )

    return ScoreBlock(
        ticker="NVDA",
        cohort=Cohort.HARDWARE_SEMI_SPACE,
        cohort_size=15,
        extended_cohort=False,
        regime=Regime.PROFITABLE,
        composite_score=82.345,
        composite_band="Strong",
        distress_ceiling_applied=False,
        categories=[
            cat("valuation", 70.0, 0.25),
            cat("growth", 85.0, 0.20),
            cat("quality", 90.0, 0.35),
            cat("financial_health", 75.0, 0.20),
        ],
        risk=RiskSubScore(
            score=88.0,
            band="Solid",
            altman_z=5.2,
            altman_zone=AltmanZone.SAFE,
            piotroski_f=8,
            net_debt_ebitda=-0.5,
            interest_coverage=20.0,
            cash_runway_months=None,
            burn_multiple=None,
            dilution_yoy_pct=0.01,
            components_used=["altman_z", "piotroski_f"],
        ),
        sector_percentile=91.0,
        sector_percentile_band="Top tier",
        coverage_pct=0.95,
        low_reliability=False,
        warnings=[],
        generated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )


def test_format_score_block_exact_text():
    text = format_score_block(_score_block())
    expected = "\n".join(
        [
            "Company: NVDA",
            "Cohort: B  |  Regime: Profitable",
            "",
            "COMPOSITE SCORE: 82.3  (band: Strong)",
            "  Valuation               : 70.0  (weight 25%)",
            "  Growth                  : 85.0  (weight 20%)",
            "  Profitability & Quality : 90.0  (weight 35%)",
            "  Financial Health        : 75.0  (weight 20%)",
            "",
            "RISK INDICATOR: 88.0  (band: Solid)",
            "  Altman Z'' zone: Safe",
            "  Piotroski F-Score: 8",
            "",
            "SECTOR PERCENTILE: 91th percentile  (Cohort B, n=15)",
            "DATA COVERAGE: 95%   |   Warnings applied: none",
        ]
    )
    assert text == expected


def test_format_score_block_shows_warnings_when_present():
    block = _score_block()
    block.warnings = ["low coverage", "distress ceiling applied"]
    text = format_score_block(block)
    assert "Warnings applied: low coverage, distress ceiling applied" in text


def test_format_score_block_handles_missing_piotroski():
    block = _score_block()
    block.risk.piotroski_f = None
    text = format_score_block(block)
    assert "Piotroski F-Score: N/A" in text
