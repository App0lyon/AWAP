"""Standalone worker process entrypoint."""

from __future__ import annotations

import os
import time

from awap.catalog import DEFAULT_NODE_CATALOG
from awap.database import create_database_engine, initialize_database
from awap.repository import SqlAlchemyWorkflowRepository
from awap.service import WorkflowService


def run() -> None:
    mode = os.getenv("AWAP_MODE", "local").lower()
    worker_count = int(os.getenv("AWAP_WORKER_COUNT", "2"))
    bootstrap_token = os.getenv("AWAP_BOOTSTRAP_ADMIN_TOKEN")
    if mode != "production" and bootstrap_token is None:
        bootstrap_token = "awap-dev-admin-token"

    engine = create_database_engine()
    initialize_database(engine)
    service = WorkflowService(
        repository=SqlAlchemyWorkflowRepository(engine),
        node_catalog=DEFAULT_NODE_CATALOG,
        worker_count=worker_count,
        bootstrap_username=os.getenv("AWAP_BOOTSTRAP_ADMIN_USERNAME", "admin"),
        bootstrap_token=bootstrap_token,
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        service.shutdown()
