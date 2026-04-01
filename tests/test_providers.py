import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-providers.db'}"


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


def test_provider_catalog_and_credential_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    providers = client.get("/providers", headers=AUTH_HEADERS)
    assert providers.status_code == 200
    provider_pairs = {(item["key"], item["kind"]) for item in providers.json()}
    assert ("nvidia_build_free_chat", "llm") in provider_pairs
    assert ("http_tool", "tool") in provider_pairs
    assert ("notification_tool", "tool") in provider_pairs
    assert ("repository_observer", "observability") in provider_pairs

    created = client.post(
        "/credentials",
        json={
            "name": "demo-llm-key",
            "kind": "bearer_token",
            "provider_key": "nvidia_build_free_chat",
            "description": "Demo credential",
            "secret_payload": {"bearer_token": "top-secret"},
        },
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 201
    credential = created.json()
    assert "secret_payload" not in credential
    assert credential["provider_key"] == "nvidia_build_free_chat"

    listed = client.get("/credentials", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == credential["id"]

    fetched = client.get(f"/credentials/{credential['id']}", headers=AUTH_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "demo-llm-key"
    assert "secret_payload" not in fetched.json()


def test_llm_provider_uses_credentials_and_persists_observability_events(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    credential = client.post(
        "/credentials",
        json={
            "name": "nvidia-credential",
            "kind": "bearer_token",
            "provider_key": "nvidia_build_free_chat",
            "secret_payload": {"bearer_token": "top-secret"},
        },
        headers=AUTH_HEADERS,
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
                        "provider": "nvidia_build_free_chat",
                        "mock_response": "Hello Ada from NVIDIA",
                        "credential_id": credential["id"],
                        "prompt_template": "Hello {{input.customer}}",
                        "model": "meta/llama-3.1-8b-instruct",
                    },
                },
            ],
            "edges": [{"source": "start", "target": "prompt"}],
        },
        headers=AUTH_HEADERS,
    )
    workflow_id = created.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"customer": "Ada"}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    run_id = started.json()["id"]

    completed = _poll_run(client, run_id)
    assert completed["status"] == "succeeded"
    assert completed["steps"][1]["output_payload"]["provider"] == "nvidia_build_free_chat"
    assert completed["steps"][1]["output_payload"]["prompt"] == "Hello Ada"
    assert completed["steps"][1]["output_payload"]["response"] == "Hello Ada from NVIDIA"

    events = client.get(f"/runs/{run_id}/events", headers=AUTH_HEADERS)
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()]
    assert event_types[0] == "run.queued"
    assert "run.started" in event_types
    assert "step.started" in event_types
    assert "step.succeeded" in event_types
    assert event_types[-1] == "run.succeeded"
