"""FastAPI application for AWAP."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from awap.auth import require_role
from awap.catalog import DEFAULT_NODE_CATALOG
from awap.database import create_database_engine, initialize_database
from awap.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalTaskDefinition,
    AuditLogEntry,
    CredentialCreateRequest,
    CredentialDefinition,
    DeadLetterDefinition,
    EvaluationRunCreateRequest,
    EvaluationRunDefinition,
    ExecutionPlan,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDefinition,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDefinition,
    KnowledgeSearchResult,
    NodeTypeDefinition,
    ObservabilitySummary,
    PromptTemplateCreateRequest,
    PromptTemplateDefinition,
    ProviderDefinition,
    SourceControlStatus,
    UserCreateRequest,
    UserDefinition,
    UserRole,
    UserWithToken,
    WorkflowCommentCreateRequest,
    WorkflowCommentDefinition,
    WorkflowDefinition,
    WorkflowDraftPayload,
    WorkflowEnvironmentCreateRequest,
    WorkflowEnvironmentDefinition,
    WorkflowEnvironmentReleaseDefinition,
    WorkflowExportBundle,
    WorkflowImportRequest,
    WorkflowPromotionRequest,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunRequest,
    WorkflowRunStatus,
    WorkflowTemplateDefinition,
    WorkflowValidationResult,
    WorkflowTriggerStateDefinition,
    WorkflowVersionDiff,
    WorkerHealthDefinition,
)
from awap.repository import SqlAlchemyWorkflowRepository
from awap.service import WorkflowService

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
STATIC_DIR = UI_DIR / "static"


def create_app(database_url: str | None = None, *, worker_count: int = 2) -> FastAPI:
    engine = create_database_engine(database_url)
    initialize_database(engine)
    repository = SqlAlchemyWorkflowRepository(engine)
    service = WorkflowService(
        repository=repository,
        node_catalog=DEFAULT_NODE_CATALOG,
        worker_count=worker_count,
        bootstrap_username=os.getenv("AWAP_BOOTSTRAP_ADMIN_USERNAME", "admin"),
        bootstrap_token=os.getenv("AWAP_BOOTSTRAP_ADMIN_TOKEN", "awap-dev-admin-token"),
    )

    viewer_access = require_role(repository, UserRole.admin, UserRole.editor, UserRole.operator, UserRole.viewer)
    editor_access = require_role(repository, UserRole.admin, UserRole.editor)
    operator_access = require_role(repository, UserRole.admin, UserRole.editor, UserRole.operator)
    admin_access = require_role(repository, UserRole.admin)

    app = FastAPI(title="AWAP", version="0.3.0", description="AI Workflow Automation Platform")
    app.router.on_shutdown.append(service.shutdown)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/me", response_model=UserDefinition)
    def auth_me(user: UserDefinition = Depends(viewer_access)) -> UserDefinition:
        return user

    @app.get("/users", response_model=list[UserDefinition])
    def list_users(user: UserDefinition = Depends(admin_access)) -> list[UserDefinition]:
        del user
        return service.list_users()

    @app.post("/users", response_model=UserWithToken, status_code=201)
    def create_user(request: UserCreateRequest, user: UserDefinition = Depends(admin_access)) -> UserWithToken:
        del user
        return service.create_user(request)

    @app.get("/node-types", response_model=list[NodeTypeDefinition])
    def list_node_types(user: UserDefinition = Depends(viewer_access)) -> list[NodeTypeDefinition]:
        del user
        return service.list_node_types()

    @app.get("/providers", response_model=list[ProviderDefinition])
    def list_providers(user: UserDefinition = Depends(viewer_access)) -> list[ProviderDefinition]:
        del user
        return service.list_providers()

    @app.get("/credentials", response_model=list[CredentialDefinition])
    def list_credentials(user: UserDefinition = Depends(editor_access)) -> list[CredentialDefinition]:
        del user
        return service.list_credentials()

    @app.post("/credentials", response_model=CredentialDefinition, status_code=201)
    def create_credential(request: CredentialCreateRequest, user: UserDefinition = Depends(editor_access)) -> CredentialDefinition:
        return service.create_credential(request, created_by=user.id)

    @app.get("/credentials/{credential_id}", response_model=CredentialDefinition)
    def get_credential(credential_id: str, user: UserDefinition = Depends(editor_access)) -> CredentialDefinition:
        del user
        credential = service.get_credential(credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Credential not found.")
        return credential

    @app.get("/knowledge-bases", response_model=list[KnowledgeBaseDefinition])
    def list_knowledge_bases(user: UserDefinition = Depends(viewer_access)) -> list[KnowledgeBaseDefinition]:
        del user
        return service.list_knowledge_bases()

    @app.post("/knowledge-bases", response_model=KnowledgeBaseDefinition, status_code=201)
    def create_knowledge_base(request: KnowledgeBaseCreateRequest, user: UserDefinition = Depends(editor_access)) -> KnowledgeBaseDefinition:
        return service.create_knowledge_base(request, created_by=user.id)

    @app.get("/knowledge-bases/{knowledge_base_id}/documents", response_model=list[KnowledgeDocumentDefinition])
    def list_knowledge_documents(knowledge_base_id: str, user: UserDefinition = Depends(viewer_access)) -> list[KnowledgeDocumentDefinition]:
        del user
        return service.list_knowledge_documents(knowledge_base_id)

    @app.post("/knowledge-documents", response_model=KnowledgeDocumentDefinition, status_code=201)
    def create_knowledge_document(request: KnowledgeDocumentCreateRequest, user: UserDefinition = Depends(editor_access)) -> KnowledgeDocumentDefinition:
        try:
            return service.create_knowledge_document(request, created_by=user.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Knowledge base not found.") from error

    @app.get("/knowledge-bases/{knowledge_base_id}/search", response_model=KnowledgeSearchResult)
    def search_knowledge(
        knowledge_base_id: str,
        query: str = Query(..., min_length=1),
        top_k: int = Query(default=5, ge=1, le=20),
        user: UserDefinition = Depends(viewer_access),
    ) -> KnowledgeSearchResult:
        del user
        return service.search_knowledge(knowledge_base_id, query, top_k=top_k)

    @app.get("/approval-tasks", response_model=list[ApprovalTaskDefinition])
    def list_approval_tasks(
        run_id: str | None = Query(default=None),
        decision: ApprovalDecision | None = Query(default=None),
        user: UserDefinition = Depends(operator_access),
    ) -> list[ApprovalTaskDefinition]:
        del user
        return service.list_approval_tasks(run_id=run_id, decision=decision)

    @app.post("/approval-tasks/{approval_task_id}/decision", response_model=ApprovalTaskDefinition)
    def decide_approval_task(
        approval_task_id: str,
        request: ApprovalDecisionRequest,
        user: UserDefinition = Depends(operator_access),
    ) -> ApprovalTaskDefinition:
        try:
            return service.decide_approval_task(approval_task_id, request, decided_by=user.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Approval task not found.") from error

    @app.get("/prompt-templates", response_model=list[PromptTemplateDefinition])
    def list_prompt_templates(
        name: str | None = Query(default=None),
        user: UserDefinition = Depends(viewer_access),
    ) -> list[PromptTemplateDefinition]:
        del user
        return service.list_prompt_templates(name=name)

    @app.post("/prompt-templates", response_model=PromptTemplateDefinition, status_code=201)
    def create_prompt_template(
        request: PromptTemplateCreateRequest,
        user: UserDefinition = Depends(editor_access),
    ) -> PromptTemplateDefinition:
        return service.create_prompt_template(request, created_by=user.id)

    @app.get("/prompt-templates/{prompt_template_id}", response_model=PromptTemplateDefinition)
    def get_prompt_template(
        prompt_template_id: str,
        user: UserDefinition = Depends(viewer_access),
    ) -> PromptTemplateDefinition:
        del user
        prompt_template = service.get_prompt_template(prompt_template_id)
        if prompt_template is None:
            raise HTTPException(status_code=404, detail="Prompt template not found.")
        return prompt_template

    @app.get("/evaluations", response_model=list[EvaluationRunDefinition])
    def list_evaluations(user: UserDefinition = Depends(viewer_access)) -> list[EvaluationRunDefinition]:
        del user
        return service.list_evaluation_runs()

    @app.post("/evaluations", response_model=EvaluationRunDefinition, status_code=201)
    def create_evaluation(
        request: EvaluationRunCreateRequest,
        user: UserDefinition = Depends(editor_access),
    ) -> EvaluationRunDefinition:
        return service.create_evaluation_run(request, created_by=user.id)

    @app.get("/evaluations/{evaluation_run_id}", response_model=EvaluationRunDefinition)
    def get_evaluation(
        evaluation_run_id: str,
        user: UserDefinition = Depends(viewer_access),
    ) -> EvaluationRunDefinition:
        del user
        evaluation = service.get_evaluation_run(evaluation_run_id)
        if evaluation is None:
            raise HTTPException(status_code=404, detail="Evaluation run not found.")
        return evaluation

    @app.get("/environments", response_model=list[WorkflowEnvironmentDefinition])
    def list_environments(user: UserDefinition = Depends(viewer_access)) -> list[WorkflowEnvironmentDefinition]:
        del user
        return service.list_environments()

    @app.post("/environments", response_model=WorkflowEnvironmentDefinition, status_code=201)
    def create_environment(
        request: WorkflowEnvironmentCreateRequest,
        user: UserDefinition = Depends(admin_access),
    ) -> WorkflowEnvironmentDefinition:
        del user
        return service.create_environment(request)

    @app.get("/environments/{environment}/releases", response_model=list[WorkflowEnvironmentReleaseDefinition])
    def list_environment_releases(
        environment: str,
        user: UserDefinition = Depends(viewer_access),
    ) -> list[WorkflowEnvironmentReleaseDefinition]:
        del user
        return service.list_environment_releases(environment=environment)

    @app.post("/workflows/{workflow_id}/promotions", response_model=WorkflowEnvironmentReleaseDefinition)
    def promote_workflow(
        workflow_id: str,
        request: WorkflowPromotionRequest,
        user: UserDefinition = Depends(editor_access),
    ) -> WorkflowEnvironmentReleaseDefinition:
        try:
            return service.promote_workflow(workflow_id, request, promoted_by=user.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow or environment not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/observability/summary", response_model=ObservabilitySummary)
    def observability_summary(user: UserDefinition = Depends(viewer_access)) -> ObservabilitySummary:
        del user
        return service.get_observability_summary()

    @app.get("/trigger-states", response_model=list[WorkflowTriggerStateDefinition])
    def list_trigger_states(user: UserDefinition = Depends(viewer_access)) -> list[WorkflowTriggerStateDefinition]:
        del user
        return service.list_trigger_states()

    @app.get("/dead-letters", response_model=list[DeadLetterDefinition])
    def list_dead_letters(
        workflow_id: str | None = Query(default=None),
        user: UserDefinition = Depends(operator_access),
    ) -> list[DeadLetterDefinition]:
        del user
        return service.list_dead_letters(workflow_id=workflow_id)

    @app.get("/worker-health", response_model=list[WorkerHealthDefinition])
    def worker_health(user: UserDefinition = Depends(viewer_access)) -> list[WorkerHealthDefinition]:
        del user
        return service.list_worker_health()

    @app.get("/source-control/status", response_model=SourceControlStatus)
    def source_control_status(user: UserDefinition = Depends(viewer_access)) -> SourceControlStatus:
        del user
        return service.get_source_control_status()

    @app.get("/workflow-templates", response_model=list[WorkflowTemplateDefinition])
    def list_workflow_templates(user: UserDefinition = Depends(viewer_access)) -> list[WorkflowTemplateDefinition]:
        del user
        return service.list_workflow_templates()

    @app.get("/workflows", response_model=list[WorkflowDefinition])
    def list_workflows(user: UserDefinition = Depends(viewer_access)) -> list[WorkflowDefinition]:
        del user
        return service.list_workflows()

    @app.post("/workflows", response_model=WorkflowDefinition, status_code=201)
    def create_workflow(workflow: WorkflowDraftPayload, user: UserDefinition = Depends(editor_access)) -> WorkflowDefinition:
        return service.create_workflow(workflow, owner_id=user.id)

    @app.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowDefinition])
    def list_workflow_versions(workflow_id: str, user: UserDefinition = Depends(viewer_access)) -> list[WorkflowDefinition]:
        del user
        try:
            return service.list_workflow_versions(workflow_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/versions", response_model=WorkflowDefinition, status_code=201)
    def create_workflow_version(workflow_id: str, workflow: WorkflowDraftPayload, user: UserDefinition = Depends(editor_access)) -> WorkflowDefinition:
        try:
            return service.create_workflow_version(workflow_id, workflow, actor_id=user.id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/versions/{version}/publish", response_model=WorkflowDefinition)
    def publish_workflow(workflow_id: str, version: int, user: UserDefinition = Depends(editor_access)) -> WorkflowDefinition:
        try:
            return service.publish_workflow(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/workflows/{workflow_id}/versions/compare", response_model=WorkflowVersionDiff)
    def compare_workflow_versions(
        workflow_id: str,
        from_version: int = Query(..., ge=1),
        to_version: int = Query(..., ge=1),
        user: UserDefinition = Depends(viewer_access),
    ) -> WorkflowVersionDiff:
        del user
        try:
            return service.compare_workflow_versions(workflow_id, from_version, to_version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.get("/workflows/{workflow_id}/comments", response_model=list[WorkflowCommentDefinition])
    def list_workflow_comments(
        workflow_id: str,
        workflow_version: int | None = Query(default=None, ge=1),
        user: UserDefinition = Depends(viewer_access),
    ) -> list[WorkflowCommentDefinition]:
        del user
        return service.list_workflow_comments(workflow_id, workflow_version)

    @app.post("/workflows/{workflow_id}/comments", response_model=WorkflowCommentDefinition, status_code=201)
    def create_workflow_comment(
        workflow_id: str,
        request: WorkflowCommentCreateRequest,
        user: UserDefinition = Depends(editor_access),
    ) -> WorkflowCommentDefinition:
        if request.workflow_id != workflow_id:
            raise HTTPException(status_code=400, detail="Workflow id mismatch.")
        return service.create_workflow_comment(request, author_id=user.id)

    @app.get("/audit-logs", response_model=list[AuditLogEntry])
    def list_audit_logs(
        workflow_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        user: UserDefinition = Depends(admin_access),
    ) -> list[AuditLogEntry]:
        del user
        return service.list_audit_logs(workflow_id=workflow_id, run_id=run_id, limit=limit)

    @app.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
    def get_workflow(workflow_id: str, version: int | None = Query(default=None, ge=1), user: UserDefinition = Depends(viewer_access)) -> WorkflowDefinition:
        del user
        workflow = service.get_workflow(workflow_id, version)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return workflow

    @app.post("/workflows/{workflow_id}/validate", response_model=WorkflowValidationResult)
    def validate_workflow(workflow_id: str, version: int | None = Query(default=None, ge=1), user: UserDefinition = Depends(viewer_access)) -> WorkflowValidationResult:
        del user
        try:
            return service.validate_workflow(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/plan", response_model=ExecutionPlan)
    def build_execution_plan(workflow_id: str, version: int | None = Query(default=None, ge=1), user: UserDefinition = Depends(viewer_access)) -> ExecutionPlan:
        del user
        try:
            return service.build_execution_plan(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/workflows/{workflow_id}/export", response_model=WorkflowExportBundle)
    def export_workflow(workflow_id: str, user: UserDefinition = Depends(viewer_access)) -> WorkflowExportBundle:
        del user
        try:
            return service.export_workflow(workflow_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/import", response_model=WorkflowDefinition, status_code=201)
    def import_workflow(request: WorkflowImportRequest, user: UserDefinition = Depends(editor_access)) -> WorkflowDefinition:
        del user
        try:
            return service.import_workflow(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found during import.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/workflows/{workflow_id}/runs", response_model=list[WorkflowRun])
    def list_workflow_runs(workflow_id: str, user: UserDefinition = Depends(viewer_access)) -> list[WorkflowRun]:
        del user
        try:
            return service.list_workflow_runs(workflow_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/runs", response_model=WorkflowRun, status_code=202)
    def start_workflow_run(workflow_id: str, run_request: WorkflowRunRequest, version: int | None = Query(default=None, ge=1), user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.start_workflow_run(workflow_id, run_request, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/triggers/webhook/{workflow_id}", response_model=WorkflowRun, status_code=202)
    def trigger_workflow_webhook(workflow_id: str, run_request: WorkflowRunRequest, version: int | None = Query(default=None, ge=1), user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.trigger_workflow_webhook(workflow_id, run_request, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/runs/search", response_model=list[WorkflowRun])
    def search_runs(
        workflow_id: str | None = Query(default=None),
        status: WorkflowRunStatus | None = Query(default=None),
        environment: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        user: UserDefinition = Depends(viewer_access),
    ) -> list[WorkflowRun]:
        del user
        return service.search_runs(workflow_id=workflow_id, status=status, environment=environment, limit=limit)

    @app.get("/runs/{run_id}", response_model=WorkflowRun)
    def get_workflow_run(run_id: str, user: UserDefinition = Depends(viewer_access)) -> WorkflowRun:
        del user
        run = service.get_workflow_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @app.post("/runs/{run_id}/pause", response_model=WorkflowRun)
    def pause_workflow_run(run_id: str, user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.pause_workflow_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.post("/runs/{run_id}/resume", response_model=WorkflowRun)
    def resume_workflow_run(run_id: str, user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.resume_workflow_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.post("/runs/{run_id}/cancel", response_model=WorkflowRun)
    def cancel_workflow_run(run_id: str, user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.cancel_workflow_run(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    @app.post("/runs/{run_id}/retry", response_model=WorkflowRun, status_code=202)
    def retry_workflow_run(run_id: str, from_failed_step: bool = Query(default=False), user: UserDefinition = Depends(operator_access)) -> WorkflowRun:
        del user
        try:
            return service.retry_workflow_run(run_id, from_failed_step=from_failed_step)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/runs/{run_id}/events", response_model=list[WorkflowRunEvent])
    def list_workflow_run_events(run_id: str, user: UserDefinition = Depends(viewer_access)) -> list[WorkflowRunEvent]:
        del user
        try:
            return service.list_workflow_run_events(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    return app
