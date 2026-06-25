"""Database engine and migration setup."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine

from awap.migrations import apply_migrations

DEFAULT_DATABASE_URL = "sqlite:///./awap.db"


def get_database_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url
    return os.getenv("AWAP_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str | None = None) -> Engine:
    resolved_url = _normalize_database_url(get_database_url(database_url))
    is_sqlite = resolved_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine_options: dict[str, object] = {
        "future": True,
        "connect_args": connect_args,
        "pool_pre_ping": True,
    }
    if not is_sqlite:
        engine_options["pool_size"] = int(os.getenv("AWAP_DATABASE_POOL_SIZE", "5"))
        engine_options["max_overflow"] = int(os.getenv("AWAP_DATABASE_MAX_OVERFLOW", "10"))
    return create_engine(resolved_url, **engine_options)


def initialize_database(engine: Engine) -> None:
    apply_migrations(engine)


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url
