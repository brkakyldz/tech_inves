"""Allow category_scores.score and risk_metrics.score to be NULL

"No data" is now a first-class state in the scoring module: a category with no
computable metric, or a risk sub-score with no computable component, carries
None rather than a fabricated 0.0 (ADR 0001 clause 6). 0.0 remains a real
score meaning "worst in cohort", so the two must be storable separately.

score_history.composite_score stays NOT NULL: a score block with no usable
metric at all is never ingested (see api/ingest.py).

Revision ID: b7e41c9d2f08
Revises: 1acd36659b4a
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e41c9d2f08"
down_revision: Union[str, None] = "1acd36659b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLite has no ALTER COLUMN, so a plain op.alter_column() here aborts the
# whole migration chain with `near "ALTER": syntax error` -- meaning nothing
# past this revision could ever be applied to a local SQLite database, even
# though production (Postgres) applies it fine. batch_alter_table() emits
# native ALTER on dialects that support it and falls back to SQLite's
# copy-and-rename recreate strategy otherwise, so this is a no-op change for
# Postgres and the difference between "works" and "cannot run at all" locally.
def upgrade() -> None:
    for table in ("category_scores", "risk_metrics"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("score", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    # NULL scores have no faithful non-null representation; 0.0 is exactly the
    # conflation this migration exists to remove. Rows are dropped instead so
    # the downgrade cannot silently manufacture "very weak" companies.
    op.execute("DELETE FROM category_scores WHERE score IS NULL")
    op.execute("DELETE FROM risk_metrics WHERE score IS NULL")
    for table in ("category_scores", "risk_metrics"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("score", existing_type=sa.Float(), nullable=False)
