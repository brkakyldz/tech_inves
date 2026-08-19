"""Run identity replaces week identity

ADR 0010 §2 / plan (`reports/plans/2026-08-18_on-demand-transformation.md`)
Faz 2 (`reports/agents/2026-08-18_faz2-run-identity-rekey.md`): `week_of` is
gone as an identity everywhere in `src/techinves/db/models.py`. This
migration brings the schema in line with that model change.

What moves:

* `pipeline_runs` is renamed to `runs` and extended with `trigger_type`,
  `ticker`, `status`, `started_at`, `finished_at`, `log`, `error`,
  `company_count`; `week_of` is dropped. Every measurement column gets a
  default, since a `scores` run never touches the LLM and legitimately
  leaves each of them empty.
* `ingestion_runs` is dropped -- folded into `runs` (`trigger_type='scores'`,
  its one column with no counterpart, `company_count`, moved across).
* `score_history.run_id` is promoted from a plain string to a real FK onto
  `runs.run_id`, unique with `company_id`; `week_of` is dropped.
* `reports` gains `run_id` (FK onto `runs.run_id`); `week_of` is dropped.
* `covered_events` gains `event_key`, `first_covered_run`,
  `last_updated_run`, `run_seq`; `first_covered_week`/`last_updated_week`
  are dropped.
* The pre-existing Alembic drift on `pipeline_runs.run_id`'s unique
  constraint (named `uq_pipeline_runs_run_id` in `d4a8f2c1b930`, never
  declared on the model) is closed here: the model now declares it
  explicitly as `uq_runs_run_id`.

Data is not carried forward (plan §9 Q3, decided): `dev.db` is reset
alongside this migration rather than backfilled. `week_of` is dropped
without being copied anywhere -- there is no column upgrade() preserves it
into -- which is what makes the downgrade below partially irreversible; see
its docstring.

SQLite (this project's dev database) cannot do most of this with a plain
`ALTER TABLE`, so every column drop/add and constraint change here goes
through `op.batch_alter_table(..., recreate="always")`, which rebuilds the
table (copy-and-rename) rather than attempting native `ALTER`. `recreate`
also resolves the indexes-and-constraints question in the task brief: an
index or unique constraint created against `pipeline_runs` keeps that exact
name after a bare `op.rename_table`, which is why the old
`ix_pipeline_runs_*` / `uq_pipeline_runs_run_id` names are dropped
explicitly by name (post-rename) rather than assumed to have followed the
rename to `ix_runs_*` on their own.

Revision ID: 769a03b2c26c
Revises: f1a9c3e7b482
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "769a03b2c26c"
down_revision: Union[str, None] = "f1a9c3e7b482"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ingestion_runs folds into runs; nothing carries forward (§9 Q3) ---
    op.drop_table("ingestion_runs")

    # --- pipeline_runs -> runs ---
    op.rename_table("pipeline_runs", "runs")

    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_index("ix_pipeline_runs_week_of")
        batch_op.drop_index("ix_pipeline_runs_run_id")
        batch_op.drop_constraint("uq_pipeline_runs_run_id", type_="unique")
        batch_op.drop_column("week_of")
        batch_op.add_column(
            sa.Column("trigger_type", sa.String(20), nullable=False, server_default="report")
        )
        batch_op.add_column(sa.Column("ticker", sa.String(10), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(20), nullable=False, server_default="queued")
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("log", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("error", sa.String(2000), nullable=True))
        batch_op.add_column(
            sa.Column("company_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.alter_column(
            "duration_seconds", existing_type=sa.Float(), nullable=False, server_default="0.0"
        )
        batch_op.create_unique_constraint("uq_runs_run_id", ["run_id"])
        batch_op.create_index("ix_runs_run_id", ["run_id"])
        batch_op.create_index("ix_runs_status", ["status"])
        batch_op.create_index("ix_runs_created_at", ["created_at"])

    # --- score_history: run_id promoted to a real FK, week_of dropped ---
    with op.batch_alter_table("score_history", recreate="always") as batch_op:
        batch_op.drop_index("ix_score_history_week_of")
        batch_op.drop_constraint("uq_score_history_company_week", type_="unique")
        batch_op.drop_column("week_of")
        batch_op.create_foreign_key(
            "fk_score_history_run_id_runs", "runs", ["run_id"], ["run_id"]
        )
        batch_op.create_unique_constraint(
            "uq_score_history_company_run", ["company_id", "run_id"]
        )
        batch_op.create_index("ix_score_history_run_id", ["run_id"])

    # --- reports: gains run_id, loses week_of ---
    # No server_default on run_id: the table is reset empty alongside this
    # migration (§9 Q3), so there is no row to backfill, and fabricating a
    # placeholder run_id for a hypothetical pre-existing row would be a
    # worse lie than a failed migration.
    with op.batch_alter_table("reports", recreate="always") as batch_op:
        batch_op.drop_index("ix_reports_week_of")
        batch_op.drop_column("week_of")
        batch_op.add_column(sa.Column("run_id", sa.String(64), nullable=False))
        batch_op.create_foreign_key("fk_reports_run_id_runs", "runs", ["run_id"], ["run_id"])
        batch_op.create_index("ix_reports_run_id", ["run_id"])

    # --- covered_events: gains event_key/first_covered_run/last_updated_run/run_seq ---
    with op.batch_alter_table("covered_events", recreate="always") as batch_op:
        batch_op.drop_index("ix_covered_events_last_updated_week")
        batch_op.drop_column("first_covered_week")
        batch_op.drop_column("last_updated_week")
        batch_op.add_column(sa.Column("event_key", sa.String(40), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column("first_covered_run", sa.String(64), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("last_updated_run", sa.String(64), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("run_seq", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_unique_constraint("uq_covered_events_event_key", ["event_key"])
        batch_op.create_index("ix_covered_events_event_key", ["event_key"])
        batch_op.create_index("ix_covered_events_last_updated_run", ["last_updated_run"])
        batch_op.create_index("ix_covered_events_run_seq", ["run_seq"])


def downgrade() -> None:
    """Structurally reverse the schema; refuse to fabricate lost data.

    `week_of` was dropped by upgrade() without being copied anywhere -- there
    is no column it survives into. An opaque `run_id` string carries no
    calendar information a downgrade could derive a week from. Splitting
    `runs` back into `pipeline_runs` + `ingestion_runs` *is* honestly
    reversible -- `trigger_type` records which of the two each row was
    ('scores' vs. everything else) -- but every row in all four tables this
    migration touches would still need a `week_of` value from nowhere.

    So: if any of `runs`, `score_history`, `reports`, `covered_events` holds
    a row, this refuses with `NotImplementedError` rather than backfilling a
    fabricated date. On an empty database (immediately after upgrading, with
    nothing written yet -- the case the verification loop exercises) the
    schema reverses fully.
    """
    conn = op.get_bind()
    for table in ("runs", "score_history", "reports", "covered_events"):
        count = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()
        if count:
            raise NotImplementedError(
                f"Cannot downgrade past {revision}: '{table}' has {count} row(s) "
                "carrying run-identity data (run_id / trigger_type / event_key). "
                "week_of was dropped by this migration's upgrade() without being "
                "persisted anywhere, and an opaque run_id carries no calendar "
                "information to derive it from -- there is no honest value to "
                "backfill. This downgrade only supports reversing the schema on "
                "an empty database (e.g. immediately after upgrading with no "
                "rows written yet)."
            )

    # --- covered_events: back to week_of columns ---
    with op.batch_alter_table("covered_events", recreate="always") as batch_op:
        batch_op.drop_index("ix_covered_events_run_seq")
        batch_op.drop_index("ix_covered_events_last_updated_run")
        batch_op.drop_index("ix_covered_events_event_key")
        batch_op.drop_constraint("uq_covered_events_event_key", type_="unique")
        batch_op.drop_column("run_seq")
        batch_op.drop_column("last_updated_run")
        batch_op.drop_column("first_covered_run")
        batch_op.drop_column("event_key")
        batch_op.add_column(sa.Column("first_covered_week", sa.Date(), nullable=False))
        batch_op.add_column(sa.Column("last_updated_week", sa.Date(), nullable=False))
        batch_op.create_index("ix_covered_events_last_updated_week", ["last_updated_week"])

    # --- reports: back to week_of ---
    with op.batch_alter_table("reports", recreate="always") as batch_op:
        batch_op.drop_index("ix_reports_run_id")
        batch_op.drop_constraint("fk_reports_run_id_runs", type_="foreignkey")
        batch_op.drop_column("run_id")
        batch_op.add_column(sa.Column("week_of", sa.Date(), nullable=False))
        batch_op.create_index("ix_reports_week_of", ["week_of"])

    # --- score_history: back to week_of, run_id un-FK'd ---
    with op.batch_alter_table("score_history", recreate="always") as batch_op:
        batch_op.drop_index("ix_score_history_run_id")
        batch_op.drop_constraint("uq_score_history_company_run", type_="unique")
        batch_op.drop_constraint("fk_score_history_run_id_runs", type_="foreignkey")
        batch_op.add_column(sa.Column("week_of", sa.Date(), nullable=False))
        batch_op.create_unique_constraint("uq_score_history_company_week", ["company_id", "week_of"])
        batch_op.create_index("ix_score_history_week_of", ["week_of"])

    # --- runs: split back into pipeline_runs + ingestion_runs ---
    # trigger_type carries which of the two a row was -- 'scores' is what
    # ingestion_runs recorded; everything else (report/company) is what
    # pipeline_runs recorded. Moot here (the table is empty, per the guard
    # above), but this is the part the task brief calls reversible.
    with op.batch_alter_table("runs", recreate="always") as batch_op:
        batch_op.drop_index("ix_runs_created_at")
        batch_op.drop_index("ix_runs_status")
        batch_op.drop_index("ix_runs_run_id")
        batch_op.drop_constraint("uq_runs_run_id", type_="unique")
        batch_op.drop_column("company_count")
        batch_op.drop_column("error")
        batch_op.drop_column("log")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("status")
        batch_op.drop_column("ticker")
        batch_op.drop_column("trigger_type")
        batch_op.add_column(sa.Column("week_of", sa.Date(), nullable=False))
        batch_op.alter_column(
            "duration_seconds", existing_type=sa.Float(), nullable=False, server_default=None
        )
        batch_op.create_unique_constraint("uq_pipeline_runs_run_id", ["run_id"])
        batch_op.create_index("ix_pipeline_runs_run_id", ["run_id"])
        batch_op.create_index("ix_pipeline_runs_week_of", ["week_of"])

    op.rename_table("runs", "pipeline_runs")

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("week_of", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("company_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
