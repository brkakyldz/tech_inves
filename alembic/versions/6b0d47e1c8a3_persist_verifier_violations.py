"""Persist the verifier's classified violations on `reports`

Faz 5.3 of `reports/plans/2026-08-18_on-demand-transformation.md`, under
ADR 0010 §6 ("a blocked draft is shown with its violations, never silently
suppressed") and that ADR's Consequences section, which calls the resulting
banner "a correctness-relevant UI element, not decoration".

**The gap this closes.** `pipeline.verifier.rules.classify_violations` has
produced a severity-classified, best-effort section-scoped violation list on
every run since R19. Nothing ever wrote it down: `reports` stored the
one-word `verifier_verdict` and the LLM layer's `section_scores`, and the
classified list died with the process. A banner reading only the verdict can
say "this report was blocked" but cannot say *what* was violated -- which is
the entire content of ADR 0010 §6's requirement that the violations be
*named*.

**Shape.** A JSON column holding a list of
`pipeline.schemas.VerifierViolation` dumps:

    [{"severity": "compliance_hard",
      "category": "citation",
      "message": "fabricated citation (URL never retrieved): https://...",
      "section": "NVDA"}, ...]

Deliberately the same treatment `section_scores` already established in
`d4a8f2c1b930` -- a JSON column mirroring a pydantic model, rather than a
normalized `report_violations` table. The list is read whole or not at all
(the banner renders every violation on the report it belongs to), is never
queried by severity across reports, and is bounded by the verifier's own
check count. A child table would buy a query nobody issues and cost a join
on the hot report-detail read.

**Nullability, and why null is not "clean".** The column is nullable, and
null means *unknown*: a row written before this revision, or by a path that
had no verifier report. It does **not** mean "no violations" -- that state is
the empty list. The distinction is load-bearing downstream: rendering an
unknown verdict as a clean one is precisely the failure ADR 0010 §6 exists to
prevent, so `front-end/lib/verifier/banner.ts` gives null its own banner
rather than folding it into `pass`.

Adding a nullable column needs no table rebuild on SQLite, so this runs as a
plain `add_column` rather than through `batch_alter_table` the way Faz 2's
constraint changes had to.

Revision ID: 6b0d47e1c8a3
Revises: 3f5c81ad2e94
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6b0d47e1c8a3"
down_revision: Union[str, None] = "3f5c81ad2e94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("verifier_violations", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Reversible, with data loss that is real but bounded: the classified
    violations are dropped, and the reports they described fall back to
    carrying only their verdict. Nothing else reads the column, so no other
    schema object has to move with it."""
    op.drop_column("reports", "verifier_violations")
