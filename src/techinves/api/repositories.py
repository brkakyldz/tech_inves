"""DB access for the API routers. Read-only: nothing here ever calls the
scoring engine or writes score data -- writes belong to `techinves.api.ingest`.
"""

from __future__ import annotations

import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from techinves.api._time import now_naive_utc
from techinves.db.models import (
    CategoryScoreRow,
    CohortRow,
    CompanyRow,
    ReportHighlightRow,
    ReportRow,
    ReportSectionRow,
    RiskMetricsRow,
    RunRow,
    ScoreHistoryRow,
)

from .schemas import (
    CATEGORY_DISPLAY_NAMES,
    COHORT_LABELS,
    CategoryScoreOut,
    CohortMeta,
    CompanyDetail,
    CompanyListItem,
    HealthResponse,
    MetaResponse,
    MetricPercentileOut,
    ReportDetailOut,
    ReportSectionOut,
    ReportSummaryOut,
    RiskOut,
    ScoreDeltaOut,
    ScoreHistoryPointOut,
    VerifierViolationOut,
)

logger = logging.getLogger(__name__)

SORT_COLUMNS = {
    "composite": ScoreHistoryRow.composite_score,
    "ticker": CompanyRow.ticker,
    "sectorPercentile": ScoreHistoryRow.sector_percentile,
}
CATEGORY_SORT_KEYS = {"valuation", "growth", "quality", "financial_health"}


def _metrics_out(metrics: list[dict]) -> list[MetricPercentileOut]:
    return [
        MetricPercentileOut(
            name=m.get("name", ""),
            raw_value=m.get("raw_value"),
            percentile=m.get("percentile"),
            weight_used=m.get("weight_used", 0.0),
        )
        for m in metrics or []
    ]


def _categories_out(rows: list[CategoryScoreRow]) -> list[CategoryScoreOut]:
    ordered = sorted(rows, key=lambda r: list(CATEGORY_DISPLAY_NAMES).index(r.category_name))
    return [
        CategoryScoreOut(
            name=CATEGORY_DISPLAY_NAMES.get(r.category_name, r.category_name),
            score=r.score,
            weight=r.weight,
            metrics=_metrics_out(r.metrics),
        )
        for r in ordered
    ]


# --------------------------------------------------------------------------
# Run-to-run delta (plan §8 Faz 7a)
#
# ## What "the previous run" means here
#
# Not "the run before this one in `runs`". `score_history` is written by
# exactly one path -- `techinves.api.ingest`, driven by the `scores` trigger
# -- so `report` and `company` runs never appear in the series at all and
# can never be somebody's previous run. Within the series the previous run
# is resolved **per company**: the score row for that company with the
# greatest `id` below the current row's.
#
# Per-company rather than global because runs interleave and because a run
# does not necessarily score every ticker. Ingest skips the `score_history`
# write for an `insufficient_data` block, so a run can legitimately produce
# 41 rows out of 43; comparing a ticker against a run that never scored it
# would either fabricate a pair or drop the delta for a ticker whose own
# history is perfectly continuous. Ordering is by row id -- insertion order
# is run order, the same ordering authority `_latest_score_run_id`,
# `get_score_history` and `CompanyRow.score_history` already use, and
# deliberately not the opaque `run_id` string, which is not required to sort.
#
# ## Comparability
#
# ADR 0009's data-boundary caution generalises past the FMP->EDGAR seam it
# was written about: two runs can be incomparable for reasons that have
# nothing to do with that migration. A composite score is a *cohort-relative
# percentile* (ADR 0005) computed under a *regime-selected metric set*
# (`scoring/engine.py`), so it is only meaningful against another score
# computed on the same basis. Where the basis moved, the difference measures
# the basis, not the company -- exactly ADR 0009's objection.
#
# So the delta is withheld, with a stated reason, rather than shown. Every
# reason below is read off data the rows and runs already carry; none of it
# is inferred.
DELTA_FIRST_RUN = "first_run"
"""Nothing precedes this score for this company. The normal state of a
first-ever run, and of a ticker added to the watchlist later -- not an error,
and emphatically not a delta of zero."""

DELTA_UNKNOWN_PROVENANCE = "unknown_provenance"
"""One of the two rows belongs to a run this database has no record of, or to
a run that is not a `scores` run. Its inputs cannot be established, so the
pair cannot be certified comparable."""

DELTA_INCOMPLETE_RUN = "incomplete_run"
"""One of the two runs did not reach `succeeded`. A run that failed partway
may have written some tickers and not others; the rows it did write are real,
but "this is the whole of that run" is not something they support."""

DELTA_COHORT_CHANGED = "cohort_changed"
"""The cohort the percentile was computed against changed size, or switched
to/from the extended cohort. The two numbers rank against different
populations."""

DELTA_REGIME_CHANGED = "regime_changed"
"""The company crossed between regimes, which substitutes the metric set the
composite is built from. Before and after are not the same measurement."""


def _comparability_reason(
    current: ScoreHistoryRow,
    previous: ScoreHistoryRow | None,
    runs: dict[str, RunRow],
) -> str | None:
    """The reason this pair cannot be differenced, or None when it can."""
    if previous is None:
        return DELTA_FIRST_RUN
    # Both rows are checked, not just the older one. A current run that died
    # partway leaves rows that are individually real but collectively partial,
    # and differencing against them is the same error in the other direction.
    for row in (previous, current):
        run = runs.get(row.run_id)
        if run is None or run.trigger_type != "scores":
            return DELTA_UNKNOWN_PROVENANCE
        if run.status != "succeeded":
            return DELTA_INCOMPLETE_RUN
    if (
        current.cohort_size != previous.cohort_size
        or current.extended_cohort != previous.extended_cohort
    ):
        return DELTA_COHORT_CHANGED
    if current.regime != previous.regime:
        return DELTA_REGIME_CHANGED
    return None


async def _score_deltas(
    session: AsyncSession, current_rows: list[ScoreHistoryRow]
) -> dict[int, ScoreDeltaOut]:
    """Deltas for a batch of current score rows, keyed by `score_history.id`.

    Two queries regardless of batch size: one for the predecessor rows, one
    for the runs behind both sides.
    """
    if not current_rows:
        return {}

    previous_id_subq = (
        select(func.max(ScoreHistoryRow.id))
        .where(
            or_(
                *[
                    and_(
                        ScoreHistoryRow.company_id == row.company_id,
                        ScoreHistoryRow.id < row.id,
                    )
                    for row in current_rows
                ]
            )
        )
        .group_by(ScoreHistoryRow.company_id)
        .scalar_subquery()
    )
    previous_rows = (
        (
            await session.execute(
                select(ScoreHistoryRow).where(ScoreHistoryRow.id.in_(previous_id_subq))
            )
        )
        .scalars()
        .all()
    )
    previous_by_company = {row.company_id: row for row in previous_rows}

    run_ids = {row.run_id for row in current_rows} | {row.run_id for row in previous_rows}
    runs = {
        run.run_id: run
        for run in (
            (await session.execute(select(RunRow).where(RunRow.run_id.in_(run_ids))))
            .scalars()
            .all()
        )
    }

    out: dict[int, ScoreDeltaOut] = {}
    for current in current_rows:
        previous = previous_by_company.get(current.company_id)
        reason = _comparability_reason(current, previous, runs)
        comparable = reason is None and previous is not None
        out[current.id] = ScoreDeltaOut(
            # `delta` and `unavailable_reason` are mutually exclusive by
            # construction here, which is the contract ScoreDeltaOut states.
            delta=(
                current.composite_score - previous.composite_score
                if comparable
                else None
            ),
            # Withheld along with the delta, not merely un-subtracted. Handing
            # a client the operand of a subtraction this layer refused to
            # perform is the same wrong number one indirection away.
            previous_composite=previous.composite_score if comparable else None,
            previous_run_id=previous.run_id if previous is not None else None,
            current_run_id=current.run_id,
            unavailable_reason=reason,
        )
    return out


def _list_item(
    company: CompanyRow, score: ScoreHistoryRow, delta: ScoreDeltaOut
) -> CompanyListItem:
    return CompanyListItem(
        ticker=company.ticker,
        company_name=company.name,
        cohort=company.cohort.code,
        composite_score=score.composite_score,
        band=score.composite_band,
        sector_percentile=score.sector_percentile,
        coverage_pct=score.coverage_pct,
        low_reliability=score.low_reliability,
        categories=_categories_out(score.categories),
        delta=delta,
    )


async def list_companies(
    session: AsyncSession,
    *,
    cohorts: list[str] | None = None,
    band: str | None = None,
    min_composite: float | None = None,
    max_composite: float | None = None,
    min_coverage: float | None = None,
    search: str | None = None,
    category_sort: str | None = None,
    sort: str = "composite",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
    run_id: str | None = None,
) -> tuple[list[CompanyListItem], int, str | None]:
    latest_run = run_id or await _latest_score_run_id(session)
    if latest_run is None:
        return [], 0, None

    stmt = (
        select(CompanyRow, ScoreHistoryRow)
        .join(ScoreHistoryRow, ScoreHistoryRow.company_id == CompanyRow.id)
        .join(CohortRow, CompanyRow.cohort_id == CohortRow.id)
        .where(ScoreHistoryRow.run_id == latest_run, CompanyRow.is_active.is_(True))
        .options(
            selectinload(CompanyRow.cohort),
            selectinload(ScoreHistoryRow.categories),
        )
    )

    if cohorts:
        stmt = stmt.where(CohortRow.code.in_(cohorts))
    if band:
        stmt = stmt.where(ScoreHistoryRow.composite_band == band)
    if min_composite is not None:
        stmt = stmt.where(ScoreHistoryRow.composite_score >= min_composite)
    if max_composite is not None:
        stmt = stmt.where(ScoreHistoryRow.composite_score <= max_composite)
    if min_coverage is not None:
        stmt = stmt.where(ScoreHistoryRow.coverage_pct >= min_coverage)
    if search:
        escaped = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = stmt.where(
            func.lower(CompanyRow.ticker).like(like, escape="\\")
            | func.lower(CompanyRow.name).like(like, escape="\\")
        )

    if sort in CATEGORY_SORT_KEYS:
        cat_alias = CategoryScoreRow
        stmt = stmt.join(
            cat_alias,
            (cat_alias.score_history_id == ScoreHistoryRow.id) & (cat_alias.category_name == sort),
        )
        sort_col = cat_alias.score
    else:
        sort_col = SORT_COLUMNS.get(sort, ScoreHistoryRow.composite_score)

    # Built from the same joined/filtered `stmt` (including the category
    # join above) so `total` matches what the paginated query actually
    # returns -- computing it before that join overcounted whenever a
    # category sort excluded companies with no row for that category.
    count_stmt = select(func.count()).select_from(stmt.with_only_columns(CompanyRow.id).subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # NULLS LAST, spelled out rather than left to the backend. A category
    # sort orders on `category_scores.score`, which is nullable and means "no
    # metric in this category was computable" (db/models.py) -- and the two
    # supported backends disagree about where a bare ORDER BY puts a NULL:
    # SQLite sorts NULL as smaller than everything (so NULLs land last on
    # DESC and first on ASC), PostgreSQL defaults to NULLS FIRST on DESC and
    # NULLS LAST on ASC. The same query over the same data would therefore
    # return a different first page in dev (sqlite, db/session.py) than in
    # production, and the Postgres reading is the useless one: page 1 of
    # "sort by quality" would be entirely companies with no quality score at
    # all. Unscored last in both directions is the ordering that means
    # something to a reader, and it is now the ordering on both backends.
    # Harmless on the non-nullable sort columns, so it is applied uniformly.
    ordered = sort_col.asc() if order == "asc" else sort_col.desc()
    stmt = stmt.order_by(ordered.nulls_last())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await session.execute(stmt)).all()
    deltas = await _score_deltas(session, [score for _, score in rows])
    items = [_list_item(company, score, deltas[score.id]) for company, score in rows]
    return items, total, latest_run


async def _latest_score_run_id(session: AsyncSession) -> str | None:
    """The run whose scores are "current" (was `_latest_week_of`).

    Ordered by `score_history.id` descending -- insertion order, one score row
    per company per run, so the highest id belongs to the most recent run that
    wrote scores. Deliberately not `max(run_id)`: run ids are opaque strings
    (ADR 0010 §2) and are not required to sort."""
    result = await session.execute(
        select(ScoreHistoryRow.run_id).order_by(ScoreHistoryRow.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_highlights(session: AsyncSession, limit: int = 5) -> list[CompanyListItem]:
    items, _, _ = await list_companies(session, sort="composite", order="desc", page=1, page_size=limit)
    return items


async def get_company_detail(
    session: AsyncSession, ticker: str, *, include_history: bool = False, history_limit: int = 52
) -> CompanyDetail | None:
    stmt = (
        select(CompanyRow)
        .where(func.upper(CompanyRow.ticker) == ticker.upper())
        .options(
            selectinload(CompanyRow.cohort),
            selectinload(CompanyRow.current_score).selectinload(ScoreHistoryRow.categories),
            selectinload(CompanyRow.current_score).selectinload(ScoreHistoryRow.risk),
        )
    )
    company = (await session.execute(stmt)).scalar_one_or_none()
    if company is None or company.current_score is None:
        return None

    score = company.current_score
    risk = score.risk
    risk_out = (
        RiskOut(
            score=risk.score,
            band=risk.band,
            altman_z=risk.altman_z,
            altman_zone=risk.altman_zone,
            piotroski_f=risk.piotroski_f,
            net_debt_ebitda=risk.net_debt_ebitda,
            interest_coverage=risk.interest_coverage,
            cash_runway_months=risk.cash_runway_months,
            burn_multiple=risk.burn_multiple,
            dilution_yoy_pct=risk.dilution_yoy_pct,
            components_used=risk.components_used,
        )
        if risk is not None
        else RiskOut(
            score=0,
            band="Adequate",
            altman_z=None,
            altman_zone="Unavailable",
            piotroski_f=None,
            net_debt_ebitda=None,
            interest_coverage=None,
            cash_runway_months=None,
            burn_multiple=None,
            dilution_yoy_pct=None,
            components_used=[],
        )
    )

    history_out = None
    if include_history:
        history_out = await get_score_history(session, ticker, limit=history_limit)

    # Keyed off `current_score`, the row the rest of this payload describes --
    # not off the globally-latest run, which a company with no row in that run
    # would not be part of anyway.
    delta = (await _score_deltas(session, [score]))[score.id]

    return CompanyDetail(
        ticker=company.ticker,
        company_name=company.name,
        cohort=company.cohort.code,
        composite_score=score.composite_score,
        band=score.composite_band,
        sector_percentile=score.sector_percentile,
        coverage_pct=score.coverage_pct,
        low_reliability=score.low_reliability,
        categories=_categories_out(score.categories),
        delta=delta,
        regime=score.regime,
        cohort_size=score.cohort_size,
        extended_cohort=score.extended_cohort,
        distress_ceiling_applied=score.distress_ceiling_applied,
        warnings=score.warnings,
        generated_at=score.generated_at,
        risk=risk_out,
        history=history_out,
    )


async def company_exists(session: AsyncSession, ticker: str) -> bool:
    stmt = select(CompanyRow.id).where(func.upper(CompanyRow.ticker) == ticker.upper())
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def get_score_history(
    session: AsyncSession,
    ticker: str,
    *,
    limit: int = 52,
) -> list[ScoreHistoryPointOut]:
    """Oldest-last history for one ticker, one point per run.

    The `from_`/`to_` week filters went with `week_of` -- they filtered on a
    calendar the history is no longer keyed by. Ordering is by row id
    (insertion = run order), and `period` is the score's own `generated_at`
    date, which is what the chart labels."""
    stmt = (
        select(
            ScoreHistoryRow.run_id,
            ScoreHistoryRow.generated_at,
            ScoreHistoryRow.composite_score,
        )
        .join(CompanyRow, ScoreHistoryRow.company_id == CompanyRow.id)
        .where(func.upper(CompanyRow.ticker) == ticker.upper())
        .order_by(ScoreHistoryRow.id.desc())
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    points = [
        ScoreHistoryPointOut(
            period=generated_at.date().isoformat(), run_id=run_id, composite_score=score
        )
        for run_id, generated_at, score in rows
    ]
    return list(reversed(points))


async def get_meta(session: AsyncSession) -> MetaResponse:
    from techinves.runs.keys import app_mode, missing_keys_by_trigger

    cohorts = (await session.execute(select(CohortRow).order_by(CohortRow.code))).scalars().all()
    latest_run = await _latest_score_run_id(session)
    last_run = await _last_successful_score_run(session)
    return MetaResponse(
        cohorts=[CohortMeta(code=c.code, label=c.label or COHORT_LABELS.get(c.code, c.code)) for c in cohorts],
        bands=["Strong", "Good", "Moderate", "Weak", "Very Weak"],
        latest_run_id=latest_run,
        last_ingested_at=last_run.finished_at if last_run else None,
        # Faz 6, ADR 0010 §7: the demo/live classification is a pure
        # os.environ presence check, not a DB query -- so it's read here
        # rather than passed in, the same way the endpoint layer reads it.
        mode=app_mode(),
        missing_keys=missing_keys_by_trigger(),
    )


async def _last_successful_score_run(session: AsyncSession) -> RunRow | None:
    """The most recent completed `scores` run (was the latest successful
    `ingestion_runs` row). Scoped to `trigger_type="scores"` because `runs`
    now also holds report runs, which write no score data -- a report run
    must never be able to make stale scores look fresh."""
    return (
        await session.execute(
            select(RunRow)
            .where(RunRow.trigger_type == "scores", RunRow.status == "succeeded")
            # `finished_at` is nullable, and the backends disagree on where a
            # bare DESC puts a NULL (see list_companies). A succeeded run with
            # no `finished_at` would sort *first* on PostgreSQL and be
            # reported as the last ingestion, with a null timestamp -- so the
            # clause is explicit here too.
            .order_by(RunRow.finished_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()


STALE_AFTER_DAYS = 8


async def get_health(session: AsyncSession, version: str) -> HealthResponse:
    last_run = await _last_successful_score_run(session)

    status = "ok"
    if last_run is None:
        status = "degraded"
    elif last_run.finished_at is not None:
        age_days = (now_naive_utc() - last_run.finished_at).days
        if age_days > STALE_AFTER_DAYS:
            status = "degraded"

    return HealthResponse(
        status=status,
        version=version,
        last_ingested_at=last_run.finished_at if last_run else None,
        last_ingestion_status=last_run.status if last_run else None,
    )


def _report_summary(report: ReportRow) -> ReportSummaryOut:
    return ReportSummaryOut(
        slug=report.slug,
        run_id=report.run_id,
        created_at=report.created_at,
        title=report.title,
        excerpt=report.summary,
        highlighted_tickers=[h.ticker for h in sorted(report.highlights, key=lambda h: h.rank)],
        verifier_verdict=report.verifier_verdict,
        is_partial=report.is_partial,
    )


def _violations_out(report: ReportRow) -> list[VerifierViolationOut] | None:
    """The stored violation list, or None when the column holds no list.

    Defensive by design (ADR 0010 §6): every row is data written by an older
    revision of the pipeline, and a malformed entry must degrade to a
    less-detailed banner, never to a 500 that hides the report and its
    verdict together. A non-list column value, or an entry that isn't a
    mapping, is dropped; a mapping missing `message` is kept with a
    placeholder, because *something is wrong here* is the part the reader
    must not lose.
    """
    raw = report.verifier_violations
    if raw is None:
        return None
    if not isinstance(raw, list):
        logger.warning(
            "reports.verifier_violations for slug=%s is %s, not a list; "
            "treating as unknown",
            report.slug,
            type(raw).__name__,
        )
        return None
    out: list[VerifierViolationOut] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("dropping non-object verifier violation on slug=%s", report.slug)
            continue
        out.append(
            VerifierViolationOut(
                severity=str(item.get("severity") or "unknown"),
                category=str(item.get("category") or "unknown"),
                message=str(item.get("message") or "(violation recorded without a message)"),
                section=(str(item["section"]) if item.get("section") is not None else None),
            )
        )
    return out


async def list_reports(
    session: AsyncSession, *, page: int = 1, page_size: int = 20
) -> tuple[list[ReportSummaryOut], int]:
    base = select(ReportRow)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    stmt = (
        base.options(selectinload(ReportRow.highlights))
        .order_by(ReportRow.created_at.desc(), ReportRow.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_report_summary(r) for r in rows], total


async def get_latest_report(session: AsyncSession) -> ReportSummaryOut | None:
    stmt = (
        select(ReportRow)
        .options(selectinload(ReportRow.highlights))
        # Newest by write time, not by the week it covers: with the publish
        # gate gone (ADR 0010 §5) "latest" means "most recently produced".
        # `id` breaks a same-timestamp tie deterministically -- two rows can
        # share a `created_at` when two runs land inside the same second.
        .order_by(ReportRow.created_at.desc(), ReportRow.id.desc())
        .limit(1)
    )
    report = (await session.execute(stmt)).scalars().first()
    return _report_summary(report) if report else None


async def get_report_by_slug(session: AsyncSession, slug: str) -> ReportDetailOut | None:
    stmt = (
        select(ReportRow)
        .where(ReportRow.slug == slug)
        .options(selectinload(ReportRow.highlights), selectinload(ReportRow.sections))
    )
    report = (await session.execute(stmt)).scalars().first()
    if report is None:
        return None
    summary = _report_summary(report)
    return ReportDetailOut(
        **summary.model_dump(),
        verifier_violations=_violations_out(report),
        sections=[
            ReportSectionOut(
                section_type=s.section_type,
                ticker=s.ticker,
                topic=s.topic,
                title=s.title,
                body_markdown=s.body_markdown,
                order_index=s.order_index,
            )
            for s in report.sections
        ],
    )
