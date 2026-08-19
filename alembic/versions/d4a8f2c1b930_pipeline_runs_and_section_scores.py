"""Add pipeline_runs table and reports.section_scores

R1/R3 (reports/plans/2026-08-14_reporting-system-overhaul.md, Phase 0): a
run's per-branch yield, token spend, cost, duration and verdict reason were
logged and then discarded -- there was no way to answer "is yield genuinely
source-limited?" (ADR 0004 §8) without re-running with custom
instrumentation, and no trailing history to compare a run's findings_count
against (R3's yield floor).

R2: the LLM verifier's `section_scores` (confidence + rationale per section)
were computed every run and never persisted -- a signal produced and
discarded.

Revision ID: d4a8f2c1b930
Revises: c3f7a1b9e246
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8f2c1b930"
down_revision: Union[str, None] = "c3f7a1b9e246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("section_scores", sa.JSON(), nullable=True))

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("week_of", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("company_branches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("macro_branches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(20), nullable=True),
        sa.Column("verdict_reason", sa.String(2000), nullable=False, server_default=""),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("branch_yields", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_pipeline_runs_run_id"),
    )
    op.create_index("ix_pipeline_runs_run_id", "pipeline_runs", ["run_id"])
    op.create_index("ix_pipeline_runs_week_of", "pipeline_runs", ["week_of"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_week_of", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_run_id", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_column("reports", "section_scores")
