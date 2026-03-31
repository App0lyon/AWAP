import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-runs.db'}"


def _poll_run(client: TestClient, run_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest_response: dict | None = None

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in {"succeeded", "failed"}:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not finish in time. Last response: {latest_response}")


def _publishable_workflow_payload(name: str, notification_message: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {"id": "start", "type": "manual_trigger", "label": "Start"},
            {
                "id": "notify",
                "type": "notification",
                "label": "Notify",
                "config": {"channel": "slack", "message": notification_message},
            },
        ],
        "edges": [{"source": "start", "target": "notify"}],
    }


def test_start_run_executes_published_version_by_default(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created_v1 = client.post(
        "/workflows",
        json=_publishable_workflow_payload("Order pipeline", "Published message"),
    )
    workflow_id = created_v1.json()["id"]

    publish_v1 = client.post(f"/workflows/{workflow_id}/versions/1/publish")
    assert publish_v1.status_code == 200

    created_v2 = client.post(
        f"/workflows/{workflow_id}/versions",
        json=_publishable_workflow_payload("Order pipeline draft", "Draft message"),
    )
    assert created_v2.status_code == 201
    assert created_v2.json()["version"] == 2

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"customer": "Ada"}},
    )
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.json()["workflow_version"] == 1
    assert started.json()["status"] == "queued"

    completed = _poll_run(client, run_id)
    assert completed["status"] == "succeeded"
    assert completed["workflow_version"] == 1
    assert [step["status"] for step in completed["steps"]] == ["succeeded", "succeeded"]
    assert completed["steps"][1]["output_payload"]["message"] == "Published message"


def test_run_can_target_specific_draft_version(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created_v1 = client.post(
        "/workflows",
        json=_publishable_workflow_payload("Support flow", "Stable message"),
    )
    workflow_id = created_v1.json()["id"]

    created_v2 = client.post(
        f"/workflows/{workflow_id}/versions",
        json=_publishable_workflow_payload("Support flow v2", "Draft message"),
    )
    assert created_v2.status_code == 201

    started = client.post(
        f"/workflows/{workflow_id}/runs?version=2",
        json={"input_payload": {"ticket": "123"}},
    )
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.json()["workflow_version"] == 2

    completed = _poll_run(client, run_id)
    assert completed["status"] == "succeeded"
    assert completed["steps"][1]["output_payload"]["message"] == "Draft message"


def test_run_failure_is_tracked_per_step_and_job(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created = client.post(
        "/workflows",
        json={
            "name": "Broken HTTP workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "call_api",
                    "type": "http_request",
                    "label": "Call API",
                    "config": {
                        "method": "GET",
                        "url": "http://127.0.0.1:1/unreachable",
                    },
                },
            ],
            "edges": [{"source": "start", "target": "call_api"}],
        },
    )
    workflow_id = created.json()["id"]

    started = client.post(f"/workflows/{workflow_id}/runs", json={"input_payload": {}})
    assert started.status_code == 202
    run_id = started.json()["id"]

    completed = _poll_run(client, run_id)
    assert completed["status"] == "failed"
    assert "HTTP request failed" in completed["error_message"]
    assert completed["steps"][0]["status"] == "succeeded"
    assert completed["steps"][1]["status"] == "failed"

    listed = client.get(f"/workflows/{workflow_id}/runs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run_id
