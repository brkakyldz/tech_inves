"""Drop delivery-layer tables and reports.status/published_at

ADR 0010 §5 (`reports/decisions/0010-on-demand-personal-use-model.md`)
retired the newsletter delivery layer and the human publish gate as part of
the on-demand transformation (`reports/plans/2026-08-18_on-demand-transformation.md`,
Faz 1). `src/techinves/db/models.py` no longer declares `SubscriberRow`,
`EditorialReviewRow`, `NewsletterSendRow`, or `NewsletterDeliveryRow`, and
`ReportRow` no longer declares `status`/`published_at` -- every stored report
is now visible through `/v1/reports*` as soon as it is written, per that
model's updated docstring. This migration brings the schema into line with
those model changes; it is a pure teardown, no new columns or tables.

Drop order for the four tables follows their FK dependencies:
`newsletter_deliveries` references both `newsletter_sends` (via `send_id`)
and `subscribers` (via `subscriber_id`), so it must go first; `newsletter_sends`
and `editorial_reviews` reference only `reports`, which is not being dropped;
`subscribers` is referenced by nothing once `newsletter_deliveries` is gone.

`reports.status`/`reports.published_at` are dropped via
`op.batch_alter_table` because SQLite (this project's dev database) cannot
`ALTER TABLE ... DROP COLUMN` outside of batch mode; on Postgres this is
equivalent to a plain `drop_column`.

Revision ID: f1a9c3e7b482
Revises: e5f8a2c17d34
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a9c3e7b482"
down_revision: Union[str, None] = "e5f8a2c17d34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- newsletter_deliveries (FKs to newsletter_sends and subscribers) ---
    op.drop_index("ix_newsletter_deliveries_subscriber_id", table_name="newsletter_deliveries")
    op.drop_index("ix_newsletter_deliveries_report_id", table_name="newsletter_deliveries")
    op.drop_index("ix_newsletter_deliveries_send_id", table_name="newsletter_deliveries")
    op.drop_table("newsletter_deliveries")

    # --- newsletter_sends (FK to reports only, reports stays) ---
    op.drop_index("ix_newsletter_sends_report_id", table_name="newsletter_sends")
    op.drop_table("newsletter_sends")

    # --- editorial_reviews (FK to reports only, reports stays) ---
    op.drop_index("ix_editorial_reviews_report_id", table_name="editorial_reviews")
    op.drop_table("editorial_reviews")

    # --- subscribers (no remaining incoming FKs once newsletter_deliveries is gone) ---
    op.drop_index("ix_subscribers_email", table_name="subscribers")
    op.drop_table("subscribers")

    # --- reports: drop the publish-gate columns ---
    with op.batch_alter_table("reports") as batch_op:
        batch_op.drop_column("status")
        batch_op.drop_column("published_at")


def downgrade() -> None:
    # --- reports: re-add the publish-gate columns ---
    # There are no rows to backfill in a fresh downgrade-from-empty scenario,
    # but a report row created after this migration's upgrade has no
    # `status`/`published_at` to recover -- the honest inverse recreates the
    # columns with the same "safe to have existing rows" defaults the
    # original migration used (a report row that predates delivery is
    # treated as already published), not a fabricated history of what was
    # actually sent.
    with op.batch_alter_table("reports") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=20), nullable=False, server_default="published")
        )
        batch_op.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))

    # The server default exists only so the NOT NULL column can be added to a
    # table that already has rows; the pre-teardown schema (1acd36659b4a) had
    # none. Drop it again once the rows are filled, so a downgraded database
    # is DDL-identical to the one this migration replaced instead of carrying
    # a default the old ORM would flag as a schema diff.
    with op.batch_alter_table("reports") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            existing_nullable=False,
            server_default=None,
        )

    # --- subscribers ---
    op.create_table(
        "subscribers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("consent_ip", sa.String(length=45), nullable=True),
        sa.Column("consent_source", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_subscribers_token"),
    )
    op.create_index("ix_subscribers_email", "subscribers", ["email"], unique=True)

    # --- editorial_reviews ---
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

    # --- newsletter_sends ---
    op.create_table(
        "newsletter_sends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_newsletter_sends_report_id", "newsletter_sends", ["report_id"])

    # --- newsletter_deliveries (no UniqueConstraint on report_id+subscriber_id, see 9d3b6e1a4c77) ---
    op.create_table(
        "newsletter_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("send_id", sa.Integer(), sa.ForeignKey("newsletter_sends.id"), nullable=False),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("subscriber_id", sa.Integer(), sa.ForeignKey("subscribers.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_newsletter_deliveries_send_id", "newsletter_deliveries", ["send_id"])
    op.create_index("ix_newsletter_deliveries_report_id", "newsletter_deliveries", ["report_id"])
    op.create_index("ix_newsletter_deliveries_subscriber_id", "newsletter_deliveries", ["subscriber_id"])
