"""Repository abstractions for workflow persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
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
    CredentialCreateRequest,
    CredentialDefinition,
    CredentialKind,
    CredentialSecret,
    ExecutionPlan,
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
from awap.security import decrypt_secret_payload, encrypt_secret_payload, generate_bearer_token, hash_token


class WorkflowRepository(Protocol):
    def save(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        ...

    def get(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition | None:
        ...

    def list(self) -> list[WorkflowDefinition]:
        ...

    def list_versions(self, workflow_id: str) -> list[WorkflowDefinition]:
        ...

    def get_next_version(self, workflow_id: str) -> int:
        ...

    def publish(self, workflow_id: str, version: int) -> WorkflowDefinition | None:
        ...

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
    ) -> WorkflowRun:
        ...

    def get_run(self, run_id: str) -> WorkflowRun | None:
        ...

    def list_runs(self, workflow_id: str) -> list[WorkflowRun]:
        ...

    def find_run_by_idempotency_key(self, workflow_id: str, key: str) -> WorkflowRun | None:
        ...

    def count_active_runs(self, workflow_id: str) -> int:
        ...

    def claim_next_queued_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None:
        ...

    def mark_run_pause_requested(self, run_id: str) -> WorkflowRun | None:
        ...

    def mark_run_paused(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None:
        ...

    def resume_run(self, run_id: str) -> WorkflowRun | None:
        ...

    def mark_run_cancel_requested(self, run_id: str) -> WorkflowRun | None:
        ...

    def mark_run_running(self, run_id: str, worker_id: str | None = None) -> WorkflowRun | None:
        ...

    def mark_run_succeeded(self, run_id: str, result_payload: dict) -> WorkflowRun | None:
        ...

    def mark_run_failed(self, run_id: str, error_message: str) -> WorkflowRun | None:
        ...

    def mark_run_cancelled(self, run_id: str, error_message: str | None = None) -> WorkflowRun | None:
        ...

    def mark_step_running(self, run_id: str, step_index: int) -> WorkflowRun | None:
        ...

    def mark_step_succeeded(
        self,
        run_id: str,
        step_index: int,
        output_payload: dict,
    ) -> WorkflowRun | None:
        ...

    def mark_step_failed(
        self,
        run_id: str,
        step_index: int,
        error_message: str,
    ) -> WorkflowRun | None:
        ...

    def mark_step_cancelled(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        ...

    def mark_step_skipped(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        ...

    def mark_step_copied(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None:
        ...

    def create_credential(
        self,
        request: CredentialCreateRequest,
        *,
        created_by: str | None = None,
    ) -> CredentialDefinition:
        ...

    def list_credentials(self) -> list[CredentialDefinition]:
        ...

    def get_credential(self, credential_id: str) -> CredentialDefinition | None:
        ...

    def get_credential_secret(self, credential_id: str) -> CredentialSecret | None:
        ...

    def append_run_event(
        self,
        *,
        run_id: str,
        workflow_id: str,
        workflow_version: int,
        event_type: str,
        message: str,
        level: RunEventLevel = RunEventLevel.info,
        provider_key: str | None = None,
        step_index: int | None = None,
        payload: dict | None = None,
    ) -> WorkflowRunEvent:
        ...

    def list_run_events(self, run_id: str) -> list[WorkflowRunEvent]:
        ...

    def create_user(self, request: UserCreateRequest) -> UserWithToken:
        ...

    def list_users(self) -> list[UserDefinition]:
        ...

    def get_user(self, user_id: str) -> UserDefinition | None:
        ...

    def get_user_by_token(self, token: str) -> UserDefinition | None:
        ...

    def ensure_bootstrap_user(self, username: str, token: str, role: UserRole) -> UserDefinition:
        ...


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
    nodes: Mapped[list[WorkflowNodeRecord]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowNodeRecord.position",
    )
    edges: Mapped[list[WorkflowEdgeRecord]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowEdgeRecord.position",
    )


class WorkflowNodeRecord(Base):
    __tablename__ = "workflow_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_record_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.record_id", ondelete="CASCADE"),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    node_key: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow: Mapped[WorkflowRecord] = relationship(back_populates="nodes")


class WorkflowEdgeRecord(Base):
    __tablename__ = "workflow_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_record_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.record_id", ondelete="CASCADE"),
        index=True,
    )
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
    steps: Mapped[list[WorkflowRunStepRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowRunStepRecord.step_index",
    )
    events: Mapped[list[WorkflowRunEventRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="WorkflowRunEventRecord.timestamp",
    )


class WorkflowRunStepRecord(Base):
    __tablename__ = "workflow_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        index=True,
    )
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
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        index=True,
    )
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


class SqlAlchemyWorkflowRepository:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def save(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        with self._session_factory() as session:
            record = session.scalar(
                select(WorkflowRecord).where(
                    WorkflowRecord.workflow_id == workflow.id,
                    WorkflowRecord.version == workflow.version,
                )
            )
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
                session.execute(
                    delete(WorkflowNodeRecord).where(
                        WorkflowNodeRecord.workflow_record_id == record.record_id
                    )
                )
                session.execute(
                    delete(WorkflowEdgeRecord).where(
                        WorkflowEdgeRecord.workflow_record_id == record.record_id
                    )
                )
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
                    condition_value=None
                    if edge.condition_value is None
                    else str(edge.condition_value),
                    is_default=edge.is_default,
                    label=edge.label,
                )
                for index, edge in enumerate(workflow.edges)
            ]
            session.commit()
        return workflow

    def get(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition | None:
        with self._session_factory() as session:
            statement = (
                select(WorkflowRecord)
                .where(WorkflowRecord.workflow_id == workflow_id)
                .options(
                    selectinload(WorkflowRecord.nodes),
                    selectinload(WorkflowRecord.edges),
                )
            )
            if version is None:
                statement = statement.order_by(WorkflowRecord.version.desc()).limit(1)
            else:
                statement = statement.where(WorkflowRecord.version == version)
            record = session.scalar(statement)
            return None if record is None else self._to_domain(record)

    def list(self) -> list[WorkflowDefinition]:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowRecord)
                .options(
                    selectinload(WorkflowRecord.nodes),
                    selectinload(WorkflowRecord.edges),
                )
                .order_by(WorkflowRecord.name, WorkflowRecord.version.desc())
            ).all()
            latest: dict[str, WorkflowRecord] = {}
            for record in records:
                existing = latest.get(record.workflow_id)
                if existing is None or record.version > existing.version:
                    latest[record.workflow_id] = record
            return [self._to_domain(item) for item in sorted(latest.values(), key=lambda r: r.name.lower())]

    def list_versions(self, workflow_id: str) -> list[WorkflowDefinition]:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowRecord)
                .where(WorkflowRecord.workflow_id == workflow_id)
                .options(
                    selectinload(WorkflowRecord.nodes),
                    selectinload(WorkflowRecord.edges),
                )
                .order_by(WorkflowRecord.version.desc())
            ).all()
            return [self._to_domain(record) for record in records]

    def get_next_version(self, workflow_id: str) -> int:
        with self._session_factory() as session:
            current_max = session.scalar(
                select(func.max(WorkflowRecord.version)).where(
                    WorkflowRecord.workflow_id == workflow_id
                )
            )
            return 1 if current_max is None else current_max + 1

    def publish(self, workflow_id: str, version: int) -> WorkflowDefinition | None:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowRecord)
                .where(WorkflowRecord.workflow_id == workflow_id)
                .options(
                    selectinload(WorkflowRecord.nodes),
                    selectinload(WorkflowRecord.edges),
                )
            ).all()
            if not records:
                return None

            target: WorkflowRecord | None = None
            for record in records:
                record.state = (
                    WorkflowState.published.value
                    if record.version == version
                    else WorkflowState.draft.value
                )
                if record.version == version:
                    target = record

            if target is None:
                return None

            session.commit()
            return self._to_domain(target)

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
    ) -> WorkflowRun:
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
            records = session.scalars(
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.workflow_id == workflow_id)
                .options(selectinload(WorkflowRunRecord.steps))
                .order_by(WorkflowRunRecord.created_at.desc())
            ).all()
            return [self._to_run_domain(record) for record in records]

    def find_run_by_idempotency_key(self, workflow_id: str, key: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.workflow_id == workflow_id,
                    WorkflowRunRecord.idempotency_key == key,
                )
                .options(selectinload(WorkflowRunRecord.steps))
                .order_by(WorkflowRunRecord.created_at.desc())
            )
            return None if record is None else self._to_run_domain(record)

    def count_active_runs(self, workflow_id: str) -> int:
        with self._session_factory() as session:
            return session.scalar(
                select(func.count())
                .select_from(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.workflow_id == workflow_id,
                    WorkflowRunRecord.status.in_(
                        [
                            WorkflowRunStatus.queued.value,
                            WorkflowRunStatus.running.value,
                            WorkflowRunStatus.pause_requested.value,
                            WorkflowRunStatus.paused.value,
                            WorkflowRunStatus.cancelling.value,
                        ]
                    ),
                )
            ) or 0

    def claim_next_queued_run(self, worker_id: str, lease_seconds: int) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.status == WorkflowRunStatus.queued.value)
                .order_by(WorkflowRunRecord.created_at)
                .options(selectinload(WorkflowRunRecord.steps))
            )
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

    def resume_run(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            if record.status == WorkflowRunStatus.paused.value:
                if record.locked_by:
                    record.status = WorkflowRunStatus.running.value
                else:
                    record.status = WorkflowRunStatus.queued.value
            session.commit()
            return self._to_run_domain(record)

    def mark_run_cancel_requested(self, run_id: str) -> WorkflowRun | None:
        with self._session_factory() as session:
            record = self._get_run_record(session, run_id)
            if record is None:
                return None
            if record.status in {
                WorkflowRunStatus.queued.value,
                WorkflowRunStatus.paused.value,
            }:
                record.status = WorkflowRunStatus.cancelled.value
                record.finished_at = _utcnow()
            elif record.status in {
                WorkflowRunStatus.running.value,
                WorkflowRunStatus.pause_requested.value,
            }:
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

    def mark_step_running(self, run_id: str, step_index: int) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.running,
            started=True,
        )

    def mark_step_succeeded(
        self,
        run_id: str,
        step_index: int,
        output_payload: dict,
    ) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.succeeded,
            output_payload=output_payload,
            finished=True,
        )

    def mark_step_failed(
        self,
        run_id: str,
        step_index: int,
        error_message: str,
    ) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.failed,
            error_message=error_message,
            finished=True,
        )

    def mark_step_cancelled(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.cancelled,
            error_message=message,
            finished=True,
        )

    def mark_step_skipped(self, run_id: str, step_index: int, message: str) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.skipped,
            error_message=message,
            finished=True,
        )

    def mark_step_copied(self, run_id: str, step_index: int, output_payload: dict) -> WorkflowRun | None:
        return self._update_step(
            run_id=run_id,
            step_index=step_index,
            status=WorkflowRunStepStatus.copied,
            output_payload=output_payload,
            finished=True,
        )

    def create_credential(
        self,
        request: CredentialCreateRequest,
        *,
        created_by: str | None = None,
    ) -> CredentialDefinition:
        with self._session_factory() as session:
            record = WorkflowCredentialRecord(
                id=str(uuid4()),
                name=request.name,
                kind=request.kind.value,
                provider_key=request.provider_key,
                description=request.description,
                secret_ciphertext=encrypt_secret_payload(request.secret_payload),
                created_at=_utcnow(),
                created_by=created_by,
            )
            session.add(record)
            session.commit()
            return self._to_credential_domain(record)

    def list_credentials(self) -> list[CredentialDefinition]:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowCredentialRecord).order_by(WorkflowCredentialRecord.name)
            ).all()
            return [self._to_credential_domain(record) for record in records]

    def get_credential(self, credential_id: str) -> CredentialDefinition | None:
        with self._session_factory() as session:
            record = session.get(WorkflowCredentialRecord, credential_id)
            return None if record is None else self._to_credential_domain(record)

    def get_credential_secret(self, credential_id: str) -> CredentialSecret | None:
        with self._session_factory() as session:
            record = session.get(WorkflowCredentialRecord, credential_id)
            return None if record is None else self._to_credential_secret(record)

    def append_run_event(
        self,
        *,
        run_id: str,
        workflow_id: str,
        workflow_version: int,
        event_type: str,
        message: str,
        level: RunEventLevel = RunEventLevel.info,
        provider_key: str | None = None,
        step_index: int | None = None,
        payload: dict | None = None,
    ) -> WorkflowRunEvent:
        with self._session_factory() as session:
            record = WorkflowRunEventRecord(
                id=str(uuid4()),
                run_id=run_id,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                level=level.value,
                event_type=event_type,
                message=message,
                timestamp=_utcnow(),
                provider_key=provider_key,
                step_index=step_index,
                payload=payload or {},
            )
            session.add(record)
            session.commit()
            return self._to_run_event_domain(record)

    def list_run_events(self, run_id: str) -> list[WorkflowRunEvent]:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowRunEventRecord)
                .where(WorkflowRunEventRecord.run_id == run_id)
                .order_by(WorkflowRunEventRecord.timestamp, _event_ordering())
            ).all()
            return [self._to_run_event_domain(record) for record in records]

    def create_user(self, request: UserCreateRequest) -> UserWithToken:
        token = generate_bearer_token()
        record = WorkflowUserRecord(
            id=str(uuid4()),
            username=request.username,
            role=request.role.value,
            token_hash=hash_token(token),
            active=True,
            created_at=_utcnow(),
        )
        with self._session_factory() as session:
            session.add(record)
            session.commit()
        return UserWithToken(
            id=record.id,
            username=record.username,
            role=UserRole(record.role),
            active=record.active,
            created_at=record.created_at,
            token=token,
        )

    def list_users(self) -> list[UserDefinition]:
        with self._session_factory() as session:
            records = session.scalars(
                select(WorkflowUserRecord).order_by(WorkflowUserRecord.username)
            ).all()
            return [self._to_user_domain(record) for record in records]

    def get_user(self, user_id: str) -> UserDefinition | None:
        with self._session_factory() as session:
            record = session.get(WorkflowUserRecord, user_id)
            return None if record is None else self._to_user_domain(record)

    def get_user_by_token(self, token: str) -> UserDefinition | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(WorkflowUserRecord).where(WorkflowUserRecord.token_hash == hash_token(token))
            )
            if record is None or not record.active:
                return None
            return self._to_user_domain(record)

    def ensure_bootstrap_user(self, username: str, token: str, role: UserRole) -> UserDefinition:
        with self._session_factory() as session:
            record = session.scalar(
                select(WorkflowUserRecord).where(WorkflowUserRecord.username == username)
            )
            if record is None:
                record = WorkflowUserRecord(
                    id=str(uuid4()),
                    username=username,
                    role=role.value,
                    token_hash=hash_token(token),
                    active=True,
                    created_at=_utcnow(),
                )
                session.add(record)
                session.commit()
            return self._to_user_domain(record)

    def _update_step(
        self,
        *,
        run_id: str,
        step_index: int,
        status: WorkflowRunStepStatus,
        output_payload: dict | None = None,
        error_message: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> WorkflowRun | None:
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

    def _update_run_terminal(
        self,
        run_id: str,
        status: WorkflowRunStatus,
        result_payload: dict | None,
        error_message: str | None,
    ) -> WorkflowRun | None:
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
            nodes=[
                WorkflowNode(
                    id=node.node_key,
                    type=node.type,
                    label=node.label,
                    config=node.config,
                )
                for node in record.nodes
            ],
            edges=[
                WorkflowEdge(
                    source=edge.source,
                    target=edge.target,
                    condition_value=edge.condition_value,
                    is_default=edge.is_default,
                    label=edge.label,
                )
                for edge in record.edges
            ],
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
            steps=[
                WorkflowRunStep(
                    index=step.step_index,
                    node_id=step.node_id,
                    node_type=step.node_type,
                    label=step.label,
                    status=WorkflowRunStepStatus(step.status),
                    output_payload=step.output_payload,
                    error_message=step.error_message,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                )
                for step in record.steps
            ],
        )

    def _to_credential_domain(self, record: WorkflowCredentialRecord) -> CredentialDefinition:
        return CredentialDefinition(
            id=record.id,
            name=record.name,
            kind=CredentialKind(record.kind),
            provider_key=record.provider_key,
            description=record.description,
            created_at=record.created_at,
            created_by=record.created_by,
        )

    def _to_credential_secret(self, record: WorkflowCredentialRecord) -> CredentialSecret:
        return CredentialSecret(
            id=record.id,
            name=record.name,
            kind=CredentialKind(record.kind),
            provider_key=record.provider_key,
            description=record.description,
            created_at=record.created_at,
            created_by=record.created_by,
            secret_payload=decrypt_secret_payload(record.secret_ciphertext),
        )

    def _to_run_event_domain(self, record: WorkflowRunEventRecord) -> WorkflowRunEvent:
        return WorkflowRunEvent(
            id=record.id,
            run_id=record.run_id,
            workflow_id=record.workflow_id,
            workflow_version=record.workflow_version,
            level=RunEventLevel(record.level),
            event_type=record.event_type,
            message=record.message,
            timestamp=record.timestamp,
            provider_key=record.provider_key,
            step_index=record.step_index,
            payload=record.payload,
        )

    def _to_user_domain(self, record: WorkflowUserRecord) -> UserDefinition:
        return UserDefinition(
            id=record.id,
            username=record.username,
            role=UserRole(record.role),
            active=record.active,
            created_at=record.created_at,
        )

    def _get_run_record(self, session: Session, run_id: str) -> WorkflowRunRecord | None:
        return session.scalar(
            select(WorkflowRunRecord)
            .where(WorkflowRunRecord.id == run_id)
            .options(selectinload(WorkflowRunRecord.steps))
        )

    def _find_run_step(
        self,
        run_record: WorkflowRunRecord,
        step_index: int,
    ) -> WorkflowRunStepRecord | None:
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
