"""SQLAlchemy ORM schema for the API and the ingestion job.

Scope: companies/cohorts/scores (`reports/research/BACKEND_IMPLEMENTATION_PLAN.md`
Section 4) plus reports/covered_events
(`reports/research/REPORTS_AND_PIPELINE_INTEGRATION_PLAN.md` Section 3, Faz 3).
The subscriber/editorial-review/newsletter tables that used to live here were
deleted with the delivery layer (ADR 0010 §5).

**Run identity, not week identity** (ADR 0010 §2). `week_of` is gone from
every table here. A unit of work is a row in `runs`, identified by its
`run_id`, and `score_history`, `reports` and `covered_events` are keyed onto
that id. The ISO week was load-bearing only while the product was a weekly;
two runs on the same afternoon are normal for a tool with a button, and under
week keying they collided.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CohortRow(Base):
    __tablename__ = "cohorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(1), unique=True, nullable=False)  # A/B/C
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    weight_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    methodology_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")

    companies: Mapped[list["CompanyRow"]] = relationship(back_populates="cohort")


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cohort_id: Mapped[int] = mapped_column(ForeignKey("cohorts.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("score_history.id", use_alter=True, name="fk_companies_current_score"),
        nullable=True,
    )

    cohort: Mapped[CohortRow] = relationship(back_populates="companies")
    score_history: Mapped[list["ScoreHistoryRow"]] = relationship(
        back_populates="company",
        foreign_keys="ScoreHistoryRow.company_id",
        # Newest first. Insertion order is run order (one score row per
        # company per run), which is what `week_of.desc()` used to approximate.
        order_by="ScoreHistoryRow.id.desc()",
    )
    current_score: Mapped["ScoreHistoryRow | None"] = relationship(
        foreign_keys=[current_score_id], post_update=True, viewonly=True
    )


class ScoreHistoryRow(Base):
    """One score snapshot per company per run (ADR 0010 §2, amending ADR 0009).

    Was one row per company per ISO week, unique on `(company_id, week_of)`.
    `run_id` was already here as a plain string; it is now the real key --
    a foreign key onto `runs.run_id`, unique with `company_id`. The
    run-to-run delta of plan §7a reads this pairing directly.
    """

    __tablename__ = "score_history"
    __table_args__ = (UniqueConstraint("company_id", "run_id", name="uq_score_history_company_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"), nullable=False, index=True
    )

    composite_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    composite_band: Mapped[str] = mapped_column(String(20), nullable=False)
    sector_percentile: Mapped[float] = mapped_column(Float, nullable=False)
    sector_percentile_band: Mapped[str] = mapped_column(String(30), nullable=False)
    coverage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    low_reliability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    regime: Mapped[str] = mapped_column(String(30), nullable=False, default="profitable")
    cohort_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extended_cohort: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    distress_ceiling_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    company: Mapped[CompanyRow] = relationship(back_populates="score_history", foreign_keys=[company_id])
    categories: Mapped[list["CategoryScoreRow"]] = relationship(
        back_populates="score_history", cascade="all, delete-orphan"
    )
    risk: Mapped["RiskMetricsRow | None"] = relationship(
        back_populates="score_history", cascade="all, delete-orphan", uselist=False
    )


class CategoryScoreRow(Base):
    __tablename__ = "category_scores"
    __table_args__ = (
        UniqueConstraint("score_history_id", "category_name", name="uq_category_scores_history_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_history_id: Mapped[int] = mapped_column(
        ForeignKey("score_history.id"), nullable=False, index=True
    )
    category_name: Mapped[str] = mapped_column(String(40), nullable=False)
    # Nullable: no metric in this category was computable. Distinct from 0.0,
    # which means "ranked last in the cohort on every metric".
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    metrics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    score_history: Mapped[ScoreHistoryRow] = relationship(back_populates="categories")


class RiskMetricsRow(Base):
    __tablename__ = "risk_metrics"

    score_history_id: Mapped[int] = mapped_column(
        ForeignKey("score_history.id"), primary_key=True
    )
    # Nullable: no risk component was computable. Distinct from 0.0, which
    # means "measured, and maximally risky". Band carries "No data".
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    band: Mapped[str] = mapped_column(String(30), nullable=False)
    altman_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    altman_zone: Mapped[str] = mapped_column(String(20), nullable=False, default="Unavailable")
    piotroski_f: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_debt_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    interest_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_runway_months: Mapped[float | None] = mapped_column(Float, nullable=True)
    burn_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    dilution_yoy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    components_used: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    score_history: Mapped[ScoreHistoryRow] = relationship(back_populates="risk")


class FinancialFactRow(Base):
    """Written by ingestion, not exposed by any v1 endpoint (BACKEND_IMPLEMENTATION_PLAN.md §4)."""

    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_date", "period", name="uq_financial_facts_company_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    fiscal_date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(String(4), nullable=False)  # FY/Q1-Q4
    raw_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


# ADR 0010 §1: the three user-initiated actions. `scores` is the cheap
# deterministic watchlist refresh (what `ingestion_runs` used to record),
# `report` the full research/synthesis/verifier chain, `company` the same
# chain narrowed to one ticker.
TRIGGER_TYPES = ("scores", "report", "company")
RUN_STATUSES = ("queued", "running", "succeeded", "failed")


class RunRow(Base):
    """One row per unit of work, whichever trigger started it (ADR 0010 §2).

    Replaces both `pipeline_runs` and `ingestion_runs`: a score refresh and a
    report generation are two trigger types of the same thing, and Faz 4's
    `GET /v1/runs` should not have to union two tables to answer "what has
    this tool done".

    **`created_at` vs `started_at`.** They mean different things and are set
    at different moments:

    * `created_at` -- when the row was *created*, i.e. when the trigger was
      accepted. Always set, never null. A `queued` run has a `created_at`
      and nothing else. This is the column to order runs by: it is the only
      timestamp every row is guaranteed to have.
    * `started_at` -- when execution actually *began*. Null while `queued`,
      set exactly once when the status moves to `running`. The gap between
      the two is queue latency.
    * `finished_at` -- set with the terminal status (`succeeded`/`failed`).

    `duration_seconds` measures the work itself and is reported by the
    pipeline's own instrumentation; it is not `finished_at - started_at`,
    which also counts persistence.

    Every measurement column below (`duration_seconds`, the branch counts,
    findings/failures, verdict, tokens, cost, `branch_yields`) is retained
    from `pipeline_runs`: ADR 0004 §8's "is yield genuinely source-limited?"
    questions and the plan's before/after source-expansion comparison read
    them, and this is still the only place a run's per-branch yield survives
    past the log line. They all carry defaults, because a `scores` run never
    touches the LLM and legitimately leaves every one of them empty.
    """

    __tablename__ = "runs"
    # Declared explicitly (rather than `unique=True` on the column) so the
    # constraint has the same name in the model as in the migration. The
    # old `pipeline_runs` table carried a named `uq_pipeline_runs_run_id`
    # that the model never declared -- pre-existing Alembic drift, closed
    # here.
    #
    # `uq_runs_active_trigger` is the in-flight lock of ADR 0010 §4 (Faz 3.3):
    # a *unique partial* index over `trigger_type`, restricted to the two
    # non-terminal statuses. At most one `queued`-or-`running` row may exist
    # per trigger type; a second insert raises IntegrityError, which
    # `techinves.runs.service` converts into a refusal naming the holder.
    # Terminal rows (`succeeded`/`failed`) fall outside the predicate, so
    # history accumulates freely.
    #
    # It lives in the database rather than in process memory on purpose: a
    # `--reload` restart, or any other process replacement, must not hand out
    # a second lock while a run is still recorded as in flight. Both targets
    # this project supports (SQLite and PostgreSQL) implement partial
    # indexes, so the same DDL expresses the same guarantee on both -- hence
    # the two dialect-specific `_where` kwargs below, which are the identical
    # predicate spelled for each dialect.
    #
    # The price of a DB-held lock is that a crashed process leaves the lock
    # held by a row nobody is executing. That is what
    # `techinves.runs.reconcile` exists to clear, and it must run before this
    # index is ever consulted -- see that module.
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_runs_run_id"),
        Index(
            "uq_runs_active_trigger",
            "trigger_type",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The identity. URLs, foreign keys and the API all expose this and
    # nothing else (ADR 0010 §2).
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="report")
    # Set only by the single-company trigger; null for `scores`/`report`.
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Append-only progress output, streamed to the UI while the run is in
    # flight (ADR 0010 §3). Text rather than String(n): a full pipeline run's
    # log has no useful upper bound.
    log: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # --- measurement columns, all optional-by-default (see docstring) ---
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    company_branches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    macro_branches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # How many companies a `scores` run actually wrote (was
    # `ingestion_runs.company_count`). Deliberately not folded into
    # `company_branches`, which counts research fan-out and is 0 for a
    # scores run.
    company_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verdict_reason: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # list[dict] shaped like pipeline.schemas.BranchYield -- kept as JSON
    # rather than a child table since it's write-once, read-as-a-blob (the
    # per-branch breakdown is diagnostic, not queried column-by-column).
    branch_yields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ReportRow(Base):
    """A report produced by one run. There is no publication state:
    ADR 0010 §5 deleted the human publish gate along with the delivery layer,
    so every stored report is visible through `/v1/reports*` as soon as it is
    written. Ordering is by `created_at` descending (id as tie-break) -- the
    replacement for the old published-first ordering.

    Keyed on the run, not the week. `slug` is derived from `run_id` as
    `run-<run_id>` (plan §9 Q4) and stays the routing key for
    `front-end/app/reports/[slug]`.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(String(4000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Both set by pipeline/storage/report_store.py when the report is saved.
    # They used to feed the publish gate; with the gate gone they are the
    # inputs to the reader-facing warning banner of ADR 0010 §6 -- a `block`
    # verdict is rendered *with* its violations rather than withheld, so
    # `verifier_verdict` is load-bearing UI state, not dead schema.
    verifier_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # R2: the LLM verifier layer's per-section confidence scores + rationale
    # (pipeline.schemas.VerifierSectionScore, as a list of dicts), previously
    # produced every run and only ever logged -- never persisted, so it was
    # unusable as a quality signal after the fact.
    section_scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Faz 5.3: the verifier's *classified* findings
    # (`pipeline.schemas.VerifierViolation`, as a list of dicts with
    # `severity`/`category`/`message`/`section`). Until now these were
    # produced by `pipeline.verifier.rules.classify_violations` on every run
    # and died with the process -- only the one-word `verifier_verdict`
    # survived. ADR 0010 §6 requires a blocked draft to be rendered "with a
    # banner naming the violations", and a verdict alone cannot name
    # anything, so this column is what makes that banner possible.
    #
    # Nullable, and null is *not* "no violations": it means this row predates
    # the column (or was written by a path that never had a verifier report).
    # The banner treats that as an unknown state, not a clean one --
    # see `front-end/lib/verifier/banner.ts`.
    verifier_violations: Mapped[list | None] = mapped_column(JSON, nullable=True)

    sections: Mapped[list["ReportSectionRow"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportSectionRow.order_index"
    )
    highlights: Mapped[list["ReportHighlightRow"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportHighlightRow.rank"
    )


class ReportSectionRow(Base):
    __tablename__ = "report_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), nullable=False, index=True)
    section_type: Mapped[str] = mapped_column(String(20), nullable=False)  # company|macro
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body_markdown: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    report: Mapped[ReportRow] = relationship(back_populates="sections")


class ReportHighlightRow(Base):
    __tablename__ = "report_highlights"
    __table_args__ = (UniqueConstraint("report_id", "ticker", name="uq_report_highlights_report_ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("companies.ticker"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    report: Mapped[ReportRow] = relationship(back_populates="highlights")


class CoveredEventRow(Base):
    """Event de-duplication state, keyed on runs (ADR 0010 §9).

    Replaces the JSON-file-backed `pipeline/storage/covered_events_store.py`
    (`data/covered_events.json`) for production use; that store is kept for
    tests and local runs and carries exactly the same fields.

    `first_covered_week`/`last_updated_week` became
    `first_covered_run`/`last_updated_run`, and the 26-week retention window
    became a trailing window of the last N runs
    (`pipeline.config.COVERED_EVENTS_TRAILING_RUNS`). `run_seq` is the
    monotonic ordinal of the run that last touched the event -- it is what
    makes "the last N runs" answerable without joining `runs`, so the
    file-backed store enforces the identical window with the identical code.

    `event_key` is the stable per-event identity: derived once, at creation,
    from the event's bucket and its first-seen title, so a row can be
    updated in place when a matched event's headline evolves. Without it a
    save would have to delete the whole table and re-insert, which cannot
    express a window.
    """

    __tablename__ = "covered_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_covered_events_event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # company|macro
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_title: Mapped[str] = mapped_column(String(300), nullable=False)
    first_covered_run: Mapped[str] = mapped_column(String(64), nullable=False)
    last_updated_run: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    source_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
