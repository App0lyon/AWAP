import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from awap import security
from awap.api.app import _resolve_worker_count, create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path, name: str = "awap-platform-hardening.db") -> str:
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


def test_readiness_blocks_environment_policy_violations(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=_database_url(tmp_path, "policy.db"))) as client:
        environment = client.post(
            "/environments",
            json={
                "name": "locked",
                "description": "Locked down environment",
                "policy": {"allowed_http_hosts": ["api.example.com"]},
            },
            headers=AUTH_HEADERS,
        )
        assert environment.status_code == 201

        workflow = client.post(
            "/workflows",
            json={
                "name": "Unsafe HTTP workflow",
                "nodes": [
                    {"id": "start", "type": "manual_trigger", "label": "Start"},
                    {
                        "id": "request",
                        "type": "http_request",
                        "label": "Request",
                        "config": {
                            "method": "GET",
                            "url": "https://evil.example.test/data",
                            "mock_response": {"ok": True},
                        },
                    },
                ],
                "edges": [{"source": "start", "target": "request"}],
            },
            headers=AUTH_HEADERS,
        )
        assert workflow.status_code == 201
        workflow_id = workflow.json()["id"]

        readiness = client.get(
            f"/workflows/{workflow_id}/versions/1/readiness",
            params={"environment": "locked"},
            headers=AUTH_HEADERS,
        )
        assert readiness.status_code == 200
        readiness_body = readiness.json()
        assert readiness_body["ready"] is False
        assert any(check["name"] == "http_policy:request" for check in readiness_body["checks"])

        promoted = client.post(
            f"/workflows/{workflow_id}/promotions",
            json={"environment": "locked", "version": 1},
            headers=AUTH_HEADERS,
        )
        assert promoted.status_code == 400
        assert "not ready" in promoted.json()["detail"]

        started = client.post(
            f"/workflows/{workflow_id}/runs",
            json={"input_payload": {}, "environment": "locked"},
            headers=AUTH_HEADERS,
        )
        assert started.status_code == 400
        assert "Cannot start an environment run" in started.json()["detail"]


def test_credentials_can_be_scoped_to_environment_and_workflow(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path, "scoped-credential.db")
    with TestClient(create_app(database_url=database_url)) as client:
        workflow = client.post(
            "/workflows",
            json={
                "name": "Scoped credential workflow",
                "nodes": [
                    {"id": "start", "type": "manual_trigger", "label": "Start"},
                    {
                        "id": "prompt",
                        "type": "llm_prompt",
                        "label": "Prompt",
                        "config": {
                            "provider": "nvidia_build_free_chat",
                            "prompt_template": "Hello {{input.name}}",
                            "model": "meta/llama-3.1-8b-instruct",
                            "mock_response": "hello",
                        },
                    },
                ],
                "edges": [{"source": "start", "target": "prompt"}],
            },
            headers=AUTH_HEADERS,
        )
        workflow_id = workflow.json()["id"]

        credential = client.post(
            "/credentials",
            json={
                "name": "prod-only",
                "kind": "bearer_token",
                "provider_key": "nvidia_build_free_chat",
                "environment_names": ["prod"],
                "workflow_ids": [workflow_id],
                "secret_payload": {"bearer_token": "top-secret"},
            },
            headers=AUTH_HEADERS,
        )
        assert credential.status_code == 201

        updated = workflow.json()
        updated["nodes"][1]["config"]["credential_id"] = credential.json()["id"]
        version_two = client.post(
            f"/workflows/{workflow_id}/versions",
            json=updated,
            headers=AUTH_HEADERS,
        )
        assert version_two.status_code == 201

        staging_readiness = client.get(
            f"/workflows/{workflow_id}/versions/2/readiness",
            params={"environment": "staging"},
            headers=AUTH_HEADERS,
        )
        assert staging_readiness.status_code == 200
        assert staging_readiness.json()["ready"] is False

        prod_readiness = client.get(
            f"/workflows/{workflow_id}/versions/2/readiness",
            params={"environment": "prod"},
            headers=AUTH_HEADERS,
        )
        assert prod_readiness.status_code == 200
        assert prod_readiness.json()["ready"] is True


def test_run_event_payloads_are_redacted(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=_database_url(tmp_path, "redaction.db"))) as client:
        workflow = client.post(
            "/workflows",
            json={
                "name": "Redaction workflow",
                "nodes": [
                    {"id": "start", "type": "manual_trigger", "label": "Start"},
                    {
                        "id": "prompt",
                        "type": "llm_prompt",
                        "label": "Prompt",
                        "config": {
                            "provider": "nvidia_build_free_chat",
                            "prompt_template": "Secret customer {{input.customer}}",
                            "model": "meta/llama-3.1-8b-instruct",
                            "mock_response": "Sensitive answer",
                        },
                    },
                ],
                "edges": [{"source": "start", "target": "prompt"}],
            },
            headers=AUTH_HEADERS,
        )
        workflow_id = workflow.json()["id"]
        run = client.post(
            f"/workflows/{workflow_id}/runs",
            json={"input_payload": {"customer": "Ada"}},
            headers=AUTH_HEADERS,
        )
        assert run.status_code == 202
        _poll_run(client, run.json()["id"])

        events = client.get(f"/runs/{run.json()['id']}/events", headers=AUTH_HEADERS)
        assert events.status_code == 200
        serialized_events = str(events.json())
        assert "Secret customer Ada" not in serialized_events
        assert "Sensitive answer" not in serialized_events
        assert "[redacted]" in serialized_events


def test_provider_connection_and_infrastructure_status(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=_database_url(tmp_path, "status.db"))) as client:
        provider = client.get(
            "/providers/nvidia_build_free_chat/connection",
            headers=AUTH_HEADERS,
        )
        assert provider.status_code == 200
        assert provider.json()["available"] is True
        assert provider.json()["provider_key"] == "nvidia_build_free_chat"

        infrastructure = client.get("/infrastructure/status", headers=AUTH_HEADERS)
        assert infrastructure.status_code == 200
        assert infrastructure.json()["queue_backend"] == "sqlite_lease_table"
        assert infrastructure.json()["distributed_workers_supported"] is False

        alerts = client.get("/observability/alerts", headers=AUTH_HEADERS)
        assert alerts.status_code == 200
        assert alerts.json() == []


def test_production_mode_requires_explicit_bootstrap_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWAP_MODE", "production")
    monkeypatch.delenv("AWAP_BOOTSTRAP_ADMIN_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="AWAP_BOOTSTRAP_ADMIN_TOKEN"):
        create_app(database_url=_database_url(tmp_path, "production-bootstrap.db"))


def test_production_mode_requires_explicit_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWAP_MODE", "production")
    monkeypatch.delenv("AWAP_SECRET_KEY", raising=False)
    security._get_fernet.cache_clear()  # noqa: SLF001

    with pytest.raises(RuntimeError, match="AWAP_SECRET_KEY"):
        security.encrypt_secret_payload({"api_key": "secret"})

    security._get_fernet.cache_clear()  # noqa: SLF001


def test_production_mode_defaults_api_process_to_zero_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWAP_WORKER_COUNT", raising=False)

    assert _resolve_worker_count(None, "production") == 0
    assert _resolve_worker_count(None, "local") == 2
    assert _resolve_worker_count(3, "production") == 3
