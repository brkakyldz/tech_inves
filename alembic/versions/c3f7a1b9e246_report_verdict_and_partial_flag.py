"""Add reports.verifier_verdict and reports.is_partial

REPORT_SPEC.md §11's publish gate: techinves-approve-report must refuse to
publish a report whose verifier verdict wasn't "pass", or whose ticker set
was a `--tickers` subset of the full watchlist (a smoke test), unless
explicitly labelled partial. Neither fact was previously persisted anywhere
-- pipeline/storage/report_store.py never received the verdict or the run's
ticker set, so the publish gate had nothing to check beyond a human `y/N`
prompt. This is how a 5-ticker smoke test reached `status=published` (see
reports/synthesis/2026-08-14_report-quality-root-cause.md).

Revision ID: c3f7a1b9e246
Revises: b7e41c9d2f08
Create Date: 2026-08-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f7a1b9e246"
down_revision: Union[str, None] = "b7e41c9d2f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("verifier_verdict", sa.String(20), nullable=True))
    op.add_column(
        "reports",
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("reports", "is_partial")
    op.drop_column("reports", "verifier_verdict")
