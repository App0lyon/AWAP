from pathlib import Path

from fastapi.testclient import TestClient

from awap.api.app import create_app

AUTH_HEADERS = {"Authorization": "Bearer awap-dev-admin-token"}


def _database_url(tmp_path: Path, name: str = "awap-priority-three.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def test_templates_comments_audit_and_version_diffs(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path)))

    me = client.get("/auth/me", headers=AUTH_HEADERS)
    assert me.status_code == 200
    admin_id = me.json()["id"]

    templates = client.get("/workflow-templates", headers=AUTH_HEADERS)
    assert templates.status_code == 200
    template = next(item for item in templates.json() if item["key"] == "support-triage")

    template_workflow = template["workflow"]
    template_workflow["name"] = "Support Triage Customized"
    template_workflow["release_notes"] = "Initial release"

    created = client.post("/workflows", json=template_workflow, headers=AUTH_HEADERS)
    assert created.status_code == 201
    workflow = created.json()
    workflow_id = workflow["id"]
    assert workflow["owner_id"] == admin_id
    assert workflow["release_notes"] == "Initial release"

    template_workflow["release_notes"] = "Updated routing and notification copy"
    template_workflow["nodes"][1]["label"] = "Classify urgency"
    version_two = client.post(
        f"/workflows/{workflow_id}/versions",
        json=template_workflow,
        headers=AUTH_HEADERS,
    )
    assert version_two.status_code == 201
    assert version_two.json()["version"] == 2

    version_diff = client.get(
        f"/workflows/{workflow_id}/versions/compare",
        params={"from_version": 1, "to_version": 2},
        headers=AUTH_HEADERS,
    )
    assert version_diff.status_code == 200
    diff_body = version_diff.json()
    assert diff_body["changed_nodes"] == ["classify"]
    assert diff_body["to_release_notes"] == "Updated routing and notification copy"

    comment = client.post(
        f"/workflows/{workflow_id}/comments",
        json={
            "workflow_id": workflow_id,
            "workflow_version": 2,
            "body": "This version is ready for QA review.",
        },
        headers=AUTH_HEADERS,
    )
    assert comment.status_code == 201

    comments = client.get(
        f"/workflows/{workflow_id}/comments",
        params={"workflow_version": 2},
        headers=AUTH_HEADERS,
    )
    assert comments.status_code == 200
    assert comments.json()[0]["body"] == "This version is ready for QA review."

    audit_logs = client.get("/audit-logs", params={"workflow_id": workflow_id}, headers=AUTH_HEADERS)
    assert audit_logs.status_code == 200
    actions = [entry["action"] for entry in audit_logs.json()]
    assert "workflow.created" in actions
    assert "workflow.version_created" in actions
    assert "workflow.comment_created" in actions


def test_frontend_includes_priority_three_surfaces(tmp_path: Path) -> None:
    client = TestClient(create_app(database_url=_database_url(tmp_path, "awap-frontend.db")))

    index = client.get("/")
    assert index.status_code == 200
    assert 'id="workflow-template-list"' in index.text
    assert 'id="workflow-release-notes"' in index.text
    assert 'id="version-diff-output"' in index.text
    assert 'id="comment-list"' in index.text
    assert 'id="audit-log-list"' in index.text

    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "renderTemplates" in js.text
    assert "renderComments" in js.text
    assert "renderAdminView" in js.text
