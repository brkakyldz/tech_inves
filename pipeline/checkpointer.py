"""Checkpointer factory (R23).

A mid-run crash previously re-bought every research branch
(`pipeline/graph.py`'s fan-out, up to the full watchlist) since nothing
persisted intermediate graph state -- `build_checkpointer()` returns a
`BaseCheckpointSaver` keyed by `run_id` (LangGraph's `thread_id`), so a
retried run with the same run_id resumes from its last completed step
instead of restarting from `init`.

Backend selection mirrors src/techinves/db/session.py's DATABASE_URL
convention, duplicated rather than imported -- pipeline/config.py stays
independent of src/techinves (see its module docstring). PostgresSaver is
imported lazily: it additionally needs a psycopg binary/system libpq, which
isn't available in every environment this package's tests run in, and a
sqlite-only environment must not be broken by importing it eagerly.
"""

from __future__ import annotations

import os
import sqlite3

from langgraph.checkpoint.base import BaseCheckpointSaver

from pipeline.config import REPO_ROOT

DEFAULT_SQLITE_PATH = REPO_ROOT / "data" / "pipeline_checkpoints.sqlite3"


def build_checkpointer(database_url: str | None = None) -> BaseCheckpointSaver:
    """`database_url` defaults to the `DATABASE_URL` env var (same variable
    src/techinves/db/session.py reads). A `postgresql...` URL builds a
    PostgresSaver; anything else (unset, or a `sqlite...` URL) builds a
    SqliteSaver against DEFAULT_SQLITE_PATH -- checkpoints are local
    process-recovery state, not something that needs to live in the same
    place as application data.
    """
    database_url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")

    if database_url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        # psycopg (sync, what PostgresSaver uses) needs a plain
        # postgresql:// URL, not the asyncpg-flavoured
        # postgresql+asyncpg:// techinves.db.session.py uses.
        sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        # Deliberately never __exit__'d: the connection stays open for the
        # life of the process, same lifetime as
        # techinves.db.session.py's module-level async engine.
        saver = PostgresSaver.from_conn_string(sync_url).__enter__()
        saver.setup()
        return saver

    from langgraph.checkpoint.sqlite import SqliteSaver

    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DEFAULT_SQLITE_PATH), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def checkpoint_config(run_id: str) -> dict:
    """The `configurable` block a checkpointed `graph.invoke()` needs.
    `thread_id` is LangGraph's checkpoint key -- set to this run's `run_id`
    so retrying the same run_id resumes instead of restarting."""
    return {"thread_id": run_id}
