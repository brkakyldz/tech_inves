"""Add subscriber consent/token columns and newsletter delivery tables

Email delivery (`reports/research/EMAIL_DELIVERY_IMPLEMENTATION_PLAN.md`
§3): `subscribers.token` backs both the `/confirm` and `/unsubscribe`
links -- a stored uuid4 rather than a signed token, because the unsubscribe
link must never expire (Gmail/Yahoo one-click requirement) and must be
revocable. `confirmed_at`/`consent_ip`/`consent_source` round out the
double opt-in / GDPR consent record; the existing `confirmed` boolean is
kept for backward compatibility.

`token` is NOT NULL but the table is non-empty (existing subscribers), so it
is added in three steps: add nullable -> backfill each row with a uuid4 ->
alter to NOT NULL.

`newsletter_sends` (one row per `techinves-send-newsletter` run) and
`newsletter_deliveries` (one row per report/subscriber send attempt) are new.
`newsletter_deliveries` deliberately has NO UniqueConstraint on
(report_id, subscriber_id): "at most one sent row per pair" only holds for
status="sent", and a failed attempt must stay retryable. Idempotency is
enforced by the sender's pre-send query
(`SELECT subscriber_id FROM newsletter_deliveries WHERE report_id=? AND
status='sent'`), not by a DB constraint.

Revision ID: 9d3b6e1a4c77
Revises: 7c1e9a3f5b02
Create Date: 2026-08-16
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9d3b6e1a4c77"
down_revision: Union[str, None] = "7c1e9a3f5b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- subscribers: new columns ---
    op.add_column("subscribers", sa.Column("token", sa.String(64), nullable=True))
    op.add_column("subscribers", sa.Column("confirmed_at", sa.DateTime(), nullable=True))
    op.add_column("subscribers", sa.Column("consent_ip", sa.String(45), nullable=True))
    op.add_column("subscribers", sa.Column("consent_source", sa.String(50), nullable=True))

    # Backfill: token is NOT NULL going forward, so every existing row needs
    # a unique value before the column can be tightened.
    connection = op.get_bind()
    subscribers = sa.table("subscribers", sa.column("id", sa.Integer), sa.column("token", sa.String))
    for (subscriber_id,) in connection.execute(sa.select(subscribers.c.id)).fetchall():
        connection.execute(
            subscribers.update().where(subscribers.c.id == subscriber_id).values(token=uuid.uuid4().hex)
        )

    # batch mode: SQLite has no ALTER COLUMN, so this recreates the table
    # under the hood; on dialects with native ALTER COLUMN it's equivalent
    # to a plain alter_column.
    with op.batch_alter_table("subscribers") as batch_op:
        batch_op.alter_column("token", existing_type=sa.String(64), nullable=False)
        batch_op.create_unique_constraint("uq_subscribers_token", ["token"])

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

    # --- newsletter_deliveries (no UniqueConstraint on report_id+subscriber_id -- see module docstring) ---
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


def downgrade() -> None:
    op.drop_index("ix_newsletter_deliveries_subscriber_id", table_name="newsletter_deliveries")
    op.drop_index("ix_newsletter_deliveries_report_id", table_name="newsletter_deliveries")
    op.drop_index("ix_newsletter_deliveries_send_id", table_name="newsletter_deliveries")
    op.drop_table("newsletter_deliveries")

    op.drop_index("ix_newsletter_sends_report_id", table_name="newsletter_sends")
    op.drop_table("newsletter_sends")

    with op.batch_alter_table("subscribers") as batch_op:
        batch_op.drop_constraint("uq_subscribers_token", type_="unique")
        batch_op.drop_column("consent_source")
        batch_op.drop_column("consent_ip")
        batch_op.drop_column("confirmed_at")
        batch_op.drop_column("token")
