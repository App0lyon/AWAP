"""Repository abstractions for workflow persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    case,
    delete,
    func,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)

from awap.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalTaskDefinition,
    CredentialCreateRequest,
    CredentialDefinition,
    CredentialKind,
    CredentialSecret,
    EvaluationCaseResult,
    EvaluationRunDefinition,
    EvaluationStatus,
    ExecutionPlan,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDefinition,
    KnowledgeChunk,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDefinition,
    PromptTemplateCreateRequest,
    PromptTemplateDefinition,
    RunEventLevel,
    UserCreateRequest,
    UserDefinition,
    UserRole,
    UserWithToken,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunStatus,
    WorkflowRunStep,
    WorkflowRunStepStatus,
    WorkflowSettings,
    WorkflowState,
)
from awap.knowledge import citation_for_chunk, chunk_text, embed_text, rerank_chunks
from awap.security import decrypt_secret_payload, encrypt_secret_payload, generate_bearer_token, hash_token


class WorkflowRepository(Protocol):
    def save(self, workflow: WorkflowDefinition) -> WorkflowDefinition: ...
    def get(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition | None: ...
    def list(self) -> list[WorkflowDefinition]: ...
    def list_versions(self, workflow_id: str) -> list[WorkflowDefinition]: ...
    def get_next_version(self, workflow_id: str) -> int: ...
    def publish(self, workflow_id: str, version: int) -> WorkflowDefinition | None: ...

    def create_run(
        self,
        workflow: WorkflowDefinition,
        plan: ExecutionPlan,
        input_payload: dict,
        *,
        idempotency_key: str | None = None,
        timeout_seconds: int | None = None,
        retry_of_run_id: str | None = None,
        resume_from_step_index: int | None = None,
    ) -> WorkflowRun: ...
    def get_run(self, run_id: str) -> WorkflowRun | None: ...
    def list_runs(self, workflow_id: str) -> list[WorkflowRun]: ...
    def find_run_by_idempotency_key(self, workflow_id: str, key: str) -> WorkflowRun | None: ...
    def count_active_runs(self, workflow_id: str) -> int: ...
    def claim_next_queued_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None: ...
    def mark_run_pause_requested(self, run_id: str) -> WorkflowRun | None: ...
    def mark_run_paused(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None: ...
    def mark_run_waiting_human(self, run_id: str) -> WorkflowRun | None: ...
    def resume_run(self, run_id: str) -> WorkflowRun | None: ...
    def mark_run_cancel_requested(self, run_id: str) -> WorkflowRun | None: ...
    def mark_run_running(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None: ...
    def mark_run_succeeded(self, run_id: str, result_payload: dict) -> WorkflowRun | None: ...
    def mark_run_failed(self, run_id: str, error_message: str) -> WorkflowRun | None: ...
    def mark_run_cancelled(self, run_id: str, error_message: str | None = None) -> WorkflowRun | None: ...
    def update_run_execution_state(self, run_id: str, execution_state: dict[str, Any] | None) -> WorkflowRun | None: ...
    def mark_step_running(self, run_id: str, step_index: int) -> WorkflowRun | None: ...
    def mark_step_waiting_human(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None: ...
    def mark_step_succeeded(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None: ...
    def mark_step_failed(self, run_id: str, step_index: int, error_message: str) -> WorkflowRun | None: ...
    def mark_step_cancelled(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None: ...
    def mark_step_skipped(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None: ...
    def mark_step_copied(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None: ...

    def create_credential(self, request: CredentialCreateRequest, *, created_by: str | None = None) -> CredentialDefinition: ...
    def list_credentials(self) -> list[CredentialDefinition]: ...
    def get_credential(self, credential_id: str) -> CredentialDefinition | None: ...
    def get_credential_secret(self, credential_id: str) -> CredentialSecret | None: ...

    def create_knowledge_base(self, request: KnowledgeBaseCreateRequest, *, created_by: str | None = None) -> KnowledgeBaseDefinition: ...
    def list_knowledge_bases(self) -> list[KnowledgeBaseDefinition]: ...
    def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDefinition | None: ...
    def create_knowledge_document(self, request: KnowledgeDocumentCreateRequest, *, created_by: str | None = None) -> KnowledgeDocumentDefinition: ...
    def list_knowledge_documents(self, knowledge_base_id: str) -> list[KnowledgeDocumentDefinition]: ...
    def search_knowledge(self, knowledge_base_id: str, query: str, *, top_k: int = 5) -> list[KnowledgeChunk]: ...

    def create_approval_task(self, task: ApprovalTaskDefinition) -> ApprovalTaskDefinition: ...
    def list_approval_tasks(self, run_id: str | None = None, decision: ApprovalDecision | None = None) -> list[ApprovalTaskDefinition]: ...
    def get_pending_approval_for_run_step(self, run_id: str, step_index: int) -> ApprovalTaskDefinition | None: ...
    def decide_approval_task(self, approval_task_id: str, request: ApprovalDecisionRequest, *, decided_by: str | None = None) -> ApprovalTaskDefinition | None: ...

    def create_prompt_template(self, request: PromptTemplateCreateRequest, *, created_by: str | None = None) -> PromptTemplateDefinition: ...
    def list_prompt_templates(self, name: str | None = None) -> list[PromptTemplateDefinition]: ...
    def get_prompt_template(self, prompt_template_id: str) -> PromptTemplateDefinition | None: ...

    def create_evaluation_run(self, evaluation: EvaluationRunDefinition) -> EvaluationRunDefinition: ...
    def list_evaluation_runs(self) -> list[EvaluationRunDefinition]: ...
    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunDefinition | None: ...

    def append_run_event(self, *, run_id: str, workflow_id: str, workflow_version: int, event_type: str, message: str, level: RunEventLevel = RunEventLevel.info, provider_key: str | None = None, step_index: int | None = None, payload: dict | None = None) -> WorkflowRunEvent: ...
    def list_run_events(self, run_id: str) -> list[WorkflowRunEvent]: ...

    def create_user(self, request: UserCreateRequest) -> UserWithToken: ...
    def list_users(self) -> list[UserDefinition]: ...
    def get_user(self, user_id: str) -> UserDefinition | None: ...
    def get_user_by_token(self, token: str) -> UserDefinition | None: ...
    def ensure_bootstrap_user(self, username: str, token: str, role: UserRole) -> UserDefinition: ...


class Base(DeclarativeBase):
    pass


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),)

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default=WorkflowState.draft.value)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String, default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    nodes: Mapped[list["WorkflowNodeRecord"]] = relationship(back_populates="workflow", cascade="all, delete-orphan", order_by="WorkflowNodeRecord.position")
    edges: Mapped[list["WorkflowEdgeRecord"]] = relationship(back_populates="workflow", cascade="all, delete-orphan", order_by="WorkflowEdgeRecord.position")


class WorkflowNodeRecord(Base):
    __tablename__ = "workflow_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_record_id: Mapped[int] = mapped_column(ForeignKey("workflows.record_id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    node_key: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow: Mapped[WorkflowRecord] = relationship(back_populates="nodes")


class WorkflowEdgeRecord(Base):
    __tablename__ = "workflow_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_record_id: Mapped[int] = mapped_column(ForeignKey("workflows.record_id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(255))
    target: Mapped[str] = mapped_column(String(255))
    condition_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(255), default="")
    workflow: Mapped[WorkflowRecord] = relationship(back_populates="edges")


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=WorkflowRunStatus.queued.value)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resume_from_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    steps: Mapped[list["WorkflowRunStepRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="WorkflowRunStepRecord.step_index")
    events: Mapped[list["WorkflowRunEventRecord"]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="WorkflowRunEventRecord.timestamp")


class WorkflowRunStepRecord(Base):
    __tablename__ = "workflow_run_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(255))
    node_type: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=WorkflowRunStepStatus.pending.value)
    output_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run: Mapped[WorkflowRunRecord] = relationship(back_populates="steps")


class WorkflowCredentialRecord(Base):
    __tablename__ = "workflow_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(30), default=CredentialKind.generic.value)
    provider_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(String, default="")
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class WorkflowRunEventRecord(Base):
    __tablename__ = "workflow_run_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_version: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(20), default=RunEventLevel.info.value)
    event_type: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    run: Mapped[WorkflowRunRecord] = relationship(back_populates="events")


class WorkflowUserRecord(Base):
    __tablename__ = "workflow_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    role: Mapped[str] = mapped_column(String(30), default=UserRole.viewer.value)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeBaseRecord(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class KnowledgeDocumentRecord(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    citation: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ApprovalTaskRecord(Base):
    __tablename__ = "approval_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_version: Mapped[int] = mapped_column(Integer)
    step_index: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(30), default=ApprovalDecision.pending.value)
    decision_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PromptTemplateRecord(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    template: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    provider_key: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    prompt_template: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    provider_key: Mapped[str] = mapped_column(String(100))
    knowledge_base_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=EvaluationStatus.completed.value)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    average_score: Mapped[float] = mapped_column(JSON, default=0.0)
    results: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SqlAlchemyWorkflowRepository:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def save(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        with self._session_factory() as session:
            record = session.scalar(select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow.id, WorkflowRecord.version == workflow.version))
            if record is None:
                record = WorkflowRecord(
                    workflow_id=workflow.id,
                    version=workflow.version,
                    state=workflow.state.value,
                    name=workflow.name,
                    description=workflow.description,
                    settings=workflow.settings.model_dump(),
                )
                session.add(record)
                session.flush()
            else:
                record.state = workflow.state.value
                record.name = workflow.name
                record.description = workflow.description
                record.settings = workflow.settings.model_dump()
                session.execute(delete(WorkflowNodeRecord).where(WorkflowNodeRecord.workflow_record_id == record.record_id))
                session.execute(delete(WorkflowEdgeRecord).where(WorkflowEdgeRecord.workflow_record_id == record.record_id))
                session.flush()

            record.nodes = [
                WorkflowNodeRecord(
                    workflow_record_id=record.record_id,
                    position=index,
                    node_key=node.id,
                    type=node.type,
                    label=node.label,
                    config=node.config,
                )
                for index, node in enumerate(workflow.nodes)
            ]
            record.edges = [
                WorkflowEdgeRecord(
                    workflow_record_id=record.record_id,
                    position=index,
                    source=edge.source,
                    target=edge.target,
                    condition_value=None if edge.condition_value is None else str(edge.condition_value),
                    is_default=edge.is_default,
                    label=edge.label,
                )
                for index, edge in enumerate(workflow.edges)
            ]
            session.commit()
        return workflow

    def get(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition | None:
        with self._session_factory() as session:
            statement = select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow_id).options(selectinload(WorkflowRecord.nodes), selectinload(WorkflowRecord.edges))
            if version is None:
                statement = statement.order_by(WorkflowRecord.version.desc()).limit(1)
            else:
                statement = statement.where(WorkflowRecord.version == version)
            record = session.scalar(statement)
            return None if record is None else self._to_domain(record)

    def list(self) -> list[WorkflowDefinition]:
        with self._session_factory() as session:
            records = session.scalars(select(WorkflowRecord).options(selectinload(WorkflowRecord.nodes), selectinload(WorkflowRecord.edges)).order_by(WorkflowRecord.name, WorkflowRecord.version.desc())).all()
            latest: dict[str, WorkflowRecord] = {}
            for record in records:
                existing = latest.get(record.workflow_id)
                if existing is None or record.version > existing.version:
                    latest[record.workflow_id] = record
            return [self._to_domain(item) for item in sorted(latest.values(), key=lambda r: r.name.lower())]

    def list_versions(self, workflow_id: str) -> list[WorkflowDefinition]:
        with self._session_factory() as session:
            records = session.scalars(select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow_id).options(selectinload(WorkflowRecord.nodes), selectinload(WorkflowRecord.edges)).order_by(WorkflowRecord.version.desc())).all()
            return [self._to_domain(record) for record in records]

    def get_next_version(self, workflow_id: str) -> int:
        with self._session_factory() as session:
            current_max = session.scalar(select(func.max(WorkflowRecord.version)).where(WorkflowRecord.workflow_id == workflow_id))
            return 1 if current_max is None else current_max + 1

    def publish(self, workflow_id: str, version: int) -> WorkflowDefinition | None:
        with self._session_factory() as session:
            records = session.scalars(select(WorkflowRecord).where(WorkflowRecord.workflow_id == workflow_id).options(selectinload(WorkflowRecord.nodes), selectinload(WorkflowRecord.edges))).all()
            if not records:
                return None
            target: WorkflowRecord | None = None
            for record in records:
                record.state = WorkflowState.published.value if record.version == version else WorkflowState.draft.value
                if record.version == version:
                    target = record
            if target is None:
                return None
            session.commit()
            return self._to_domain(target)

    def create_run(self, workflow: WorkflowDefinition, plan: ExecutionPlan, input_payload: dict, *, idempotency_key: str | None = None, timeout_seconds: int | None = None, retry_of_run_id: str | None = None, resume_from_step_index: int | None = None) -> WorkflowRun:
        with self._session_factory() as session:
            record = WorkflowRunRecord(
                id=str(uuid4()),
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                status=WorkflowRunStatus.queued.value,
                input_payload=input_payload,
                created_at=_utcnow(),
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
                retry_of_run_id=retry_of_run_id,
                resume_from_step_index=resume_from_step_index,
                steps=[
                    WorkflowRunStepRecord(
                        step_index=step.index,
                        node_id=step.node_id,
                        node_type=step.node_type,
                        label=step.label,
                        status=WorkflowRunStepStatus.pending.value,
                    )
                    for step in plan.steps
                ],
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._to_run_domain(record)

    def get_run(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            return None if record is None else self._to_run_domain(record)

    def list_runs(self, workflow_id: str) -> list[WorkflowRun]:
        with self._session_factory() as session:
            records = session.scalars(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_id == workflow_id).options(selectinload(WorkflowRunRecord.steps)).order_by(WorkflowRunRecord.created_at.desc())).all()
            return [self._to_run_domain(record) for record in records]

    def find_run_by_idempotency_key(self, workflow_id: str, key: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = session.scalar(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_id == workflow_id, WorkflowRunRecord.idempotency_key == key).options(selectinload(WorkflowRunRecord.steps)).order_by(WorkflowRunRecord.created_at.desc()))
            return None if record is None else self._to_run_domain(record)

    def count_active_runs(self, workflow_id: str) -> int:
        with self._session_factory() as session:
            return session.scalar(select(func.count()).select_from(WorkflowRunRecord).where(WorkflowRunRecord.workflow_id == workflow_id, WorkflowRunRecord.status.in_([WorkflowRunStatus.queued.value, WorkflowRunStatus.running.value, WorkflowRunStatus.pause_requested.value, WorkflowRunStatus.paused.value, WorkflowRunStatus.waiting_human.value, WorkflowRunStatus.cancelling.value]))) or 0

    def claim_next_queued_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = session.scalar(select(WorkflowRunRecord).where(WorkflowRunRecord.status == WorkflowRunStatus.queued.value).order_by(WorkflowRunRecord.created_at).options(selectinload(WorkflowRunRecord.steps)))
            if record is None:
                return None
            record.status = WorkflowRunStatus.running.value
            record.started_at = record.started_at or _utcnow()
            record.locked_by = worker_id
            record.lease_expires_at = _utcnow() + timedelta(seconds=lease_seconds)
            session.commit()
            return self._to_run_domain(record)

    def mark_run_pause_requested(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            if record.status == WorkflowRunStatus.queued.value:
                record.status = WorkflowRunStatus.paused.value
            elif record.status == WorkflowRunStatus.running.value:
                record.status = WorkflowRunStatus.pause_requested.value
            session.commit()
            return self._to_run_domain(record)

    def mark_run_paused(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            record.status = WorkflowRunStatus.paused.value
            if worker_id is not None:
                record.locked_by = worker_id
            session.commit()
            return self._to_run_domain(record)

    def mark_run_waiting_human(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            record.status = WorkflowRunStatus.waiting_human.value
            record.locked_by = None
            record.lease_expires_at = None
            session.commit()
            return self._to_run_domain(record)

    def resume_run(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            if record.status in {WorkflowRunStatus.paused.value, WorkflowRunStatus.waiting_human.value}:
                record.status = WorkflowRunStatus.queued.value
                record.locked_by = None
                record.lease_expires_at = None
            session.commit()
            return self._to_run_domain(record)

    def mark_run_cancel_requested(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            if record.status in {WorkflowRunStatus.queued.value, WorkflowRunStatus.paused.value, WorkflowRunStatus.waiting_human.value}:
                record.status = WorkflowRunStatus.cancelled.value
                record.finished_at = _utcnow()
            elif record.status in {WorkflowRunStatus.running.value, WorkflowRunStatus.pause_requested.value}:
                record.status = WorkflowRunStatus.cancelling.value
            session.commit()
            return self._to_run_domain(record)

    def mark_run_running(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            record.status = WorkflowRunStatus.running.value
            record.started_at = record.started_at or _utcnow()
            if worker_id is not None:
                record.locked_by = worker_id
            session.commit()
            return self._to_run_domain(record)

    def mark_run_succeeded(self, run_id: str, result_payload: dict) -> WorkflowRun | None:
        return self._update_run_terminal(run_id, WorkflowRunStatus.succeeded, result_payload, None)

    def mark_run_failed(self, run_id: str, error_message: str) -> WorkflowRun | None:
        return self._update_run_terminal(run_id, WorkflowRunStatus.failed, None, error_message)

    def mark_run_cancelled(self, run_id: str, error_message: str | None = None) -> WorkflowRun | None:
        return self._update_run_terminal(run_id, WorkflowRunStatus.cancelled, None, error_message)

    def update_run_execution_state(self, run_id: str, execution_state: dict[str, Any] | None) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            record.execution_state = execution_state
            session.commit()
            return self._to_run_domain(record)

    def mark_step_running(self, run_id: str, step_index: int) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.running, started=True)

    def mark_step_waiting_human(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.waiting_human, error_message=message)

    def mark_step_succeeded(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.succeeded, output_payload=output_payload, finished=True)

    def mark_step_failed(self, run_id: str, step_index: int, error_message: str) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.failed, error_message=error_message, finished=True)

    def mark_step_cancelled(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.cancelled, error_message=message, finished=True)

    def mark_step_skipped(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.skipped, error_message=message, finished=True)

    def mark_step_copied(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None:
        return self._update_step(run_id=run_id, step_index=step_index, status=WorkflowRunStepStatus.copied, output_payload=output_payload, finished=True)

    def create_credential(self, request: CredentialCreateRequest, *, created_by: str | None = None) -> CredentialDefinition:
        with self._session_factory() as session:
            record = WorkflowCredentialRecord(id=str(uuid4()), name=request.name, kind=request.kind.value, provider_key=request.provider_key, description=request.description, secret_ciphertext=encrypt_secret_payload(request.secret_payload), created_at=_utcnow(), created_by=created_by)
            session.add(record)
            session.commit()
            return self._to_credential_domain(record)

    def list_credentials(self) -> list[CredentialDefinition]:
        with self._session_factory() as session:
            return [self._to_credential_domain(record) for record in session.scalars(select(WorkflowCredentialRecord).order_by(WorkflowCredentialRecord.name)).all()]

    def get_credential(self, credential_id: str) -> CredentialDefinition | None:
        with self._session_factory() as session:
            record = session.get(WorkflowCredentialRecord, credential_id)
            return None if record is None else self._to_credential_domain(record)

    def get_credential_secret(self, credential_id: str) -> CredentialSecret | None:
        with self._session_factory() as session:
            record = session.get(WorkflowCredentialRecord, credential_id)
            return None if record is None else self._to_credential_secret(record)

    def create_knowledge_base(self, request: KnowledgeBaseCreateRequest, *, created_by: str | None = None) -> KnowledgeBaseDefinition:
        with self._session_factory() as session:
            record = KnowledgeBaseRecord(id=str(uuid4()), name=request.name, description=request.description, created_at=_utcnow(), created_by=created_by)
            session.add(record)
            session.commit()
            return self._to_knowledge_base_domain(record)

    def list_knowledge_bases(self) -> list[KnowledgeBaseDefinition]:
        with self._session_factory() as session:
            return [self._to_knowledge_base_domain(record) for record in session.scalars(select(KnowledgeBaseRecord).order_by(KnowledgeBaseRecord.name)).all()]

    def get_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDefinition | None:
        with self._session_factory() as session:
            record = session.get(KnowledgeBaseRecord, knowledge_base_id)
            return None if record is None else self._to_knowledge_base_domain(record)

    def create_knowledge_document(self, request: KnowledgeDocumentCreateRequest, *, created_by: str | None = None) -> KnowledgeDocumentDefinition:
        chunks = chunk_text(request.content)
        with self._session_factory() as session:
            if session.get(KnowledgeBaseRecord, request.knowledge_base_id) is None:
                raise KeyError(request.knowledge_base_id)
            record = KnowledgeDocumentRecord(
                id=str(uuid4()),
                knowledge_base_id=request.knowledge_base_id,
                title=request.title,
                content=request.content,
                metadata_payload=request.metadata,
                chunk_count=len(chunks),
                created_at=_utcnow(),
                created_by=created_by,
            )
            session.add(record)
            session.flush()
            for index, chunk in enumerate(chunks):
                session.add(
                    KnowledgeChunkRecord(
                        id=str(uuid4()),
                        knowledge_base_id=request.knowledge_base_id,
                        document_id=record.id,
                        chunk_index=index,
                        content=chunk,
                        metadata_payload=request.metadata,
                        embedding=embed_text(chunk),
                        citation=citation_for_chunk(request.title, index, request.metadata),
                    )
                )
            session.commit()
            return self._to_knowledge_document_domain(record)

    def list_knowledge_documents(self, knowledge_base_id: str) -> list[KnowledgeDocumentDefinition]:
        with self._session_factory() as session:
            records = session.scalars(select(KnowledgeDocumentRecord).where(KnowledgeDocumentRecord.knowledge_base_id == knowledge_base_id).order_by(KnowledgeDocumentRecord.created_at.desc())).all()
            return [self._to_knowledge_document_domain(record) for record in records]

    def search_knowledge(self, knowledge_base_id: str, query: str, *, top_k: int = 5) -> list[KnowledgeChunk]:
        with self._session_factory() as session:
            records = session.scalars(select(KnowledgeChunkRecord).where(KnowledgeChunkRecord.knowledge_base_id == knowledge_base_id)).all()
            chunks = [self._to_knowledge_chunk_domain(record) for record in records]
            embeddings = {record.id: record.embedding for record in records}
            return rerank_chunks(query, chunks, embedding_vectors=embeddings, top_k=top_k)

    def create_approval_task(self, task: ApprovalTaskDefinition) -> ApprovalTaskDefinition:
        with self._session_factory() as session:
            record = ApprovalTaskRecord(
                id=task.id,
                run_id=task.run_id,
                workflow_id=task.workflow_id,
                workflow_version=task.workflow_version,
                step_index=task.step_index,
                node_id=task.node_id,
                title=task.title,
                prompt=task.prompt,
                decision=task.decision.value,
                decision_payload=task.decision_payload,
                created_at=task.created_at,
                decided_at=task.decided_at,
                decided_by=task.decided_by,
            )
            session.add(record)
            session.commit()
            return self._to_approval_domain(record)

    def list_approval_tasks(self, run_id: str | None = None, decision: ApprovalDecision | None = None) -> list[ApprovalTaskDefinition]:
        with self._session_factory() as session:
            statement = select(ApprovalTaskRecord).order_by(ApprovalTaskRecord.created_at.desc())
            if run_id is not None:
                statement = statement.where(ApprovalTaskRecord.run_id == run_id)
            if decision is not None:
                statement = statement.where(ApprovalTaskRecord.decision == decision.value)
            return [self._to_approval_domain(record) for record in session.scalars(statement).all()]

    def get_pending_approval_for_run_step(self, run_id: str, step_index: int) -> ApprovalTaskDefinition | None:
        with self._session_factory() as session:
            record = session.scalar(select(ApprovalTaskRecord).where(ApprovalTaskRecord.run_id == run_id, ApprovalTaskRecord.step_index == step_index, ApprovalTaskRecord.decision == ApprovalDecision.pending.value))
            return None if record is None else self._to_approval_domain(record)

    def decide_approval_task(self, approval_task_id: str, request: ApprovalDecisionRequest, *, decided_by: str | None = None) -> ApprovalTaskDefinition | None:
        with self._session_factory() as session:
            record = session.get(ApprovalTaskRecord, approval_task_id)
            if record is None:
                return None
            record.decision = request.decision.value
            record.decision_payload = {"comment": request.comment, **request.payload}
            record.decided_at = _utcnow()
            record.decided_by = decided_by
            session.commit()
            return self._to_approval_domain(record)

    def create_prompt_template(self, request: PromptTemplateCreateRequest, *, created_by: str | None = None) -> PromptTemplateDefinition:
        with self._session_factory() as session:
            latest_version = session.scalar(select(func.max(PromptTemplateRecord.version)).where(PromptTemplateRecord.name == request.name))
            version = 1 if latest_version is None else latest_version + 1
            record = PromptTemplateRecord(id=str(uuid4()), name=request.name, template=request.template, model=request.model, provider_key=request.provider_key, description=request.description, version=version, created_at=_utcnow(), created_by=created_by)
            session.add(record)
            session.commit()
            return self._to_prompt_template_domain(record)

    def list_prompt_templates(self, name: str | None = None) -> list[PromptTemplateDefinition]:
        with self._session_factory() as session:
            statement = select(PromptTemplateRecord).order_by(PromptTemplateRecord.name, PromptTemplateRecord.version.desc())
            if name is not None:
                statement = statement.where(PromptTemplateRecord.name == name)
            return [self._to_prompt_template_domain(record) for record in session.scalars(statement).all()]

    def get_prompt_template(self, prompt_template_id: str) -> PromptTemplateDefinition | None:
        with self._session_factory() as session:
            record = session.get(PromptTemplateRecord, prompt_template_id)
            return None if record is None else self._to_prompt_template_domain(record)

    def create_evaluation_run(self, evaluation: EvaluationRunDefinition) -> EvaluationRunDefinition:
        with self._session_factory() as session:
            record = EvaluationRunRecord(
                id=evaluation.id,
                name=evaluation.name,
                prompt_template=evaluation.prompt_template,
                model=evaluation.model,
                provider_key=evaluation.provider_key,
                knowledge_base_id=evaluation.knowledge_base_id,
                status=evaluation.status.value,
                total_cases=evaluation.total_cases,
                passed_cases=evaluation.passed_cases,
                average_score=evaluation.average_score,
                results=[result.model_dump() for result in evaluation.results],
                created_at=evaluation.created_at,
                created_by=evaluation.created_by,
            )
            session.add(record)
            session.commit()
            return self._to_evaluation_domain(record)

    def list_evaluation_runs(self) -> list[EvaluationRunDefinition]:
        with self._session_factory() as session:
            return [self._to_evaluation_domain(record) for record in session.scalars(select(EvaluationRunRecord).order_by(EvaluationRunRecord.created_at.desc())).all()]

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunDefinition | None:
        with self._session_factory() as session:
            record = session.get(EvaluationRunRecord, evaluation_run_id)
            return None if record is None else self._to_evaluation_domain(record)

    def append_run_event(self, *, run_id: str, workflow_id: str, workflow_version: int, event_type: str, message: str, level: RunEventLevel = RunEventLevel.info, provider_key: str | None = None, step_index: int | None = None, payload: dict | None = None) -> WorkflowRunEvent:
        with self._session_factory() as session:
            record = WorkflowRunEventRecord(id=str(uuid4()), run_id=run_id, workflow_id=workflow_id, workflow_version=workflow_version, level=level.value, event_type=event_type, message=message, timestamp=_utcnow(), provider_key=provider_key, step_index=step_index, payload=payload or {})
            session.add(record)
            session.commit()
            return self._to_run_event_domain(record)

    def list_run_events(self, run_id: str) -> list[WorkflowRunEvent]:
        with self._session_factory() as session:
            records = session.scalars(select(WorkflowRunEventRecord).where(WorkflowRunEventRecord.run_id == run_id).order_by(WorkflowRunEventRecord.timestamp, _event_ordering())).all()
            return [self._to_run_event_domain(record) for record in records]

    def create_user(self, request: UserCreateRequest) -> UserWithToken:
        token = generate_bearer_token()
        record = WorkflowUserRecord(id=str(uuid4()), username=request.username, role=request.role.value, token_hash=hash_token(token), active=True, created_at=_utcnow())
        with self._session_factory() as session:
            session.add(record)
            session.commit()
        return UserWithToken(id=record.id, username=record.username, role=UserRole(record.role), active=record.active, created_at=record.created_at, token=token)

    def list_users(self) -> list[UserDefinition]:
        with self._session_factory() as session:
            return [self._to_user_domain(record) for record in session.scalars(select(WorkflowUserRecord).order_by(WorkflowUserRecord.username)).all()]

    def get_user(self, user_id: str) -> UserDefinition | None:
        with self._session_factory() as session:
            record = session.get(WorkflowUserRecord, user_id)
            return None if record is None else self._to_user_domain(record)

    def get_user_by_token(self, token: str) -> UserDefinition | None:
        with self._session_factory() as session:
            record = session.scalar(select(WorkflowUserRecord).where(WorkflowUserRecord.token_hash == hash_token(token)))
            if record is None or not record.active:
                return None
            return self._to_user_domain(record)

    def ensure_bootstrap_user(self, username: str, token: str, role: UserRole) -> UserDefinition:
        with self._session_factory() as session:
            record = session.scalar(select(WorkflowUserRecord).where(WorkflowUserRecord.username == username))
            if record is None:
                record = WorkflowUserRecord(id=str(uuid4()), username=username, role=role.value, token_hash=hash_token(token), active=True, created_at=_utcnow())
                session.add(record)
                session.commit()
            return self._to_user_domain(record)

    def _update_step(self, *, run_id: str, step_index: int, status: WorkflowRunStepStatus, output_payload: dict | None = None, error_message: str | None = None, started: bool = False, finished: bool = False) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            step = self._find_run_step(record, step_index)
            if step is None:
                return None
            step.status = status.value
            if output_payload is not None:
                step.output_payload = output_payload
            if error_message is not None:
                step.error_message = error_message
            if started:
                step.started_at = _utcnow()
            if finished:
                step.finished_at = _utcnow()
            session.commit()
            return self._to_run_domain(record)

    def _update_run_terminal(self, run_id: str, status: WorkflowRunStatus, result_payload: dict | None, error_message: str | None) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            record.status = status.value
            record.result_payload = result_payload
            record.error_message = error_message
            record.finished_at = _utcnow()
            record.locked_by = None
            record.lease_expires_at = None
            record.execution_state = None
            session.commit()
            return self._to_run_domain(record)

    def _to_domain(self, record: WorkflowRecord) -> WorkflowDefinition:
        return WorkflowDefinition(
            id=record.workflow_id,
            name=record.name,
            description=record.description,
            version=record.version,
            state=WorkflowState(record.state),
            settings=WorkflowSettings.model_validate(record.settings or {}),
            nodes=[WorkflowNode(id=node.node_key, type=node.type, label=node.label, config=node.config) for node in record.nodes],
            edges=[WorkflowEdge(source=edge.source, target=edge.target, condition_value=edge.condition_value, is_default=edge.is_default, label=edge.label) for edge in record.edges],
        )

    def _to_run_domain(self, record: WorkflowRunRecord) -> WorkflowRun:
        return WorkflowRun(
            id=record.id,
            workflow_id=record.workflow_id,
            workflow_version=record.workflow_version,
            status=WorkflowRunStatus(record.status),
            input_payload=record.input_payload,
            result_payload=record.result_payload,
            error_message=record.error_message,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            idempotency_key=record.idempotency_key,
            timeout_seconds=record.timeout_seconds,
            retry_of_run_id=record.retry_of_run_id,
            resume_from_step_index=record.resume_from_step_index,
            locked_by=record.locked_by,
            lease_expires_at=record.lease_expires_at,
            execution_state=record.execution_state,
            steps=[WorkflowRunStep(index=step.step_index, node_id=step.node_id, node_type=step.node_type, label=step.label, status=WorkflowRunStepStatus(step.status), output_payload=step.output_payload, error_message=step.error_message, started_at=step.started_at, finished_at=step.finished_at) for step in record.steps],
        )

    def _to_credential_domain(self, record: WorkflowCredentialRecord) -> CredentialDefinition:
        return CredentialDefinition(id=record.id, name=record.name, kind=CredentialKind(record.kind), provider_key=record.provider_key, description=record.description, created_at=record.created_at, created_by=record.created_by)

    def _to_credential_secret(self, record: WorkflowCredentialRecord) -> CredentialSecret:
        return CredentialSecret(id=record.id, name=record.name, kind=CredentialKind(record.kind), provider_key=record.provider_key, description=record.description, created_at=record.created_at, created_by=record.created_by, secret_payload=decrypt_secret_payload(record.secret_ciphertext))

    def _to_run_event_domain(self, record: WorkflowRunEventRecord) -> WorkflowRunEvent:
        return WorkflowRunEvent(id=record.id, run_id=record.run_id, workflow_id=record.workflow_id, workflow_version=record.workflow_version, level=RunEventLevel(record.level), event_type=record.event_type, message=record.message, timestamp=record.timestamp, provider_key=record.provider_key, step_index=record.step_index, payload=record.payload)

    def _to_user_domain(self, record: WorkflowUserRecord) -> UserDefinition:
        return UserDefinition(id=record.id, username=record.username, role=UserRole(record.role), active=record.active, created_at=record.created_at)

    def _to_knowledge_base_domain(self, record: KnowledgeBaseRecord) -> KnowledgeBaseDefinition:
        return KnowledgeBaseDefinition(id=record.id, name=record.name, description=record.description, created_at=record.created_at, created_by=record.created_by)

    def _to_knowledge_document_domain(self, record: KnowledgeDocumentRecord) -> KnowledgeDocumentDefinition:
        return KnowledgeDocumentDefinition(id=record.id, knowledge_base_id=record.knowledge_base_id, title=record.title, content=record.content, metadata=record.metadata_payload, chunk_count=record.chunk_count, created_at=record.created_at, created_by=record.created_by)

    def _to_knowledge_chunk_domain(self, record: KnowledgeChunkRecord) -> KnowledgeChunk:
        return KnowledgeChunk(id=record.id, knowledge_base_id=record.knowledge_base_id, document_id=record.document_id, chunk_index=record.chunk_index, content=record.content, metadata=record.metadata_payload, citation=record.citation)

    def _to_approval_domain(self, record: ApprovalTaskRecord) -> ApprovalTaskDefinition:
        return ApprovalTaskDefinition(id=record.id, run_id=record.run_id, workflow_id=record.workflow_id, workflow_version=record.workflow_version, step_index=record.step_index, node_id=record.node_id, title=record.title, prompt=record.prompt, decision=ApprovalDecision(record.decision), decision_payload=record.decision_payload, created_at=record.created_at, decided_at=record.decided_at, decided_by=record.decided_by)

    def _to_prompt_template_domain(self, record: PromptTemplateRecord) -> PromptTemplateDefinition:
        return PromptTemplateDefinition(id=record.id, name=record.name, template=record.template, model=record.model, provider_key=record.provider_key, description=record.description, version=record.version, created_at=record.created_at, created_by=record.created_by)

    def _to_evaluation_domain(self, record: EvaluationRunRecord) -> EvaluationRunDefinition:
        return EvaluationRunDefinition(id=record.id, name=record.name, prompt_template=record.prompt_template, model=record.model, provider_key=record.provider_key, knowledge_base_id=record.knowledge_base_id, status=EvaluationStatus(record.status), total_cases=record.total_cases, passed_cases=record.passed_cases, average_score=float(record.average_score), results=[EvaluationCaseResult.model_validate(result) for result in record.results], created_at=record.created_at, created_by=record.created_by)

    def _get_run_record(self, session: Session, run_id: str) -> WorkflowRunRecord | None:
        return session.scalar(select(WorkflowRunRecord).where(WorkflowRunRecord.id == run_id).options(selectinload(WorkflowRunRecord.steps)))

    def _find_run_step(self, run_record: WorkflowRunRecord, step_index: int) -> WorkflowRunStepRecord | None:
        for step in run_record.steps:
            if step.step_index == step_index:
                return step
        return None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _event_ordering():
    return case(
        (WorkflowRunEventRecord.event_type == "run.queued", 1),
        (WorkflowRunEventRecord.event_type == "run.started", 2),
        (WorkflowRunEventRecord.event_type == "step.started", 3),
        (WorkflowRunEventRecord.event_type == "step.succeeded", 4),
        (WorkflowRunEventRecord.event_type == "step.failed", 5),
        (WorkflowRunEventRecord.event_type == "run.paused", 6),
        (WorkflowRunEventRecord.event_type == "run.resumed", 7),
        (WorkflowRunEventRecord.event_type == "run.cancel_requested", 8),
        (WorkflowRunEventRecord.event_type == "run.cancelled", 9),
        (WorkflowRunEventRecord.event_type == "run.failed", 10),
        (WorkflowRunEventRecord.event_type == "run.succeeded", 11),
        else_=50,
    )
