import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-providers.db'}"


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


def test_provider_catalog_and_credential_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    providers = client.get("/providers")
    assert providers.status_code == 200
    provider_pairs = {(item["key"], item["kind"]) for item in providers.json()}
    assert ("echo_llm", "llm") in provider_pairs
    assert ("http_tool", "tool") in provider_pairs
    assert ("notification_tool", "tool") in provider_pairs
    assert ("repository_observer", "observability") in provider_pairs

    created = client.post(
        "/credentials",
        json={
            "name": "demo-llm-key",
            "kind": "api_key",
            "provider_key": "echo_llm",
            "description": "Demo credential",
            "secret_payload": {"api_key": "top-secret"},
        },
    )
    assert created.status_code == 201
    credential = created.json()
    assert "secret_payload" not in credential
    assert credential["provider_key"] == "echo_llm"

    listed = client.get("/credentials")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == credential["id"]

    fetched = client.get(f"/credentials/{credential['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "demo-llm-key"
    assert "secret_payload" not in fetched.json()


def test_llm_provider_uses_credentials_and_persists_observability_events(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    credential = client.post(
        "/credentials",
        json={
            "name": "echo-credential",
            "kind": "api_key",
            "provider_key": "echo_llm",
            "secret_payload": {"api_key": "top-secret"},
        },
    ).json()

    created = client.post(
        "/workflows",
        json={
            "name": "LLM provider workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "prompt",
                    "type": "llm_prompt",
                    "label": "Prompt",
                    "config": {
                        "provider": "echo_llm",
                        "credential_id": credential["id"],
                        "prompt_template": "Hello {{input.customer}}",
                        "model": "demo-model",
                    },
                },
            ],
            "edges": [{"source": "start", "target": "prompt"}],
        },
    )
    workflow_id = created.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"customer": "Ada"}},
    )
    assert started.status_code == 202
    run_id = started.json()["id"]

    completed = _poll_run(client, run_id)
    assert completed["status"] == "succeeded"
    assert completed["steps"][1]["output_payload"]["provider"] == "echo_llm"
    assert completed["steps"][1]["output_payload"]["credential_configured"] is True
    assert completed["steps"][1]["output_payload"]["prompt"] == "Hello Ada"

    events = client.get(f"/runs/{run_id}/events")
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()]
    assert event_types[0] == "run.queued"
    assert "run.started" in event_types
    assert "step.started" in event_types
    assert "step.succeeded" in event_types
    assert event_types[-1] == "run.succeeded"
