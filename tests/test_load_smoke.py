import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'awap-load-smoke.db'}"


def test_small_run_queue_load_smoke(tmp_path: Path) -> None:
    with TestClient(create_app(database_url=_database_url(tmp_path), worker_count=4)) as client:
        workflow = client.post(
            "/workflows",
            json={
                "name": "Load smoke workflow",
                "settings": {"max_concurrent_runs": 20},
                "nodes": [
                    {"id": "start", "type": "manual_trigger", "label": "Start"},
                    {
                        "id": "notify",
                        "type": "notification",
                        "label": "Notify",
                        "config": {"channel": "log", "message": "done"},
                    },
                ],
                "edges": [{"source": "start", "target": "notify"}],
            },
            headers=AUTH_HEADERS,
        )
        assert workflow.status_code == 201
        workflow_id = workflow.json()["id"]

        run_ids: list[str] = []
        started_at = time.monotonic()
        for index in range(12):
            run = client.post(
                f"/workflows/{workflow_id}/runs",
                json={"input_payload": {"index": index}},
                headers=AUTH_HEADERS,
            )
            assert run.status_code == 202
            run_ids.append(run.json()["id"])

        deadline = started_at + 8.0
        completed: set[str] = set()
        while time.monotonic() < deadline and len(completed) < len(run_ids):
            for run_id in run_ids:
                if run_id in completed:
                    continue
                response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
                assert response.status_code == 200
                if response.json()["status"] == "succeeded":
                    completed.add(run_id)
            time.sleep(0.05)

        assert completed == set(run_ids)
