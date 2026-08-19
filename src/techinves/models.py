"""Shared enums and data models used across the scoring engine.

Pydantic v2 throughout: this gives free JSON schema validation/serialization
(`--format json` on the CLI, and later reuse by the LangGraph `scoring` node
in reports/research/ARCHITECTURE_PROPOSAL.md, which is designed around Pydantic state).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Cohort(str, Enum):
    SOFTWARE_INTERNET = "A"
    HARDWARE_SEMI_SPACE = "B"
    IT_SERVICES_INFRA = "C"


class Regime(str, Enum):
    PROFITABLE = "profitable"
    UNPROFITABLE_GROWTH = "unprofitable_growth"


class AltmanZone(str, Enum):
    SAFE = "Safe"
    GREY = "Grey"
    DISTRESSED = "Distressed"
    UNAVAILABLE = "Unavailable"


CategoryName = Literal["valuation", "growth", "quality", "financial_health"]
Direction = Literal["higher_better", "lower_better"]


# --- Raw financial facts (financial-API-sourced only, per methodology Section 0) ---


class FxTranslation(BaseModel):
    """Traceability record for a period whose facts were translated from a
    non-USD reporting currency (ADR 0007). `None` on `PeriodFacts.fx` means
    the period's facts were already USD-native and never touched
    `data/fx_rates.py`.

    Carries both conventions ADR 0007 §2 defines, since a single period
    routinely needs both: duration facts (income statement, cash flow) use
    `average_rate` over `[average_rate_start, average_rate_end]`; instant
    facts (balance sheet) use `closing_rate` as of `closing_rate_date`,
    which can differ from the period's own `end` date under the ADR's §3
    bounded carry-forward rule -- recording the date actually used, not just
    the date asked for, is the point of this model.
    """

    source_currency: str
    rate_source: str = "ECB reference rate"
    average_rate: float | None = None
    average_rate_start: date | None = None
    average_rate_end: date | None = None
    closing_rate: float | None = None
    closing_rate_date: date | None = None


class PeriodFacts(BaseModel):
    """One annual or quarterly reporting period. Field names are normalized
    across XBRL concepts -- see data/edgar_facts.py for the us-gaap/ifrs-full
    -> here mapping. All fields are Optional[float]; a missing field means
    "the company did not tag this line item for this period in a form the
    mapping recognizes", not zero.
    """

    fiscal_date: date
    period: Literal["FY", "Q1", "Q2", "Q3", "Q4"]

    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None  # EBIT proxy
    net_income: float | None = None
    interest_expense: float | None = None
    income_tax_expense: float | None = None
    weighted_avg_diluted_shares: float | None = None

    total_assets: float | None = None
    total_liabilities: float | None = None
    total_current_assets: float | None = None
    total_current_liabilities: float | None = None
    retained_earnings: float | None = None
    total_stockholders_equity: float | None = None
    total_debt: float | None = None
    cash_and_equivalents: float | None = None
    long_term_debt: float | None = None

    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    stock_based_compensation: float | None = None
    # Nobody tags free cash flow -- it is a derived measure, not a filed line
    # item -- so this is always ocf + capex (capex carries a negative sign).
    free_cash_flow: float | None = None
    depreciation_and_amortization: float | None = None  # used to derive EBITDA = operating_income + D&A

    # ADR 0007: set only for a period whose facts were translated from a
    # non-USD reporting currency (ASML/EUR today). None for USD-native periods.
    fx: FxTranslation | None = None


class AnalystEstimate(BaseModel):
    """Always empty since ADR 0001 clause 4: no free provider serves analyst
    consensus, and the two metrics that consumed it (forward P/E, forward
    revenue growth) were dropped from the methodology rather than left
    permanently unavailable. Retained so a paid estimates source can be added
    without a model migration.
    """

    fiscal_date: date
    estimated_eps_avg: float | None = None
    estimated_revenue_avg: float | None = None


class RawFinancialFacts(BaseModel):
    ticker: str
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    price: float | None = None
    enterprise_value: float | None = None

    # EDGAR serves a company's entire filing history in the same single
    # request, so the depth here is a storage choice, not an API-cost one.
    annual: list[PeriodFacts] = Field(default_factory=list)  # newest first, up to 8y
    quarterly: list[PeriodFacts] = Field(default_factory=list)  # newest first, up to 8q
    analyst_estimates: list[AnalystEstimate] = Field(default_factory=list)

    fetched_at: datetime
    missing_endpoints: list[str] = Field(default_factory=list)


# --- Scoring intermediates ---


class MetricValue(BaseModel):
    """A single metric's raw (unnormalized) value for one company, before
    cohort percentile ranking. `available=False` means the metric could not
    be computed (per methodology Section 3.4) -- its weight will be
    redistributed to other metrics in the same category, not defaulted to 50.
    """

    name: str
    category: CategoryName | Literal["risk"]
    raw_value: float | None
    direction: Direction
    available: bool
    reason_unavailable: str | None = None
    base_weight: float  # within-category weight before redistribution


class MetricPercentile(BaseModel):
    name: str
    raw_value: float | None
    percentile: float | None  # 0-100 within cohort; None if unavailable
    weight_used: float  # within-category weight after Section 3.4 redistribution


class CategoryScore(BaseModel):
    name: CategoryName
    # 0-100, or None when NO metric in this category was computable. None is a
    # distinct "no data" state and must never be collapsed to 0.0 -- 0.0 means
    # "ranked last in the cohort on every metric", which is a real measurement.
    score: float | None
    weight: float  # cohort weight profile share, e.g. 0.30
    metrics: list[MetricPercentile]
    coverage: float  # fraction of this category's metrics that were computable


class RiskSubScore(BaseModel):
    score: float | None  # 0-100, higher = safer; None when no component was computable
    band: Literal["Solid", "Adequate", "Worth watching", "Fragile", "High risk", "No data"]
    altman_z: float | None
    altman_zone: AltmanZone
    piotroski_f: int | None  # 0-9, raw (not the 100/9-scaled composite contribution)
    net_debt_ebitda: float | None
    interest_coverage: float | None
    cash_runway_months: float | None  # regime-substituted component, unprofitable-growth only
    burn_multiple: float | None  # regime-substituted component, unprofitable-growth only
    dilution_yoy_pct: float | None
    components_used: list[str]


class ScoreBlock(BaseModel):
    """Matches report_scoring_metadology.md Section 9's output block."""

    ticker: str
    cohort: Cohort
    cohort_size: int
    extended_cohort: bool
    regime: Regime

    # None when the company had no usable metric at all (failed/empty fetch,
    # unlisted ticker, provider quota). ADR 0001 clause 6: "no data" must not
    # be published as 0.0 / "Very Weak" -- that is indistinguishable from a
    # genuinely terrible company. Check `insufficient_data` rather than
    # comparing the score to 0.
    composite_score: float | None
    composite_band: Literal["Strong", "Good", "Moderate", "Weak", "Very Weak", "No Data"]
    distress_ceiling_applied: bool

    categories: list[CategoryScore]  # 4, in Valuation/Growth/Quality/FinHealth order
    risk: RiskSubScore

    sector_percentile: float | None
    sector_percentile_band: str

    #: True when not a single metric (category or risk) was computable for this
    #: company. Downstream consumers must not treat such a block as a score:
    #: don't rank it, don't persist it, don't render a number for it.
    insufficient_data: bool = False

    coverage_pct: float
    low_reliability: bool
    warnings: list[str] = Field(default_factory=list)

    generated_at: datetime
