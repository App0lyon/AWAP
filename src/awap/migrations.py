"""Lightweight schema migrations for AWAP."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, inspect, text

from awap.repository import Base
from awap.security import encrypt_secret_payload


def apply_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
        )
        existing_versions = {
            row[0]
            for row in connection.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }

    migrations: list[tuple[int, Callable[[Engine], None]]] = [
        (1, _migration_1_create_schema),
        (2, _migration_2_upgrade_existing_schema),
        (3, _migration_3_add_priority_one_tables),
    ]
    for version, migration in migrations:
        if version in existing_versions:
            continue
        migration(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
                ),
                {"version": version},
            )


def _migration_1_create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def _migration_2_upgrade_existing_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "workflows" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflows", "settings", "JSON")
    if "workflow_edges" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_edges", "condition_value", "VARCHAR(255)")
        _ensure_column(engine, inspector, "workflow_edges", "is_default", "BOOLEAN DEFAULT 0")
        _ensure_column(engine, inspector, "workflow_edges", "label", "VARCHAR(255) DEFAULT ''")
    if "workflow_runs" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_runs", "idempotency_key", "VARCHAR(255)")
        _ensure_column(engine, inspector, "workflow_runs", "timeout_seconds", "INTEGER")
        _ensure_column(engine, inspector, "workflow_runs", "retry_of_run_id", "VARCHAR(36)")
        _ensure_column(engine, inspector, "workflow_runs", "resume_from_step_index", "INTEGER")
        _ensure_column(engine, inspector, "workflow_runs", "locked_by", "VARCHAR(255)")
        _ensure_column(engine, inspector, "workflow_runs", "lease_expires_at", "DATETIME")
        _ensure_column(engine, inspector, "workflow_runs", "execution_state", "JSON")
    if "workflow_credentials" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_credentials", "secret_ciphertext", "TEXT")
        _ensure_column(engine, inspector, "workflow_credentials", "created_by", "VARCHAR(36)")
        _migrate_credential_payloads(engine)
    if "workflow_users" not in inspector.get_table_names():
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE workflow_users (
                        id VARCHAR(36) PRIMARY KEY,
                        username VARCHAR(255) UNIQUE NOT NULL,
                        role VARCHAR(30) NOT NULL,
                        token_hash VARCHAR(64) UNIQUE NOT NULL,
                        active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )


def _migration_3_add_priority_one_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "workflow_run_steps" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("workflow_run_steps")}
        if "status" in columns:
            return


def _ensure_column(
    engine: Engine,
    inspector: Any,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))


def _migrate_credential_payloads(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("workflow_credentials")}
    if "secret_payload" not in columns or "secret_ciphertext" not in columns:
        return

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, secret_payload, secret_ciphertext
                FROM workflow_credentials
                """
            )
        ).fetchall()
        for row in rows:
            if row.secret_ciphertext:
                continue
            payload = row.secret_payload
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if payload is None:
                payload = {}
            ciphertext = encrypt_secret_payload(payload)
            connection.execute(
                text(
                    """
                    UPDATE workflow_credentials
                    SET secret_ciphertext = :ciphertext
                    WHERE id = :credential_id
                    """
                ),
                {"ciphertext": ciphertext, "credential_id": row.id},
            )
