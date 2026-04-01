import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path, name: str = "awap-priority-zero.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _poll_run(client: TestClient, run_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest_response: dict | None = None

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in {"succeeded", "failed", "cancelled"}:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not finish in time. Last response: {latest_response}")


def test_auth_and_user_management(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    unauthorized = client.get("/node-types")
    assert unauthorized.status_code == 401

    me = client.get("/auth/me", headers=AUTH_HEADERS)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    created_user = client.post(
        "/users",
        json={"username": "editor", "role": "editor"},
        headers=AUTH_HEADERS,
    )
    assert created_user.status_code == 201
    assert created_user.json()["role"] == "editor"
    assert created_user.json()["token"]


def test_credentials_are_encrypted_at_rest(tmp_path: Path) -> None:
    db_path = tmp_path / "awap-encrypted.db"
    client = TestClient(create_app(database_url=f"sqlite:///{db_path}"))

    created = client.post(
        "/credentials",
        json={
            "name": "encrypted-secret",
            "kind": "bearer_token",
            "provider_key": "nvidia_build_free_chat",
            "secret_payload": {"bearer_token": "super-secret-token"},
        },
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 201

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT secret_ciphertext FROM workflow_credentials WHERE id = ?",
        (created.json()["id"],),
    ).fetchone()
    connection.close()

    assert row is not None
    assert row[0] != "super-secret-token"
    assert "super-secret-token" not in row[0]


def test_decision_branching_skips_unselected_steps(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-branching.db")))

    created = client.post(
        "/workflows",
        json={
            "name": "Approval flow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "decision",
                    "type": "decision",
                    "label": "Check approval",
                    "config": {"condition_key": "input.approved", "equals": True},
                },
                {
                    "id": "approved_notify",
                    "type": "notification",
                    "label": "Approved",
                    "config": {"channel": "slack", "message": "approved"},
                },
                {
                    "id": "rejected_notify",
                    "type": "notification",
                    "label": "Rejected",
                    "config": {"channel": "slack", "message": "rejected"},
                },
            ],
            "edges": [
                {"source": "start", "target": "decision"},
                {"source": "decision", "target": "approved_notify", "condition_value": True},
                {"source": "decision", "target": "rejected_notify", "is_default": True},
            ],
        },
        headers=AUTH_HEADERS,
    )
    workflow_id = created.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"approved": True}},
        headers=AUTH_HEADERS,
    )
    completed = _poll_run(client, started.json()["id"])

    statuses = {step["node_id"]: step["status"] for step in completed["steps"]}
    assert statuses["approved_notify"] == "succeeded"
    assert statuses["rejected_notify"] == "skipped"


def test_subworkflow_and_for_each_execution(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-subworkflow.db")))

    child = client.post(
        "/workflows",
        json={
            "name": "Child",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "notify",
                    "type": "notification",
                    "label": "Notify",
                    "config": {"channel": "slack", "message": "Processed {{input.item}}"},
                },
            ],
            "edges": [{"source": "start", "target": "notify"}],
        },
        headers=AUTH_HEADERS,
    )
    child_id = child.json()["id"]

    parent = client.post(
        "/workflows",
        json={
            "name": "Parent",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "loop",
                    "type": "for_each",
                    "label": "Loop",
                    "config": {
                        "workflow_id": child_id,
                        "items_path": "input.items",
                        "item_key": "item",
                    },
                },
            ],
            "edges": [{"source": "start", "target": "loop"}],
        },
        headers=AUTH_HEADERS,
    )
    parent_id = parent.json()["id"]

    started = client.post(
        f"/workflows/{parent_id}/runs",
        json={"input_payload": {"items": ["a", "b"]}},
        headers=AUTH_HEADERS,
    )
    completed = _poll_run(client, started.json()["id"])
    loop_output = completed["steps"][1]["output_payload"]

    assert loop_output["count"] == 2
    assert loop_output["results"][0]["result"]["last_output"]["message"] == "Processed a"
    assert loop_output["results"][1]["result"]["last_output"]["message"] == "Processed b"


def test_pause_resume_cancel_and_retry_from_failed_step(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path, "awap-controls.db")
    control_client = TestClient(create_app(database_url=database_url, worker_count=0))

    created = control_client.post(
        "/workflows",
        json={
            "name": "Control flow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "call_api",
                    "type": "http_request",
                    "label": "Call API",
                    "config": {"method": "GET", "url": "http://127.0.0.1:1/unreachable"},
                },
            ],
            "edges": [{"source": "start", "target": "call_api"}],
        },
        headers=AUTH_HEADERS,
    )
    workflow_id = created.json()["id"]

    queued_run = control_client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {}, "idempotency_key": "control-1"},
        headers=AUTH_HEADERS,
    ).json()

    paused = control_client.post(f"/runs/{queued_run['id']}/pause", headers=AUTH_HEADERS)
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = control_client.post(f"/runs/{queued_run['id']}/resume", headers=AUTH_HEADERS)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"

    worker_client = TestClient(create_app(database_url=database_url, worker_count=1))
    completed = _poll_run(worker_client, queued_run["id"])
    assert completed["status"] == "failed"

    retried = worker_client.post(
        f"/runs/{queued_run['id']}/retry?from_failed_step=true",
        headers=AUTH_HEADERS,
    )
    assert retried.status_code == 202
    retry_run = retried.json()
    assert retry_run["retry_of_run_id"] == queued_run["id"]
    assert retry_run["resume_from_step_index"] == 2

    cancelled = worker_client.post(f"/runs/{retry_run['id']}/cancel", headers=AUTH_HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] in {"cancelling", "cancelled"}
