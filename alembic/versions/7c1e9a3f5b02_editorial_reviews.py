"""Add editorial_reviews table

R29 (reports/plans/2026-08-14_reporting-system-overhaul.md, Phase 4): the
publish gate's human-review step was a bare `y/N` stdout prompt in
techinves-approve-report -- it recorded nothing about what was reviewed, by
whom, or against what checklist. `editorial_reviews` is the persisted audit
trail for a sampling review of N deep-dive sections against a standing
compliance checklist (techinves.api.approve_report.EDITORIAL_CHECKLIST): verdict,
reviewer, timestamp, which sections were sampled, and the per-item result.

Revision ID: 7c1e9a3f5b02
Revises: d4a8f2c1b930
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e9a3f5b02"
down_revision: Union[str, None] = "d4a8f2c1b930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "editorial_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("sampled_sections", sa.JSON(), nullable=False),
        sa.Column("checklist_results", sa.JSON(), nullable=False),
    )
    op.create_index("ix_editorial_reviews_report_id", "editorial_reviews", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_editorial_reviews_report_id", table_name="editorial_reviews")
    op.drop_table("editorial_reviews")
