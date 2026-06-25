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
        (4, _migration_4_add_priority_two_tables),
        (5, _migration_5_add_priority_three_tables),
        (6, _migration_6_add_environment_policy_and_credential_scopes),
        (7, _migration_7_add_performance_indexes),
        (8, _migration_8_add_pgvector_retrieval_backend),
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


def _migration_4_add_priority_two_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "workflow_runs" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_runs", "environment", "VARCHAR(120)")
        _ensure_column(engine, inspector, "workflow_runs", "trigger_node_ids", "JSON")


def _migration_5_add_priority_three_tables(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "workflows" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflows", "release_notes", "TEXT DEFAULT ''")
        _ensure_column(engine, inspector, "workflows", "owner_id", "VARCHAR(36)")


def _migration_6_add_environment_policy_and_credential_scopes(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "workflow_environments" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_environments", "policy", "JSON DEFAULT '{}'")
    if "workflow_credentials" in inspector.get_table_names():
        _ensure_column(engine, inspector, "workflow_credentials", "environment_names", "JSON DEFAULT '[]'")
        _ensure_column(engine, inspector, "workflow_credentials", "workflow_ids", "JSON DEFAULT '[]'")


def _migration_7_add_performance_indexes(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    indexes = [
        ("ix_workflows_workflow_version", "workflows", "workflow_id, version"),
        ("ix_workflows_state_name_version", "workflows", "state, name, version"),
        ("ix_workflow_runs_claim_queue", "workflow_runs", "status, created_at"),
        (
            "ix_workflow_runs_search",
            "workflow_runs",
            "workflow_id, status, environment, created_at",
        ),
        ("ix_workflow_run_events_run_time", "workflow_run_events", "run_id, timestamp"),
        (
            "ix_workflow_run_events_workflow_time",
            "workflow_run_events",
            "workflow_id, workflow_version, timestamp",
        ),
        (
            "ix_approval_tasks_decision_created",
            "approval_tasks",
            "decision, created_at",
        ),
        (
            "ix_approval_tasks_run_step_decision",
            "approval_tasks",
            "run_id, step_index, decision",
        ),
        (
            "ix_environment_releases_env_workflow",
            "workflow_environment_releases",
            "environment, workflow_id",
        ),
        (
            "ix_audit_logs_workflow_created",
            "audit_logs",
            "workflow_id, created_at",
        ),
        ("ix_audit_logs_run_created", "audit_logs", "run_id, created_at"),
        (
            "ix_dead_letters_workflow_created",
            "workflow_dead_letters",
            "workflow_id, created_at",
        ),
        (
            "ix_workflow_comments_version_created",
            "workflow_comments",
            "workflow_id, workflow_version, created_at",
        ),
        (
            "ix_knowledge_chunks_base_document",
            "knowledge_chunks",
            "knowledge_base_id, document_id",
        ),
    ]
    for index_name, table_name, columns in indexes:
        _ensure_index(engine, table_name, index_name, columns)


def _migration_8_add_pgvector_retrieval_backend(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS knowledge_vectors (
                    chunk_id VARCHAR(36) PRIMARY KEY
                        REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                    knowledge_base_id VARCHAR(36) NOT NULL,
                    document_id VARCHAR(36) NOT NULL,
                    embedding vector(64) NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO knowledge_vectors (
                    chunk_id,
                    knowledge_base_id,
                    document_id,
                    embedding
                )
                SELECT
                    id,
                    knowledge_base_id,
                    document_id,
                    ('[' || array_to_string(
                        ARRAY(SELECT jsonb_array_elements_text(embedding::jsonb)),
                        ','
                    ) || ']')::vector
                FROM knowledge_chunks
                ON CONFLICT (chunk_id) DO NOTHING
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_base_document
                ON knowledge_vectors (knowledge_base_id, document_id)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_embedding_hnsw
                ON knowledge_vectors USING hnsw (embedding vector_cosine_ops)
                """
            )
        )


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


def _ensure_index(engine: Engine, table_name: str, index_name: str, columns: str) -> None:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in existing_indexes:
        return
    with engine.begin() as connection:
        connection.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns})"))


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
