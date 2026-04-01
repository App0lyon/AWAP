import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path, name: str = "awap-priority-two.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _poll_run(
    client: TestClient,
    run_id: str,
    *,
    timeout_seconds: float = 8.0,
    expected_statuses: set[str] | None = None,
) -> dict:
    deadline = time.time() + timeout_seconds
    target_statuses = expected_statuses or {"succeeded", "failed", "cancelled"}
    latest_response: dict | None = None

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in target_statuses:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not reach {sorted(target_statuses)} in time. Last response: {latest_response}")


def _wait_for_runs(client: TestClient, workflow_id: str, *, timeout_seconds: float = 8.0) -> list[dict]:
    deadline = time.time() + timeout_seconds
    latest_runs: list[dict] = []

    while time.time() < deadline:
        response = client.get(f"/workflows/{workflow_id}/runs", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_runs = response.json()
        if latest_runs:
            return latest_runs
        time.sleep(0.1)

    raise AssertionError(f"No runs appeared for workflow {workflow_id}. Last response: {latest_runs}")


def test_scheduler_and_webhook_triggers_create_runs(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-triggers.db")))

    workflow = client.post(
        "/workflows",
        json={
            "name": "Trigger workflow",
            "nodes": [
                {"id": "schedule", "type": "schedule_trigger", "label": "Schedule", "config": {"cron": "* * * * *"}},
                {"id": "webhook", "type": "webhook_trigger", "label": "Webhook"},
                {
                    "id": "schedule_notify",
                    "type": "notification",
                    "label": "Schedule notify",
                    "config": {"channel": "slack", "message": "scheduled"},
                },
                {
                    "id": "webhook_notify",
                    "type": "notification",
                    "label": "Webhook notify",
                    "config": {"channel": "slack", "message": "webhook"},
                },
            ],
            "edges": [
                {"source": "schedule", "target": "schedule_notify"},
                {"source": "webhook", "target": "webhook_notify"},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]

    scheduled_runs = _wait_for_runs(client, workflow_id)
    scheduled_completed = _poll_run(client, scheduled_runs[0]["id"])
    scheduled_statuses = {step["node_id"]: step["status"] for step in scheduled_completed["steps"]}
    assert scheduled_statuses["schedule"] == "succeeded"
    assert scheduled_statuses["schedule_notify"] == "succeeded"
    assert scheduled_statuses["webhook"] == "skipped"
    assert scheduled_statuses["webhook_notify"] == "skipped"

    trigger_states = client.get("/trigger-states", headers=AUTH_HEADERS)
    assert trigger_states.status_code == 200
    assert any(state["node_id"] == "schedule" for state in trigger_states.json())

    webhook_run = client.post(
        f"/triggers/webhook/{workflow_id}",
        json={"input_payload": {"source": "api"}},
        headers=AUTH_HEADERS,
    )
    assert webhook_run.status_code == 202
    webhook_completed = _poll_run(client, webhook_run.json()["id"])
    webhook_statuses = {step["node_id"]: step["status"] for step in webhook_completed["steps"]}
    assert webhook_statuses["webhook"] == "succeeded"
    assert webhook_statuses["webhook_notify"] == "succeeded"
    assert webhook_statuses["schedule"] == "skipped"
    assert webhook_statuses["schedule_notify"] == "skipped"


def test_retry_policies_dead_letters_and_fallback_routes(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-retries.db")))

    failing_workflow = client.post(
        "/workflows",
        json={
            "name": "Retry failure workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "request",
                    "type": "http_request",
                    "label": "Request",
                    "config": {
                        "method": "GET",
                        "url": "http://127.0.0.1:1/unreachable",
                        "retry_policy": {"max_attempts": 2, "backoff_seconds": 0.01, "backoff_multiplier": 1.0},
                    },
                },
            ],
            "edges": [{"source": "start", "target": "request"}],
        },
        headers=AUTH_HEADERS,
    )
    assert failing_workflow.status_code == 201
    failing_workflow_id = failing_workflow.json()["id"]

    failing_run = client.post(
        f"/workflows/{failing_workflow_id}/runs",
        json={"input_payload": {}},
        headers=AUTH_HEADERS,
    )
    assert failing_run.status_code == 202
    failed = _poll_run(client, failing_run.json()["id"])
    assert failed["status"] == "failed"

    events = client.get(f"/runs/{failed['id']}/events", headers=AUTH_HEADERS)
    assert events.status_code == 200
    assert any(event["event_type"] == "step.retry_scheduled" for event in events.json())

    dead_letters = client.get("/dead-letters", params={"workflow_id": failing_workflow_id}, headers=AUTH_HEADERS)
    assert dead_letters.status_code == 200
    assert dead_letters.json()[0]["node_id"] == "request"

    fallback_workflow = client.post(
        "/workflows",
        json={
            "name": "Fallback workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "request",
                    "type": "http_request",
                    "label": "Request",
                    "config": {
                        "method": "GET",
                        "url": "http://127.0.0.1:1/unreachable",
                        "retry_policy": {
                            "max_attempts": 1,
                            "fallback_output": {"route": "fallback", "response": "cached response"},
                        },
                    },
                },
                {
                    "id": "success_notify",
                    "type": "notification",
                    "label": "Success",
                    "config": {"channel": "slack", "message": "success"},
                },
                {
                    "id": "fallback_notify",
                    "type": "notification",
                    "label": "Fallback",
                    "config": {"channel": "slack", "message": "fallback"},
                },
            ],
            "edges": [
                {"source": "start", "target": "request"},
                {"source": "request", "target": "fallback_notify", "condition_value": "fallback"},
                {"source": "request", "target": "success_notify", "is_default": True},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert fallback_workflow.status_code == 201

    fallback_run = client.post(
        f"/workflows/{fallback_workflow.json()['id']}/runs",
        json={"input_payload": {}},
        headers=AUTH_HEADERS,
    )
    assert fallback_run.status_code == 202
    completed = _poll_run(client, fallback_run.json()["id"])
    statuses = {step["node_id"]: step["status"] for step in completed["steps"]}
    request_step = next(step for step in completed["steps"] if step["node_id"] == "request")
    assert completed["status"] == "succeeded"
    assert request_step["output_payload"]["fallback_applied"] is True
    assert statuses["fallback_notify"] == "succeeded"
    assert statuses["success_notify"] == "skipped"


def test_environment_promotion_observability_and_export_import(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-environments.db")))

    created_environment = client.post(
        "/environments",
        json={"name": "qa", "description": "QA", "variables": {"region": "eu-west-1"}},
        headers=AUTH_HEADERS,
    )
    assert created_environment.status_code == 201

    workflow_v1 = client.post(
        "/workflows",
        json={
            "name": "Environment workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "notify",
                    "type": "notification",
                    "label": "Notify",
                    "config": {"channel": "slack", "message": "env={{environment.name}} region={{environment.variables.region}} v1"},
                },
            ],
            "edges": [{"source": "start", "target": "notify"}],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow_v1.status_code == 201
    workflow_id = workflow_v1.json()["id"]

    workflow_v2 = client.post(
        f"/workflows/{workflow_id}/versions",
        json={
            "name": "Environment workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "notify",
                    "type": "notification",
                    "label": "Notify",
                    "config": {"channel": "slack", "message": "env={{environment.name}} region={{environment.variables.region}} v2"},
                },
            ],
            "edges": [{"source": "start", "target": "notify"}],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow_v2.status_code == 201

    promoted = client.post(
        f"/workflows/{workflow_id}/promotions",
        json={"environment": "qa", "version": 2},
        headers=AUTH_HEADERS,
    )
    assert promoted.status_code == 200
    assert promoted.json()["workflow_version"] == 2

    qa_run = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {}, "environment": "qa"},
        headers=AUTH_HEADERS,
    )
    assert qa_run.status_code == 202
    completed = _poll_run(client, qa_run.json()["id"])
    notify_step = next(step for step in completed["steps"] if step["node_id"] == "notify")
    assert completed["environment"] == "qa"
    assert "env=qa region=eu-west-1 v2" == notify_step["output_payload"]["message"]

    releases = client.get("/environments/qa/releases", headers=AUTH_HEADERS)
    assert releases.status_code == 200
    assert releases.json()[0]["workflow_version"] == 2

    search_runs = client.get(
        "/runs/search",
        params={"environment": "qa", "status": "succeeded"},
        headers=AUTH_HEADERS,
    )
    assert search_runs.status_code == 200
    assert search_runs.json()[0]["id"] == completed["id"]

    summary = client.get("/observability/summary", headers=AUTH_HEADERS)
    assert summary.status_code == 200
    assert summary.json()["total_runs"] >= 1
    assert summary.json()["succeeded_runs"] >= 1

    worker_health = client.get("/worker-health", headers=AUTH_HEADERS)
    assert worker_health.status_code == 200
    assert any(item["worker_id"] == "awap-scheduler" for item in worker_health.json())

    source_control = client.get("/source-control/status", headers=AUTH_HEADERS)
    assert source_control.status_code == 200
    assert "dirty" in source_control.json()

    exported = client.get(f"/workflows/{workflow_id}/export", headers=AUTH_HEADERS)
    assert exported.status_code == 200
    imported = client.post(
        "/workflows/import",
        json={"bundle": exported.json(), "as_new_workflow": True, "name_override": "Imported environment workflow"},
        headers=AUTH_HEADERS,
    )
    assert imported.status_code == 201
    assert imported.json()["name"] == "Imported environment workflow"
