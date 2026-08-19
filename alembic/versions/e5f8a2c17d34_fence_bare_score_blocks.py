"""Backfill: wrap bare COMPOSITE SCORE...DATA COVERAGE blocks in code fences

REPORT_SPEC.md §5.1/§9 mandates that the deterministic score block in every
company deep-dive section be wrapped in a ``` code fence. The fence-emitting
stitcher (`pipeline/synthesis/stitcher.py`'s `render_score_block()`) is not
wired into the live graph -- it still isn't, see
`reports/backlog/adr-0003-graph-topology-never-implemented.md`. The live path
builds the block from one LLM call, `prompts.py` asks the model to fence it,
and the model did not, so every `report_sections.body_markdown` row already
in the database has an unfenced score block. The companion pipeline fix
landing alongside this migration adds deterministic post-processing
(`fence_bare_score_blocks()` in `pipeline/synthesis/render.py`) so new runs
are fenced regardless of model behaviour; it does not wire up the stitcher.

`front-end/app/reports/[slug]/page.tsx` renders section markdown through
ReactMarkdown, which treats a bare block as an ordinary paragraph,
so `<p>` collapses its newlines and aligned columns into one unreadable
run-on line. This migration backfills the existing rows so already-published
reports render correctly without waiting for a re-publish.

Fencing algorithm intentionally duplicated, not imported
----------------------------------------------------------------------------
This inlines the same logic as `_fence_bare_score_blocks()` in
`src/techinves/email/render.py` rather than importing it. A migration is a
frozen historical artifact -- replayed from scratch on a fresh database it
must always produce the same result. Importing application code would let a
later refactor of that helper silently change what this migration does when
replayed, which defeats the point of keeping migrations frozen. The two
implementations must be kept in sync by hand if the algorithm ever changes,
but that's a smaller risk than the alternative.

Algorithm: detect a block by a line starting (after stripping leading
whitespace) with "COMPOSITE SCORE"; the block runs through the next line
starting with "DATA COVERAGE", inclusive. Regions already inside a ``` fence
are skipped, so an already-fenced block is left untouched (idempotent). A
"COMPOSITE SCORE" line with no following "DATA COVERAGE" line is left alone
rather than fenced to the end of the text, so as not to swallow prose that
follows it. A section can contain more than one block; all are handled.

Only rows whose content actually changes are UPDATEd.

Revision ID: e5f8a2c17d34
Revises: 9d3b6e1a4c77
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f8a2c17d34"
down_revision: Union[str, None] = "9d3b6e1a4c77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept identical to `_SCORE_BLOCK_START`/`_SCORE_BLOCK_END` in
# `src/techinves/email/render.py` -- see the module docstring above for why
# this is a hand-kept duplicate rather than an import.
_SCORE_BLOCK_START = "COMPOSITE SCORE"
_SCORE_BLOCK_END = "DATA COVERAGE"


def _fence_bare_score_blocks(body_markdown: str) -> str:
    """Wrap any not-already-fenced COMPOSITE SCORE...DATA COVERAGE block in
    a ``` code fence. See the module docstring for the algorithm; this is a
    frozen, migration-local copy of `_fence_bare_score_blocks()` in
    `src/techinves/email/render.py`."""
    lines = body_markdown.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if not in_fence and line.lstrip().startswith(_SCORE_BLOCK_START):
            end = i
            while end < len(lines) and not lines[end].lstrip().startswith(_SCORE_BLOCK_END):
                end += 1
            if end < len(lines):
                out.append("```")
                out.extend(lines[i : end + 1])
                out.append("```")
                i = end + 1
                continue
            # No DATA COVERAGE line: not the score block layout we know --
            # leave it exactly as-is rather than fencing to the end of the
            # section and swallowing whatever prose follows.
        out.append(line)
        i += 1
    return "\n".join(out)


def upgrade() -> None:
    connection = op.get_bind()
    report_sections = sa.table(
        "report_sections",
        sa.column("id", sa.Integer),
        sa.column("body_markdown", sa.String),
    )
    rows = connection.execute(
        sa.select(report_sections.c.id, report_sections.c.body_markdown)
    ).fetchall()
    for section_id, body_markdown in rows:
        if body_markdown is None:
            continue
        fenced = _fence_bare_score_blocks(body_markdown)
        if fenced != body_markdown:
            connection.execute(
                report_sections.update()
                .where(report_sections.c.id == section_id)
                .values(body_markdown=fenced)
            )


def downgrade() -> None:
    # No-op, deliberately. A faithful downgrade would need to strip fences
    # this migration added while leaving alone any fence that was already
    # legitimately present before it ran (e.g. a section some later pipeline
    # run had already fenced correctly, or a fence inside genuine prose
    # elsewhere in the same body). This migration does not record which
    # fences it added, so re-stripping indiscriminately would also strip
    # fences that predate it -- not a faithful inverse, and actively
    # destructive to correctly-formatted content. The safe, honest inverse
    # of "rendering is now readable" is "leave the readable version in
    # place"; downgrading this migration to restore the old broken
    # run-on-line rendering is not a goal worth writing code for.
    pass
