"""Core domain models for workflow authoring, AI capabilities, and execution."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeCategory(StrEnum):
    trigger = "trigger"
    ai = "ai"
    logic = "logic"
    action = "action"
    flow = "flow"


class WorkflowState(StrEnum):
    draft = "draft"
    published = "published"


class WorkflowRunStatus(StrEnum):
    queued = "queued"
    running = "running"
    pause_requested = "pause_requested"
    paused = "paused"
    waiting_human = "waiting_human"
    cancelling = "cancelling"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class WorkflowRunStepStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting_human = "waiting_human"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"
    copied = "copied"


class ProviderKind(StrEnum):
    llm = "llm"
    tool = "tool"
    observability = "observability"


class CredentialKind(StrEnum):
    generic = "generic"
    api_key = "api_key"
    bearer_token = "bearer_token"
    header_map = "header_map"


class RunEventLevel(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"


class UserRole(StrEnum):
    admin = "admin"
    editor = "editor"
    operator = "operator"
    viewer = "viewer"


class ApprovalDecision(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EvaluationStatus(StrEnum):
    completed = "completed"
    failed = "failed"


class NodeTypeDefinition(BaseModel):
    key: str
    category: NodeCategory
    display_name: str
    description: str
    required_config_fields: list[str] = Field(default_factory=list)
    max_outgoing_edges: int | None = None


class WorkflowNode(BaseModel):
    id: str
    type: str
    label: str
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    source: str
    target: str
    condition_value: str | int | float | bool | None = None
    is_default: bool = False
    label: str = ""


class WorkflowSettings(BaseModel):
    max_concurrent_runs: int = Field(default=3, ge=1, le=100)
    run_timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)


class WorkflowContent(BaseModel):
    name: str
    description: str = ""
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge] = Field(default_factory=list)
    settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def ensure_nodes_are_unique(self) -> WorkflowContent:
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Workflow nodes must have unique ids.")
        return self


class WorkflowDraftPayload(WorkflowContent):
    pass


class WorkflowDefinition(WorkflowContent):
    id: str = Field(default_factory=lambda: str(uuid4()))
    version: int = 1
    state: WorkflowState = WorkflowState.draft

    @model_validator(mode="after")
    def ensure_version_is_positive(self) -> WorkflowDefinition:
        if self.version < 1:
            raise ValueError("Workflow version must be greater than zero.")
        return self


class WorkflowValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)


class ProviderDefinition(BaseModel):
    key: str
    kind: ProviderKind
    display_name: str
    description: str
    supported_node_types: list[str] = Field(default_factory=list)


class CredentialCreateRequest(BaseModel):
    name: str
    kind: CredentialKind = CredentialKind.generic
    provider_key: str | None = None
    description: str = ""
    secret_payload: dict[str, Any] = Field(default_factory=dict)


class CredentialDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    kind: CredentialKind = CredentialKind.generic
    provider_key: str | None = None
    description: str = ""
    created_at: datetime
    created_by: str | None = None


class CredentialSecret(CredentialDefinition):
    secret_payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionStep(BaseModel):
    index: int
    node_id: str
    node_type: str
    label: str


class ExecutionPlan(BaseModel):
    workflow_id: str
    version: int
    steps: list[ExecutionStep]


class WorkflowRunStep(BaseModel):
    index: int
    node_id: str
    node_type: str
    label: str
    status: WorkflowRunStepStatus = WorkflowRunStepStatus.pending
    output_payload: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    workflow_version: int
    status: WorkflowRunStatus = WorkflowRunStatus.queued
    input_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[WorkflowRunStep] = Field(default_factory=list)
    idempotency_key: str | None = None
    timeout_seconds: int | None = None
    retry_of_run_id: str | None = None
    resume_from_step_index: int | None = None
    locked_by: str | None = None
    lease_expires_at: datetime | None = None
    execution_state: dict[str, Any] | None = None


class WorkflowRunEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    workflow_id: str
    workflow_version: int
    level: RunEventLevel = RunEventLevel.info
    event_type: str
    message: str
    timestamp: datetime
    provider_key: str | None = None
    step_index: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class UserDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    username: str
    role: UserRole = UserRole.viewer
    active: bool = True
    created_at: datetime


class UserCreateRequest(BaseModel):
    username: str
    role: UserRole = UserRole.viewer


class UserWithToken(UserDefinition):
    token: str


class KnowledgeBaseCreateRequest(BaseModel):
    name: str
    description: str = ""


class KnowledgeBaseDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    created_at: datetime
    created_by: str | None = None


class KnowledgeDocumentCreateRequest(BaseModel):
    knowledge_base_id: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    knowledge_base_id: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunk_count: int = 0
    created_at: datetime
    created_by: str | None = None


class KnowledgeChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    knowledge_base_id: str
    document_id: str
    chunk_index: int
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    citation: str | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeSearchResult(BaseModel):
    knowledge_base_id: str
    query: str
    chunks: list[KnowledgeChunk] = Field(default_factory=list)


class ApprovalTaskDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    workflow_id: str
    workflow_version: int
    step_index: int
    node_id: str
    title: str
    prompt: str
    decision: ApprovalDecision = ApprovalDecision.pending
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    comment: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_not_pending(self) -> ApprovalDecisionRequest:
        if self.decision is ApprovalDecision.pending:
            raise ValueError("Approval decisions cannot remain pending.")
        return self


class EvaluationCase(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    expected_contains: list[str] = Field(default_factory=list)
    blocked_terms: list[str] = Field(default_factory=list)


class EvaluationRunCreateRequest(BaseModel):
    name: str
    prompt_template: str
    model: str
    provider_key: str = "nvidia_build_free_chat"
    knowledge_base_id: str | None = None
    mock_response: str | None = None
    test_cases: list[EvaluationCase] = Field(default_factory=list)


class EvaluationCaseResult(BaseModel):
    index: int
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output: str
    passed: bool
    score: float
    reasons: list[str] = Field(default_factory=list)


class EvaluationRunDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    prompt_template: str
    model: str
    provider_key: str
    knowledge_base_id: str | None = None
    status: EvaluationStatus = EvaluationStatus.completed
    total_cases: int = 0
    passed_cases: int = 0
    average_score: float = 0.0
    results: list[EvaluationCaseResult] = Field(default_factory=list)
    created_at: datetime
    created_by: str | None = None


class PromptTemplateCreateRequest(BaseModel):
    name: str
    template: str
    model: str
    provider_key: str = "nvidia_build_free_chat"
    description: str = ""


class PromptTemplateDefinition(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    template: str
    model: str
    provider_key: str
    description: str = ""
    version: int = 1
    created_at: datetime
    created_by: str | None = None


class WorkflowValidator:
    """Validates workflow graph structure and node configuration."""

    def __init__(self, node_catalog: dict[str, NodeTypeDefinition]) -> None:
        self._node_catalog = node_catalog

    def validate(self, workflow: WorkflowDefinition) -> WorkflowValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        node_map = {node.id: node for node in workflow.nodes}
        incoming: dict[str, int] = {node.id: 0 for node in workflow.nodes}
        outgoing: dict[str, int] = {node.id: 0 for node in workflow.nodes}

        if not workflow.nodes:
            errors.append("Workflow must include at least one node.")
            return WorkflowValidationResult(valid=False, errors=errors, warnings=warnings)

        for node in workflow.nodes:
            node_type = self._node_catalog.get(node.type)
            if node_type is None:
                errors.append(f"Node '{node.id}' uses unknown type '{node.type}'.")
                continue

            missing_fields = [
                field for field in node_type.required_config_fields if field not in node.config
            ]
            if missing_fields:
                errors.append(
                    f"Node '{node.id}' is missing required config fields: {', '.join(missing_fields)}."
                )

        default_edges_by_source: dict[str, int] = {}
        for edge in workflow.edges:
            if edge.source not in node_map:
                errors.append(f"Edge source '{edge.source}' does not exist.")
                continue
            if edge.target not in node_map:
                errors.append(f"Edge target '{edge.target}' does not exist.")
                continue

            outgoing[edge.source] += 1
            incoming[edge.target] += 1
            if edge.is_default:
                default_edges_by_source[edge.source] = default_edges_by_source.get(edge.source, 0) + 1

        for node_id, count in default_edges_by_source.items():
            if count > 1:
                errors.append(f"Node '{node_id}' cannot define more than one default edge.")

        trigger_nodes = [
            node
            for node in workflow.nodes
            if self._node_catalog.get(node.type) is not None
            and self._node_catalog[node.type].category is NodeCategory.trigger
        ]

        if not trigger_nodes:
            errors.append("Workflow must include at least one trigger node.")

        for node in workflow.nodes:
            definition = self._node_catalog.get(node.type)
            if definition is None:
                continue
            if definition.category is NodeCategory.trigger and incoming[node.id] > 0:
                errors.append(f"Trigger node '{node.id}' cannot have incoming edges.")
            if (
                definition.max_outgoing_edges is not None
                and outgoing[node.id] > definition.max_outgoing_edges
            ):
                errors.append(
                    f"Node '{node.id}' exceeds max outgoing edges ({definition.max_outgoing_edges})."
                )
            if definition.category is not NodeCategory.trigger and incoming[node.id] == 0:
                warnings.append(f"Node '{node.id}' is unreachable from any predecessor.")
            if node.type == "join" and incoming[node.id] < 2:
                warnings.append(f"Join node '{node.id}' should normally have at least two inputs.")
            if node.type in {"sub_workflow", "for_each"} and node.config.get("workflow_id") == workflow.id:
                warnings.append(f"Node '{node.id}' references the current workflow id as a sub-workflow.")

        if self._has_cycle(workflow):
            errors.append("Workflow graph must be acyclic.")

        return WorkflowValidationResult(valid=not errors, errors=errors, warnings=warnings)

    def create_execution_plan(self, workflow: WorkflowDefinition) -> ExecutionPlan:
        validation = self.validate(workflow)
        if not validation.valid:
            raise ValueError("Cannot create execution plan for invalid workflow.")

        node_map = {node.id: node for node in workflow.nodes}
        indegree = {node.id: 0 for node in workflow.nodes}
        adjacency: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}

        for edge in workflow.edges:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
        ordered_ids: list[str] = []

        while queue:
            current = queue.popleft()
            ordered_ids.append(current)
            for neighbor in sorted(adjacency[current]):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        steps = [
            ExecutionStep(
                index=index,
                node_id=node.id,
                node_type=node.type,
                label=node.label,
            )
            for index, node in enumerate((node_map[node_id] for node_id in ordered_ids), start=1)
        ]
        return ExecutionPlan(workflow_id=workflow.id, version=workflow.version, steps=steps)

    def _has_cycle(self, workflow: WorkflowDefinition) -> bool:
        indegree = {node.id: 0 for node in workflow.nodes}
        adjacency: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}

        for edge in workflow.edges:
            if edge.source not in adjacency or edge.target not in indegree:
                continue
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0

        while queue:
            current = queue.popleft()
            visited += 1
            for neighbor in adjacency[current]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        return visited != len(workflow.nodes)
