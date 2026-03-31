import pytest

from awap.catalog import DEFAULT_NODE_CATALOG
from awap.domain import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowState,
    WorkflowValidator,
)


def test_validator_accepts_valid_workflow() -> None:
    workflow = WorkflowDefinition(
        name="Lead qualification",
        nodes=[
            WorkflowNode(id="start", type="manual_trigger", label="Start"),
            WorkflowNode(
                id="prompt",
                type="llm_prompt",
                label="Classify lead",
                config={"prompt_template": "Classify {{lead}}", "model": "gpt-4.1-mini"},
            ),
            WorkflowNode(
                id="notify",
                type="notification",
                label="Notify sales",
                config={"channel": "email", "message": "A lead is ready."},
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="prompt"),
            WorkflowEdge(source="prompt", target="notify"),
        ],
    )

    validator = WorkflowValidator(DEFAULT_NODE_CATALOG)
    result = validator.validate(workflow)

    assert result.valid is True
    assert result.errors == []


def test_validator_rejects_cycle() -> None:
    workflow = WorkflowDefinition(
        name="Loop",
        nodes=[
            WorkflowNode(id="start", type="manual_trigger", label="Start"),
            WorkflowNode(
                id="http",
                type="http_request",
                label="Call API",
                config={"method": "POST", "url": "https://example.com"},
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="http"),
            WorkflowEdge(source="http", target="start"),
        ],
    )

    validator = WorkflowValidator(DEFAULT_NODE_CATALOG)
    result = validator.validate(workflow)

    assert result.valid is False
    assert "Workflow graph must be acyclic." in result.errors


def test_execution_plan_respects_topology() -> None:
    workflow = WorkflowDefinition(
        name="Plan",
        nodes=[
            WorkflowNode(id="start", type="manual_trigger", label="Start"),
            WorkflowNode(
                id="fetch",
                type="http_request",
                label="Fetch",
                config={"method": "GET", "url": "https://example.com"},
            ),
            WorkflowNode(
                id="summarize",
                type="llm_prompt",
                label="Summarize",
                config={"prompt_template": "Summarize {{data}}", "model": "gpt-4.1-mini"},
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="fetch"),
            WorkflowEdge(source="fetch", target="summarize"),
        ],
    )

    validator = WorkflowValidator(DEFAULT_NODE_CATALOG)
    plan = validator.create_execution_plan(workflow)

    assert [step.node_id for step in plan.steps] == ["start", "fetch", "summarize"]
    assert plan.version == 1


def test_workflow_definition_defaults_to_draft_version_one() -> None:
    workflow = WorkflowDefinition(
        name="Default state",
        nodes=[WorkflowNode(id="start", type="manual_trigger", label="Start")],
    )

    assert workflow.version == 1
    assert workflow.state is WorkflowState.draft


def test_workflow_definition_rejects_non_positive_version() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        WorkflowDefinition(
            id="workflow-1",
            name="Invalid version",
            version=0,
            nodes=[WorkflowNode(id="start", type="manual_trigger", label="Start")],
        )
