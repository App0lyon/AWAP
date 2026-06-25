import sqlite3
from pathlib import Path

from awap.database import _normalize_database_url, create_database_engine, initialize_database


def test_sqlite_migration_smoke_creates_latest_schema_and_indexes(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-smoke.db"

    initialize_database(create_database_engine(f"sqlite:///{database_path}"))

    connection = sqlite3.connect(database_path)
    try:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        run_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(workflow_runs)")
        }
        event_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(workflow_run_events)")
        }
        approval_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(approval_tasks)")
        }
    finally:
        connection.close()

    assert versions == list(range(1, 9))
    assert "ix_workflow_runs_claim_queue" in run_indexes
    assert "ix_workflow_runs_search" in run_indexes
    assert "ix_workflow_run_events_run_time" in event_indexes
    assert "ix_approval_tasks_decision_created" in approval_indexes


def test_postgres_urls_use_psycopg_driver() -> None:
    assert (
        _normalize_database_url("postgres://user:pass@db/awap")
        == "postgresql+psycopg://user:pass@db/awap"
    )
    assert (
        _normalize_database_url("postgresql://user:pass@db/awap")
        == "postgresql+psycopg://user:pass@db/awap"
    )
