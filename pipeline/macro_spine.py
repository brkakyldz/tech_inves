"""Macro quantitative spine (R28): a static 3-series set of real numbers
for the "Sector & Macro" section, sourced from FRED
(`pipeline/data/fred_client.py`) -- closes "a sector report with zero
macro numbers." The existing `pipeline.config.MACRO_TOPICS` (qualitative
research topics fed to Tavily) are the "adaptive topics" half of R28's
"static 3-topic spine + <=2 adaptive topics/week": that list already caps
at 4 hand-maintained topics and is unchanged by this module.

Each spine item is a *number*, not a narrative -- rendered deterministically
(`pipeline/synthesis/render.py`'s `render_macro_spine`), the same
never-let-the-LLM-write-a-number principle the score block already follows.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.data.fred_client import FredClient

# The static 3-series spine. Series originate from the Federal Reserve
# (FEDFUNDS, DGS10) and Census/BEA via FRED's aggregation (IPG3344S) --
# see this module's docstring and pipeline/data/fred_client.py's for why
# "FRED/Census" is one integration, not two.
MACRO_SPINE_SERIES: list[tuple[str, str, str]] = [
    ("FEDFUNDS", "Effective Federal Funds Rate", "%"),
    ("DGS10", "10-Year Treasury Yield", "%"),
    ("IPG3344S", "Semiconductor & Electronic Component Production Index", "index"),
]


@dataclass
class MacroSpineItem:
    series_id: str
    label: str
    units: str
    value: float | None
    as_of: str | None


def build_macro_spine(client: FredClient) -> list[MacroSpineItem]:
    """Fetches the latest observation for each static series. A single
    series failing (client returns None, or raises) degrades to a `None`
    value for that item rather than dropping the whole spine or failing
    the run -- this is supplementary macro context, not load-bearing data."""
    items: list[MacroSpineItem] = []
    for series_id, label, units in MACRO_SPINE_SERIES:
        try:
            point = client.latest_observation(series_id)
        except Exception:  # noqa: BLE001 - deliberate: one series failing isn't fatal
            point = None
        items.append(
            MacroSpineItem(
                series_id=series_id,
                label=label,
                units=units,
                value=point["value"] if point else None,
                as_of=point["date"] if point else None,
            )
        )
    return items
