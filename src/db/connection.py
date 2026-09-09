"""Database connection layer.

Production: DATABASE_URL / SUPABASE_DB_URL (PostgreSQL, e.g. Supabase pooler).
Tests/local: SQLite file or :memory:.

The repository layer uses only SQL that works on both dialects (JSONB -> TEXT,
BIGINT[] -> TEXT of comma-separated ids). Postgres deployments should apply
migrations/001_init.sql for native JSONB, but the runtime code path is shared.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from src.db.schema import SQLITE_SCHEMA


class Database:
    """Thin wrapper exposing parametrized query helpers.

    Postgres uses %(name)s placeholders; SQLite uses :name. The repository
    layer writes named-style placeholders compatible with both.
    """

    dialect: str

    def query(self, sql: str, params: dict | None = None) -> list[dict]: ...
    def execute(self, sql: str, params: dict | None = None) -> int: ...
    def executescript(self, script: str) -> None: ...


class SQLiteDatabase(Database):
    dialect = "sqlite"

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.isolation_level = None  # autocommit; explicit BEGIN where needed

    def _ensure_open(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.isolation_level = None

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        with self._lock:
            self._ensure_open()
            cur = self._conn.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: dict | None = None) -> int:
        with self._lock:
            self._ensure_open()
            cur = self._conn.execute(sql, params or {})
            if sql.lstrip().upper().startswith(("INSERT", "REPLACE")):
                return cur.lastrowid if cur.lastrowid is not None else cur.rowcount
            return cur.rowcount  # UPDATE/DELETE: number of affected rows

    def executescript(self, script: str) -> None:
        with self._lock:
            self._ensure_open()
            self._conn.executescript(script)

    def migrate(self) -> None:
        self.executescript(SQLITE_SCHEMA)
        # `CREATE TABLE IF NOT EXISTS` above does nothing for a table that
        # already existed before a column was added to the schema (e.g. an
        # existing state/agent.db from before ideas.angle existed). Self-heal
        # by adding any missing columns; SQLite has no "ADD COLUMN IF NOT
        # EXISTS", so probe first.
        self._add_column_if_missing("ideas", "angle", "TEXT")

    def _add_column_if_missing(self, table: str, column: str, coltype: str) -> None:
        with self._lock:
            self._ensure_open()
            cols = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # Test helper: simulate a total DB outage for failure-mode tests.
    def simulate_outage(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._ensure_open = lambda: (_ for _ in ()).throw(
            sqlite3.OperationalError("simulated database outage"))


def get_db() -> Database:
    """Create the right DB backend from the environment.

    postgres:// / postgresql:// URLs -> Postgres/Supabase (production).
    file: paths or *.db paths -> SQLite (sandbox/local/tests).
    """
    import os

    db_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if db_url and db_url.startswith(("postgres://", "postgresql://")):
        return get_postgres_db(db_url)
    if db_url and db_url.startswith("file:"):
        path = db_url[5:]
    elif db_url and db_url.endswith(".db"):
        path = db_url
    else:
        path = os.environ.get("SQLITE_PATH", ":memory:")
    if path != ":memory:":
        from pathlib import Path as _P
        _P(path).parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteDatabase(path)
    db.migrate()
    return db


def get_postgres_db(db_url: str) -> Database:
    """Postgres/Supabase backend (lazy import; requires psycopg2-binary)."""
    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "DATABASE_URL set but psycopg2 not installed. pip install psycopg2-binary"
        ) from exc

    class PostgresDatabase(Database):
        dialect = "postgres"

        def __init__(self, url: str):
            self._conn = psycopg2.connect(url)
            self._conn.autocommit = True

        def query(self, sql: str, params: dict | None = None) -> list[dict]:
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or {})
                return [dict(r) for r in cur.fetchall()]

        def execute(self, sql: str, params: dict | None = None) -> int:
            with self._conn.cursor() as cur:
                cur.execute(sql, params or {})
                return cur.rowcount

        def executescript(self, script: str) -> None:
            with self._conn.cursor() as cur:
                cur.execute(script)

        def migrate(self) -> None:
            from pathlib import Path as _P

            migrations_dir = _P(__file__).resolve().parent.parent.parent / "migrations"
            for migration in sorted(migrations_dir.glob("*.sql")):
                self.executescript(migration.read_text(encoding="utf-8"))

    return PostgresDatabase(db_url)
