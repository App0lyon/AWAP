import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path, name: str = "awap-priority-one.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _poll_run(
    client: TestClient,
    run_id: str,
    *,
    timeout_seconds: float = 8.0,
    expected_statuses: set[str] | None = None,
) -> dict:
    deadline = time.time() + timeout_seconds
    latest_response: dict | None = None
    target_statuses = expected_statuses or {"succeeded", "failed", "cancelled"}

    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        latest_response = response.json()
        if latest_response["status"] in target_statuses:
            return latest_response
        time.sleep(0.05)

    raise AssertionError(f"Run {run_id} did not reach {sorted(target_statuses)} in time. Last response: {latest_response}")


def test_knowledge_base_ingestion_search_and_retrieval_node(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-knowledge.db")))

    knowledge_base = client.post(
        "/knowledge-bases",
        json={"name": "Operations Handbook", "description": "Internal operations handbook"},
        headers=AUTH_HEADERS,
    )
    assert knowledge_base.status_code == 201
    knowledge_base_id = knowledge_base.json()["id"]

    document = client.post(
        "/knowledge-documents",
        json={
            "knowledge_base_id": knowledge_base_id,
            "title": "Expense Policy",
            "content": "Expense approvals require manager sign-off. Travel reimbursements must include receipts.",
            "metadata": {"source": "handbook"},
        },
        headers=AUTH_HEADERS,
    )
    assert document.status_code == 201
    assert document.json()["chunk_count"] >= 1

    listed_documents = client.get(
        f"/knowledge-bases/{knowledge_base_id}/documents",
        headers=AUTH_HEADERS,
    )
    assert listed_documents.status_code == 200
    assert listed_documents.json()[0]["title"] == "Expense Policy"

    search = client.get(
        f"/knowledge-bases/{knowledge_base_id}/search",
        params={"query": "manager sign-off", "top_k": 2},
        headers=AUTH_HEADERS,
    )
    assert search.status_code == 200
    search_body = search.json()
    assert search_body["chunks"]
    assert "Expense Policy" in search_body["chunks"][0]["citation"]

    workflow = client.post(
        "/workflows",
        json={
            "name": "Knowledge retrieval workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "search",
                    "type": "knowledge_retrieval",
                    "label": "Search knowledge",
                    "config": {
                        "knowledge_base_id": knowledge_base_id,
                        "query_template": "Find policy guidance about {{input.topic}}",
                        "top_k": 2,
                    },
                },
                {
                    "id": "notify",
                    "type": "notification",
                    "label": "Notify",
                    "config": {"channel": "slack", "message": "Used {{last.citations}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "search"},
                {"source": "search", "target": "notify"},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"topic": "travel reimbursements"}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    completed = _poll_run(client, started.json()["id"])

    retrieval_step = next(step for step in completed["steps"] if step["node_id"] == "search")
    assert retrieval_step["status"] == "succeeded"
    assert retrieval_step["output_payload"]["citations"]
    assert any("receipts" in chunk["content"].lower() for chunk in retrieval_step["output_payload"]["chunks"])


def test_ai_agent_guardrails_and_connector_tools(tmp_path: Path) -> None:
    db_path = tmp_path / "agent-tools.db"
    output_path = tmp_path / "artifacts" / "agent-note.txt"

    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
    connection.execute("INSERT INTO items (name) VALUES (?)", ("invoice-42",))
    connection.commit()
    connection.close()

    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-agent.db")))
    workflow = client.post(
        "/workflows",
        json={
            "name": "Agent workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "agent",
                    "type": "ai_agent",
                    "label": "Investigate",
                    "config": {
                        "provider": "nvidia_build_free_chat",
                        "model": "meta/llama-3.1-8b-instruct",
                        "goal_template": "Investigate {{input.topic}}",
                        "max_iterations": 3,
                        "tool_sequence": [
                            {
                                "type": "sql_query",
                                "config": {
                                    "database_path": str(db_path),
                                    "query": "SELECT name FROM items ORDER BY name",
                                    "query_type": "select",
                                },
                            },
                            {
                                "type": "file_write",
                                "config": {
                                    "path": str(output_path),
                                    "content": "Agent note for {{input.topic}}",
                                },
                            },
                        ],
                        "enable_reflection": True,
                        "blocked_terms": ["unsafe"],
                        "fallback_response": "SAFE SUMMARY",
                        "mock_response": "unsafe draft",
                    },
                },
            ],
            "edges": [{"source": "start", "target": "agent"}],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"topic": "invoice triage"}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    completed = _poll_run(client, started.json()["id"])

    agent_step = next(step for step in completed["steps"] if step["node_id"] == "agent")
    output = agent_step["output_payload"]
    assert agent_step["status"] == "succeeded"
    assert output["response"] == "SAFE SUMMARY"
    assert output["guardrail_fallback_applied"] is True
    assert len(output["iterations"]) == 3
    assert output["iterations"][0]["tool"]["type"] == "sql_query"
    assert output["iterations"][1]["tool"]["type"] == "file_write"
    assert output["reflection"] == "unsafe draft"
    assert output_path.read_text(encoding="utf-8") == "Agent note for invoice triage"


def test_approval_tasks_pause_and_resume_execution(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-approval.db")))

    workflow = client.post(
        "/workflows",
        json={
            "name": "Approval workflow",
            "nodes": [
                {"id": "start", "type": "manual_trigger", "label": "Start"},
                {
                    "id": "approval",
                    "type": "approval",
                    "label": "Manager approval",
                    "config": {"prompt_template": "Approve {{input.subject}}?"},
                },
                {
                    "id": "approved_notify",
                    "type": "notification",
                    "label": "Approved",
                    "config": {"channel": "slack", "message": "Approved {{input.subject}}"},
                },
                {
                    "id": "rejected_notify",
                    "type": "notification",
                    "label": "Rejected",
                    "config": {"channel": "slack", "message": "Rejected {{input.subject}}"},
                },
            ],
            "edges": [
                {"source": "start", "target": "approval"},
                {"source": "approval", "target": "approved_notify", "condition_value": "approved"},
                {"source": "approval", "target": "rejected_notify", "is_default": True},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]

    started = client.post(
        f"/workflows/{workflow_id}/runs",
        json={"input_payload": {"subject": "customer refund"}},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 202
    waiting_run = _poll_run(client, started.json()["id"], expected_statuses={"waiting_human", "succeeded", "failed", "cancelled"})
    assert waiting_run["status"] == "waiting_human"

    approval_tasks = client.get(
        "/approval-tasks",
        params={"run_id": waiting_run["id"]},
        headers=AUTH_HEADERS,
    )
    assert approval_tasks.status_code == 200
    task = approval_tasks.json()[0]
    assert task["decision"] == "pending"
    assert task["prompt"] == "Approve customer refund?"

    decided = client.post(
        f"/approval-tasks/{task['id']}/decision",
        json={"decision": "approved", "comment": "Looks good", "payload": {"reviewed": True}},
        headers=AUTH_HEADERS,
    )
    assert decided.status_code == 200
    assert decided.json()["decision"] == "approved"

    completed = _poll_run(client, waiting_run["id"])
    statuses = {step["node_id"]: step["status"] for step in completed["steps"]}
    assert statuses["approval"] == "succeeded"
    assert statuses["approved_notify"] == "succeeded"
    assert statuses["rejected_notify"] == "skipped"


def test_prompt_templates_and_evaluations_are_persisted(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-evals.db")))

    knowledge_base = client.post(
        "/knowledge-bases",
        json={"name": "Support KB", "description": "Support answers"},
        headers=AUTH_HEADERS,
    )
    assert knowledge_base.status_code == 201
    knowledge_base_id = knowledge_base.json()["id"]

    document = client.post(
        "/knowledge-documents",
        json={
            "knowledge_base_id": knowledge_base_id,
            "title": "Support Hours",
            "content": "Support hours are 9-5 on weekdays. Escalation follows the incident policy.",
        },
        headers=AUTH_HEADERS,
    )
    assert document.status_code == 201

    first_template = client.post(
        "/prompt-templates",
        json={
            "name": "Support answer",
            "template": "Answer the question: {{input.question}}",
            "model": "meta/llama-3.1-8b-instruct",
            "provider_key": "nvidia_build_free_chat",
        },
        headers=AUTH_HEADERS,
    )
    assert first_template.status_code == 201
    assert first_template.json()["version"] == 1

    second_template = client.post(
        "/prompt-templates",
        json={
            "name": "Support answer",
            "template": "Answer carefully: {{input.question}}",
            "model": "meta/llama-3.1-8b-instruct",
            "provider_key": "nvidia_build_free_chat",
            "description": "Second revision",
        },
        headers=AUTH_HEADERS,
    )
    assert second_template.status_code == 201
    assert second_template.json()["version"] == 2

    listed_templates = client.get(
        "/prompt-templates",
        params={"name": "Support answer"},
        headers=AUTH_HEADERS,
    )
    assert listed_templates.status_code == 200
    assert [item["version"] for item in listed_templates.json()] == [2, 1]

    fetched_template = client.get(
        f"/prompt-templates/{second_template.json()['id']}",
        headers=AUTH_HEADERS,
    )
    assert fetched_template.status_code == 200
    assert fetched_template.json()["description"] == "Second revision"

    evaluation = client.post(
        "/evaluations",
        json={
            "name": "Support quality check",
            "prompt_template": fetched_template.json()["template"],
            "model": "meta/llama-3.1-8b-instruct",
            "provider_key": "nvidia_build_free_chat",
            "knowledge_base_id": knowledge_base_id,
            "mock_response": "Support hours are 9-5 and escalation follows policy.",
            "test_cases": [
                {"input_payload": {"question": "When are support hours?"}, "expected_contains": ["9-5"]},
                {"input_payload": {"question": "What is the escalation process?"}, "expected_contains": ["policy"], "blocked_terms": ["refund"]},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert evaluation.status_code == 201
    evaluation_body = evaluation.json()
    assert evaluation_body["status"] == "completed"
    assert evaluation_body["total_cases"] == 2
    assert evaluation_body["passed_cases"] == 2
    assert evaluation_body["average_score"] == 1.0

    listed_evaluations = client.get("/evaluations", headers=AUTH_HEADERS)
    assert listed_evaluations.status_code == 200
    assert listed_evaluations.json()[0]["id"] == evaluation_body["id"]

    fetched_evaluation = client.get(
        f"/evaluations/{evaluation_body['id']}",
        headers=AUTH_HEADERS,
    )
    assert fetched_evaluation.status_code == 200
    assert [result["passed"] for result in fetched_evaluation.json()["results"]] == [True, True]
