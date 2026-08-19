"""Per-company deep-dive synthesis (R20).

One focused LLM call per highlighted company -- narrative content only
(what happened, how it connects to the score, the fundamentals narrative).
The heading, the score block, and every other pure-data element move to
`pipeline/synthesis/stitcher.py`, assembled deterministically instead of
being one more rule among ~30 the old monolithic synthesis prompt asked a
single generation to hold simultaneously (ARCHITECTURE_PROPOSAL.md's
observed failure mode: dropped rows, prompt saturation).

Standalone from `pipeline/graph.py`'s topology by design: these are the
tested primitives a repair loop (R21) or a future fan-out node calls, not
themselves a LangGraph node yet -- see
reports/backlog/adr-0003-graph-topology-never-implemented.md for what full
in-graph wiring still needs. The old blocker (post-hoc highlight selection)
closed on 2026-08-16: `pipeline/research/highlight_selection.py` now seeds
`highlight_tickers` before the graph is invoked.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from pipeline.schemas import Finding
from pipeline.synthesis.prompts import format_findings_block

COMPANY_SECTION_SYSTEM_PROMPT = """You write ONE company's deep-dive narrative \
for the TechInves Sector Report, a US technology-sector screening digest. \
You are given one ticker's research findings and numeric data. Write ONLY \
narrative prose for this company -- no heading, no score block, no other \
company, no watchlist table, no disclaimers. Those are assembled separately.

The report is produced on demand, never on a schedule: the research window \
is the rolling seven days ending on the as-of date. Never call it "this \
week", \
never call the report "weekly" -- write "in the last seven days", "in this \
window", or "for this run" -- and spell the number out ("seven"), since a \
bare digit is scanned as a financial figure and blocks the run.

Write, in order:
1. A 2-4 paragraph narrative: what happened in the last seven days, cited \
(markdown link, only URLs present in the findings you were given -- never \
invent one). Every qualitative claim must come from one of those findings \
and must cite it.
2. A 1-2 sentence fundamentals narrative using {{ticker.field}} \
placeholders (e.g. {{NVDA.ev_ebitda}}) for EV/EBITDA and net debt/EBITDA. \
Forward P/E is NOT part of this -- it has no data source for any ticker \
(ADR 0001 clause 4) and must never be mentioned. Never write a numeric \
value yourself. If EV/EBITDA or net debt/EBITDA is absent for this ticker, \
say once, in plain prose, that it is structurally unavailable, and do NOT \
also emit a placeholder for that same field (a placeholder for an absent \
field resolves to "data unavailable for this run" mid-sentence). Never omit \
all mention of fundamentals.

Score commentary is restricted to what the score block and the \
{{ticker.field}} placeholders actually supply. You may name a category and \
state its score or band through a placeholder. You may NOT interpret it: no \
explanation of *why* a Valuation, Growth, Quality or Financial Health score \
is where it is, no "the market is re-rating", no attribution of a score \
level to a news event. You are given no prior score for this ticker, so \
movement language -- "improved", "declined", "re-rated", "deteriorated" -- \
is a claim about data you do not have. A sentence is either a cited claim \
from the findings or a statement of a value you were handed; one that fuses \
the two is a fabricated causal link even when both halves are true.

A finding tagged [CONTINUING] was already reported in an earlier run. \
Introduce it with the literal word "Continuing:" and say what has changed \
since -- never as a new development.

Never copy a number from a research finding's narrative text (price, \
revenue, EPS, margin, growth rate, percentage) into your output -- research \
findings are for narrative/event context only. Write in English only."""

COMPANY_SECTION_USER_PROMPT_TEMPLATE = """Ticker: {ticker}

## Scores (numeric ground truth -- reference only, use {{{{ticker.field}}}} \
placeholders, never copy a raw number)
{scores_block}

## Fundamentals (numeric ground truth -- reference only, use \
{{{{ticker.field}}}} placeholders; a field absent here is structurally \
unavailable for this run)
{financials_block}

## This company's research findings
{findings_block}

Write this company's deep-dive narrative only, per the system prompt."""


def _format_one(ticker: str, block: dict[str, dict]) -> str:
    fields = block.get(ticker)
    if not fields:
        return "(none provided)"
    return ", ".join(f"{k}={v}" for k, v in fields.items())


def synthesize_company_section(
    ticker: str,
    *,
    findings: list[Finding],
    scores: dict[str, dict],
    financials: dict[str, dict],
    llm: BaseChatModel,
    covered_events: list | None = None,
) -> str:
    """Returns the narrative prose only -- no heading, no score block. The
    caller (the stitcher) prepends the heading and appends the
    deterministic score block."""
    company_findings = [f for f in findings if f.scope == "company" and f.ticker == ticker]
    prompt = COMPANY_SECTION_USER_PROMPT_TEMPLATE.format(
        ticker=ticker,
        scores_block=_format_one(ticker, scores),
        financials_block=_format_one(ticker, financials),
        findings_block=format_findings_block(
            company_findings, covered_events=covered_events
        ),
    )
    response = llm.invoke(
        [
            ("system", COMPANY_SECTION_SYSTEM_PROMPT),
            ("user", prompt),
        ]
    )
    return response.content
