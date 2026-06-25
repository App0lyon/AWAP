import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-runs.db'}"


def _poll_run(client: TestClient, run_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest_response: dict | None = None

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in {"succeeded", "failed"}:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not finish in time. Last response: {latest_response}")


def _poll_run_status(
    client: TestClient,
    run_id: str,
    statuses: set[str],
    timeout_seconds: float = 5.0,
) -> dict:
    deadline = time.time() + timeout_seconds
    latest_response: dict | None = None

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in statuses:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not reach {statuses}. Last response: {latest_response}")


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
        headers=AUTH_HEADERS,
    )
    workflow_id = created_v1.json()["id"]

    publish_v1 = client.post(
        f"/workflows/{workflow_id}/versions/1/publish",
        headers=AUTH_HEADERS,
    )
    assert publish_v1.status_code == 200

    created_v2 = client.post(
        f"/workflows/{workflow_id}/versions",
        json=_publishable_workflow_payload("Order pipeline draft", "Draft message"),
        headers=AUTH_HEADERS,
    )
    assert created_v2.status_code == 201
    assert created_v2.json()["version"] == 2

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"customer": "Ada"}},
        headers=AUTH_HEADERS,
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
        headers=AUTH_HEADERS,
    )
    workflow_id = created_v1.json()["id"]

    created_v2 = client.post(
        f"/workflows/{workflow_id}/versions",
        json=_publishable_workflow_payload("Support flow v2", "Draft message"),
        headers=AUTH_HEADERS,
    )
    assert created_v2.status_code == 201

    started = client.post(
        f"/workflows/{workflow_id}/runs?version=2",
        json={"input_payload": {"ticket": "123"}},
        headers=AUTH_HEADERS,
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
        headers=AUTH_HEADERS,
    )
    workflow_id = created.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    run_id = started.json()["id"]

    completed = _poll_run(client, run_id)
    assert completed["status"] == "failed"
    assert "HTTP request failed" in completed["error_message"]
    assert completed["steps"][0]["status"] == "succeeded"
    assert completed["steps"][1]["status"] == "failed"

    listed = client.get(f"/workflows/{workflow_id}/runs", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run_id


def test_run_events_can_be_streamed(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=_database_url(tmp_path))) as client:
        created = client.post(
            "/workflows",
            json=_publishable_workflow_payload("Streaming workflow", "Streamed message"),
            headers=AUTH_HEADERS,
        )
        workflow_id = created.json()["id"]
        started = client.post(
            f"/workflows/{workflow_id}/runs",
            json={"input_payload": {}},
            headers=AUTH_HEADERS,
        )
        assert started.status_code == 202
        run_id = started.json()["id"]
        _poll_run(client, run_id)

        with client.stream(
            "GET",
            f"/runs/{run_id}/events/stream",
            headers=AUTH_HEADERS,
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        assert "data: " in body
        assert "run.succeeded" in body


def test_large_run_payloads_are_stored_as_artifact_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AWAP_MAX_STORED_PAYLOAD_BYTES", "512")
    monkeypatch.setenv("AWAP_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    client = TestClient(create_app(database_url=_database_url(tmp_path)))
    large_value = "x" * 2000

    created = client.post(
        "/workflows",
        json={
            "name": "Large payload workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "notify",
                    "type": "notification",
                    "label": "Notify",
                    "config": {"channel": "log", "message": large_value},
                },
                {
                    "id": "approval",
                    "type": "approval",
                    "label": "Approve",
                    "config": {"prompt_template": "Approve?"},
                },
            ],
            "edges": [
                {"source": "start", "target": "notify"},
                {"source": "notify", "target": "approval"},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"large": large_value}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert started.json()["input_payload"]["large"]["awap_artifact_ref"] is True

    waiting = _poll_run_status(client, run_id, {"waiting_human"})
    serialized_run = json.dumps(waiting)
    assert large_value not in serialized_run
    assert waiting["input_payload"]["large"]["awap_artifact_ref"] is True
    assert waiting["steps"][1]["output_payload"]["message"]["awap_artifact_ref"] is True
    assert waiting["execution_state"]["awap_artifact_ref"] is True

    artifact_path = Path(waiting["steps"][1]["output_payload"]["message"]["uri"])
    assert artifact_path.exists()
    assert artifact_path.is_relative_to(tmp_path / "artifacts")

    events = client.get(f"/runs/{run_id}/events", headers=AUTH_HEADERS)
    assert events.status_code == 200
    assert large_value not in json.dumps(events.json())
