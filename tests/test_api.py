from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-test.db'}"


def _valid_workflow_payload(name: str = "Customer support triage") -> dict:
    return {
        "name": name,
        "nodes": [
            {"id": "start", "type": "manual_trigger", "label": "Start"},
            {
                "id": "prompt",
                "type": "llm_prompt",
                "label": "Categorize",
                "config": {
                    "prompt_template": "Categorize ticket {{ticket}}",
                    "model": "gpt-4.1-mini",
                },
            },
            {
                "id": "notify",
                "type": "notification",
                "label": "Notify agent",
                "config": {"channel": "slack", "message": "New triage result available"},
            },
        ],
        "edges": [
            {"source": "start", "target": "prompt"},
            {"source": "prompt", "target": "notify"},
        ],
    }


def test_create_validate_and_plan_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created = client.post("/workflows", json=_valid_workflow_payload())
    assert created.status_code == 201
    created_body = created.json()
    workflow_id = created_body["id"]
    assert created_body["version"] == 1
    assert created_body["state"] == "draft"

    validated = client.post(f"/workflows/{workflow_id}/validate")
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    planned = client.post(f"/workflows/{workflow_id}/plan")
    assert planned.status_code == 200
    assert planned.json()["version"] == 1
    assert [step["node_id"] for step in planned.json()["steps"]] == ["start", "prompt", "notify"]


def test_invalid_workflow_returns_bad_request_for_plan(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    payload = {
        "name": "Broken workflow",
        "nodes": [
            {"id": "start", "type": "manual_trigger", "label": "Start"},
            {"id": "prompt", "type": "llm_prompt", "label": "Categorize", "config": {}},
        ],
        "edges": [{"source": "start", "target": "prompt"}],
    }

    created = client.post("/workflows", json=payload)
    workflow_id = created.json()["id"]

    planned = client.post(f"/workflows/{workflow_id}/plan")
    assert planned.status_code == 400


def test_workflow_persists_across_app_instances(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path)

    first_client = TestClient(create_app(database_url=database_url))
    payload = {
        "name": "Persistent workflow",
        "nodes": [
            {"id": "start", "type": "manual_trigger", "label": "Start"},
            {
                "id": "call_api",
                "type": "http_request",
                "label": "Call API",
                "config": {"method": "GET", "url": "https://example.com"},
            },
        ],
        "edges": [{"source": "start", "target": "call_api"}],
    }

    created = first_client.post("/workflows", json=payload)
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    second_client = TestClient(create_app(database_url=database_url))
    fetched = second_client.get(f"/workflows/{workflow_id}")

    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Persistent workflow"
    assert fetched.json()["version"] == 1


def test_workflow_versioning_and_publish_flow(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created = client.post("/workflows", json=_valid_workflow_payload(name="Support triage v1"))
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    published_v1 = client.post(f"/workflows/{workflow_id}/versions/1/publish")
    assert published_v1.status_code == 200
    assert published_v1.json()["state"] == "published"
    assert published_v1.json()["version"] == 1

    version_two_payload = _valid_workflow_payload(name="Support triage v2")
    version_two_payload["nodes"][1]["label"] = "Classify urgency"
    created_v2 = client.post(f"/workflows/{workflow_id}/versions", json=version_two_payload)
    assert created_v2.status_code == 201
    assert created_v2.json()["version"] == 2
    assert created_v2.json()["state"] == "draft"

    latest = client.get(f"/workflows/{workflow_id}")
    assert latest.status_code == 200
    assert latest.json()["version"] == 2
    assert latest.json()["state"] == "draft"

    published_v1_lookup = client.get(f"/workflows/{workflow_id}?version=1")
    assert published_v1_lookup.status_code == 200
    assert published_v1_lookup.json()["state"] == "published"

    versions = client.get(f"/workflows/{workflow_id}/versions")
    assert versions.status_code == 200
    assert [(item["version"], item["state"]) for item in versions.json()] == [
        (2, "draft"),
        (1, "published"),
    ]


def test_publish_rejects_invalid_draft_version(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    created = client.post("/workflows", json=_valid_workflow_payload(name="Support triage v1"))
    workflow_id = created.json()["id"]

    invalid_v2_payload = {
        "name": "Broken v2",
        "nodes": [
            {"id": "start", "type": "manual_trigger", "label": "Start"},
            {"id": "prompt", "type": "llm_prompt", "label": "Categorize", "config": {}},
        ],
        "edges": [{"source": "start", "target": "prompt"}],
    }
    created_v2 = client.post(f"/workflows/{workflow_id}/versions", json=invalid_v2_payload)
    assert created_v2.status_code == 201
    assert created_v2.json()["version"] == 2

    publish_invalid = client.post(f"/workflows/{workflow_id}/versions/2/publish")
    assert publish_invalid.status_code == 400
    assert publish_invalid.json()["detail"] == "Cannot publish an invalid workflow version."
