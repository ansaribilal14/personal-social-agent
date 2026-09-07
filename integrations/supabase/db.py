"""Supabase/Postgres connector (spec section 6: PostgreSQL is the runtime state).

DATABASE_URL or SUPABASE_DB_URL points at the Supabase pooler connection
string. Secrets live in GitHub Secrets only. This module is intentionally
thin - src/db/connection.py handles dialect behavior.
"""
from __future__ import annotations

import os

from src.db.connection import Database, get_postgres_db


def get_supabase_db() -> Database:
    url = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_DB_URL/DATABASE_URL not configured - runtime state requires "
            "PostgreSQL (spec section 6). Configure the Supabase pooler URL in "
            "GitHub Secrets.")
    return get_postgres_db(url)
