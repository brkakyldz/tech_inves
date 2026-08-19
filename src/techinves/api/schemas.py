"""API response models.

Deliberately separate from `techinves.models` (the engine's internal
contract): the four naming/shape mismatches between the engine and the
front-end (`reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 3.4) are resolved
here, once, via `alias_generator=to_camel` -- neither the engine nor
`front-end/lib/data/types.ts` needs to change.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# Engine category key -> front-end display label (BACKEND_IMPLEMENTATION_PLAN.md §3.4).
CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "valuation": "Valuation",
    "growth": "Growth",
    "quality": "Profitability & Quality",
    "financial_health": "Financial Health",
}

COHORT_LABELS: dict[str, str] = {
    "A": "Software & Internet",
    "B": "Hardware, Semiconductor & Space",
    "C": "IT Services & Infrastructure",
}


class MetricPercentileOut(CamelModel):
    """R26: the per-metric layer -- raw value, cohort percentile, and
    post-redistribution weight -- computed by the scoring engine
    (`techinves.models.MetricPercentile`) and persisted to
    `category_scores.metrics`, but previously dropped at this API boundary
    (`CategoryScoreOut` exposed only the category-level score/weight)."""

    name: str
    raw_value: float | None
    percentile: float | None
    weight_used: float


class CategoryScoreOut(CamelModel):
    name: str
    score: float | None  # None = no metric in this category was computable
    weight: float
    metrics: list[MetricPercentileOut] = []


class RiskOut(CamelModel):
    score: float | None  # None = no risk component was computable; band is "No data"
    band: str
    altman_z: float | None
    altman_zone: str
    piotroski_f: int | None
    net_debt_ebitda: float | None
    interest_coverage: float | None
    cash_runway_months: float | None
    burn_multiple: float | None
    dilution_yoy_pct: float | None
    components_used: list[str]


class ScoreDeltaOut(CamelModel):
    """Change in composite score since the **previous run that scored this
    company** (plan §8 Faz 7a, ADR 0010's Consequences on ADR 0009).

    This is a UI/API affordance only. ADR 0009's decision stands: the report
    narrative carries no movement language, nothing in `pipeline/synthesis/**`
    reads this, and the verifier never sees it.

    **The contract is `delta is None` XOR `unavailable_reason is None`.**
    There is deliberately no third state and no zero fallback: a delta of
    `0.0` means *measured, and unchanged*, which is a different claim from
    *not comparable*. Collapsing the two would put a confident number on a
    pair the data cannot support, which is the one failure this field exists
    to avoid.

    `unavailable_reason` is one of `techinves.api.repositories`'
    `DELTA_*` constants; see `_comparability_reason` there for what each one
    means and why it disqualifies the pair.
    """

    delta: float | None
    previous_composite: float | None
    previous_run_id: str | None
    current_run_id: str
    unavailable_reason: str | None


class CompanyListItem(CamelModel):
    ticker: str
    company_name: str
    cohort: str
    composite_score: float
    band: str
    sector_percentile: float
    coverage_pct: float
    low_reliability: bool
    categories: list[CategoryScoreOut]
    # Always present. When there is nothing comparable to compare against,
    # it carries a reason rather than being absent or zero (see ScoreDeltaOut).
    delta: ScoreDeltaOut


class CompanyDetail(CompanyListItem):
    regime: str
    cohort_size: int
    extended_cohort: bool
    distress_ceiling_applied: bool
    warnings: list[str]
    generated_at: datetime
    risk: RiskOut
    history: list["ScoreHistoryPointOut"] | None = None


class ScoreHistoryPointOut(CamelModel):
    """One point on a company's score history.

    `period` is the display label (the point's generation date), `run_id` the
    identity of the run that produced it -- ADR 0010 §2 retired `week_of`, and
    plan §7a's run-to-run delta reads `run_id`."""

    period: str
    run_id: str
    composite_score: float


CompanyDetail.model_rebuild()


class CompanyListResponse(CamelModel):
    items: list[CompanyListItem]
    page: int
    page_size: int
    total: int
    # The run whose scores this page reflects (was `week_of`).
    run_id: str | None


class CohortMeta(CamelModel):
    code: str
    label: str


class MetaResponse(CamelModel):
    cohorts: list[CohortMeta]
    bands: list[str]
    latest_run_id: str | None
    last_ingested_at: datetime | None
    #: "demo" | "live" (Faz 6, ADR 0010 §7) -- "demo" means at least one
    #: trigger is missing a required API key. Exposed here so the front-end
    #: can disable-with-reason up front, on load, rather than only after a
    #: click's refusal.
    mode: str
    #: trigger_type -> the first missing required key, for every trigger
    #: with a gap. Empty in live mode. Deliberately snake_case values under
    #: a camelCase field name -- these mirror `POST /v1/runs`'s own
    #: `missing_key` refusal field, which is a plain dict and stays
    #: snake_case for the same reason (routers/runs.py's module docstring).
    missing_keys: dict[str, str]


class HealthResponse(CamelModel):
    status: str  # "ok" | "degraded"
    version: str
    last_ingested_at: datetime | None
    last_ingestion_status: str | None


class ReportSectionOut(CamelModel):
    section_type: str  # company|macro
    ticker: str | None
    topic: str | None
    title: str
    body_markdown: str
    order_index: int


class VerifierViolationOut(CamelModel):
    """One classified verifier finding, as persisted on
    `reports.verifier_violations`. Mirrors `pipeline.schemas.VerifierViolation`
    field for field.

    Every field is typed permissively (`str`, not a `Literal`) on purpose:
    this is read back out of a JSON column that older rows may have written
    under a different vocabulary, and a validation error here would 500 the
    report detail endpoint -- i.e. hide the report *and* its violations, the
    one outcome ADR 0010 §6 rules out. An unrecognised severity is rendered
    at the loudest treatment by the front-end rather than dropped.
    """

    severity: str  # compliance_hard | structural_hard | soft
    category: str
    message: str
    section: str | None = None


class ReportSummaryOut(CamelModel):
    slug: str
    run_id: str
    created_at: datetime
    title: str
    excerpt: str
    highlighted_tickers: list[str]
    # ADR 0010 §6 / Faz 5.3. Carried on the *summary* (not just the detail)
    # so the archive list and the landing preview can mark an unsound report
    # as unsound at the point a reader chooses to open it -- a warning that
    # only exists after you have started reading is a warning that arrives
    # late. `None` means no verdict was recorded, which is an unknown state
    # rather than a clean one; the front-end keeps them apart.
    verifier_verdict: str | None = None
    is_partial: bool = False


class ReportDetailOut(ReportSummaryOut):
    sections: list[ReportSectionOut]
    # The violations themselves live on the detail response only: this is
    # what the banner *names*, and it is only ever rendered on the page that
    # shows the report body. `None` = no verifier report was stored for this
    # row (pre-Faz-5.3, or a run that died before the verifier); `[]` = the
    # verifier ran and found nothing. Not the same thing, so not collapsed.
    verifier_violations: list[VerifierViolationOut] | None = None


class ReportListResponse(CamelModel):
    items: list[ReportSummaryOut]
    page: int
    page_size: int
    total: int


class RunTriggerIn(CamelModel):
    """Body of `POST /v1/runs` (ADR 0010 §1, plan §5.1). `ticker` is only
    meaningful for the `company` trigger; the endpoint rejects it elsewhere."""

    trigger_type: str
    ticker: str | None = None


class RunTriggerOut(CamelModel):
    """The immediate response to a successful trigger -- the run id, nothing
    else. The caller polls `GET /v1/runs/{id}` for status and log output."""

    run_id: str
    trigger_type: str
    ticker: str | None
    status: str


class RunSummaryOut(CamelModel):
    """One row of `GET /v1/runs` history. Field set mirrors `RunRow`'s
    measurement columns (`src/techinves/db/models.py`) minus `log`, which is
    detail-view-only (Faz 4's log-tail protocol)."""

    run_id: str
    trigger_type: str
    ticker: str | None
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    verdict: str | None
    duration_seconds: float
    findings_count: int
    failure_count: int
    total_tokens: int
    total_cost_usd: float


class RunDetailOut(RunSummaryOut):
    """`GET /v1/runs/{id}`. `log` is the tail from the requested
    `log_offset` onward (default 0, i.e. the whole log); `log_offset` in the
    response is the offset to pass on the *next* poll -- monotonic, since
    `runs.log` is append-only and never rewritten (see
    `techinves.runs.service.RunService.append_log`)."""

    log: str
    log_offset: int


class RunListResponse(CamelModel):
    items: list[RunSummaryOut]
    page: int
    page_size: int
    total: int
