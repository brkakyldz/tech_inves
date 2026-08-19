"""In-flight run lock: unique partial index on runs(trigger_type)

Faz 3.3 of `reports/plans/2026-08-18_on-demand-transformation.md`, under
ADR 0010 §4: one active run per trigger type, refused rather than queued.

The lock is a **unique partial index** over `runs(trigger_type)`, restricted
to the two non-terminal statuses:

    CREATE UNIQUE INDEX uq_runs_active_trigger
        ON runs (trigger_type)
     WHERE status IN ('queued', 'running');

Three properties this shape buys, none of which a check-then-insert in
application code has:

* **It survives a reload.** The lock is a row, not a variable, so replacing
  the process (`uvicorn --reload`, a restart, a crash) does not hand out a
  second lock for work that is still recorded as in flight. The price is
  that a *dead* process's row keeps holding it -- cleared unconditionally at
  startup by `techinves.runs.reconcile`.
* **It cannot race.** Two concurrent triggers both insert; the database
  rejects exactly one, and `techinves.runs.service` converts that
  IntegrityError into a refusal naming the holder. There is no window
  between a check and an insert because there is no check.
* **History still accumulates.** `succeeded`/`failed` rows fall outside the
  predicate, so the index constrains only the three rows that can be in
  flight at once.

Both backends this project supports implement partial indexes -- SQLite
since 3.8.0, PostgreSQL always -- so the same DDL expresses the same
guarantee on both. The `sqlite_where`/`postgresql_where` pair below is one
predicate spelled for each dialect, and must stay identical to the pair on
`RunRow.__table_args__` in `src/techinves/db/models.py`, and to
`techinves.runs.reconcile.NON_TERMINAL_STATUSES`.

The index is created directly rather than through `batch_alter_table`:
adding an index needs no table rebuild on SQLite, unlike Faz 2's column and
constraint changes.

**Existing rows.** `dev.db` is empty at `769a03b2c26c` (plan §12, Faz 2), so
there is nothing to conflict here. A non-empty database carrying two
non-terminal rows of the same trigger type would fail this migration --
correctly, and loudly: those rows are precisely the state the lock exists to
make impossible, and silently picking a survivor would be a data decision a
migration has no business making. The fix is to mark the stale ones failed,
which is what `techinves.runs.reconcile` does at every startup.

Revision ID: 3f5c81ad2e94
Revises: 769a03b2c26c
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3f5c81ad2e94"
down_revision: Union[str, None] = "769a03b2c26c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_runs_active_trigger"
ACTIVE_PREDICATE = "status IN ('queued', 'running')"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "runs",
        ["trigger_type"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_PREDICATE),
        postgresql_where=sa.text(ACTIVE_PREDICATE),
    )


def downgrade() -> None:
    """Fully reversible: the index holds no data, and dropping it only
    removes the guarantee. An application running against the downgraded
    schema has no in-flight lock at all -- two concurrent triggers of the
    same type would both proceed -- which is the pre-Faz-3 behaviour this
    revision replaced."""
    op.drop_index(INDEX_NAME, table_name="runs")
