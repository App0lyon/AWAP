"""Database engine and session setup."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine

from awap.repository import Base

DEFAULT_DATABASE_URL = "sqlite:///./awap.db"


def get_database_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url
    return os.getenv("AWAP_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_database_engine(database_url: str | None = None) -> Engine:
    resolved_url = get_database_url(database_url)
    connect_args = {"check_same_thread": False} if resolved_url.startswith("sqlite") else {}
    return create_engine(resolved_url, future=True, connect_args=connect_args)


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
