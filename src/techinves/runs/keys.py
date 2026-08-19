"""Single source of truth for which environment variable each trigger type
requires (ADR 0010 §7-§8), and the demo/live classification built on top of
it (Faz 6, `reports/plans/2026-08-18_on-demand-transformation.md` §7).

Faz 4 built the per-trigger check as a private function inside
`api/routers/runs.py` (`_missing_required_key`) because at the time it was
the only caller. Faz 6 needs the same check from three more places --
`GET /v1/meta` (so the front-end can know up front, not just react to a
click's refusal), the startup seed-on-empty decision in `api/main.py`, and
the `pipeline.run` CLI -- so it moves here, to a module with no FastAPI or
DB import, importable from the CLI without pulling in the API layer.
`api/routers/runs.py` re-exports the same names for backward compatibility.

Presence only: every function here reads `os.environ` membership and
nothing else -- never a key's value, never logged, never returned beyond
its name. See `reports/backlog/credential-exposure-during-faz4.md` for why
that boundary is treated as absolute in this codebase.
"""

from __future__ import annotations

import os

#: ADR 0010 §8: the required key set is exactly `FMP_API_KEY`,
#: `OPENAI_API_KEY`, `TAVILY_API_KEY`. `FRED_API_KEY`/`EXA_API_KEY` are
#: deliberately absent from every value here -- they stay optional and must
#: never block a run; they degrade to an empty section instead.
REQUIRED_KEYS_BY_TRIGGER: dict[str, tuple[str, ...]] = {
    "scores": ("FMP_API_KEY",),
    "report": ("FMP_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY"),
    "company": ("FMP_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY"),
}


def missing_required_key(trigger_type: str) -> str | None:
    """The first required-but-absent env var for `trigger_type`, or `None`
    if everything it needs is present."""
    for name in REQUIRED_KEYS_BY_TRIGGER.get(trigger_type, ()):
        if not os.environ.get(name):
            return name
    return None


def missing_keys_by_trigger() -> dict[str, str]:
    """`trigger_type -> first missing required key`, for every trigger that
    has a gap. Empty means every trigger has what it needs (live mode)."""
    result: dict[str, str] = {}
    for trigger_type in REQUIRED_KEYS_BY_TRIGGER:
        missing = missing_required_key(trigger_type)
        if missing is not None:
            result[trigger_type] = missing
    return result


def app_mode() -> str:
    """"demo" if any trigger is missing a required key, else "live"
    (ADR 0010 §7). Key absence is a normal state to classify, not an error
    -- this never raises."""
    return "demo" if missing_keys_by_trigger() else "live"
