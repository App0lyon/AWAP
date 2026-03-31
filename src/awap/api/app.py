"""FastAPI application for AWAP."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from awap.catalog import DEFAULT_NODE_CATALOG
from awap.database import create_database_engine, initialize_database
from awap.domain import (
    CredentialCreateRequest,
    CredentialDefinition,
    ExecutionPlan,
    NodeTypeDefinition,
    ProviderDefinition,
    WorkflowDefinition,
    WorkflowDraftPayload,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunRequest,
    WorkflowValidationResult,
)
from awap.repository import SqlAlchemyWorkflowRepository
from awap.service import WorkflowService

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
STATIC_DIR = UI_DIR / "static"


def create_app(database_url: str | None = None) -> FastAPI:
    engine = create_database_engine(database_url)
    initialize_database(engine)
    repository = SqlAlchemyWorkflowRepository(engine)
    service = WorkflowService(repository=repository, node_catalog=DEFAULT_NODE_CATALOG)

    app = FastAPI(
        title="AWAP",
        version="0.1.0",
        description="AI Workflow Automation Platform backend foundation",
    )
    app.router.on_shutdown.append(service.shutdown)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/node-types", response_model=list[NodeTypeDefinition])
    def list_node_types() -> list[NodeTypeDefinition]:
        return service.list_node_types()

    @app.get("/providers", response_model=list[ProviderDefinition])
    def list_providers() -> list[ProviderDefinition]:
        return service.list_providers()

    @app.get("/credentials", response_model=list[CredentialDefinition])
    def list_credentials() -> list[CredentialDefinition]:
        return service.list_credentials()

    @app.post("/credentials", response_model=CredentialDefinition, status_code=201)
    def create_credential(request: CredentialCreateRequest) -> CredentialDefinition:
        return service.create_credential(request)

    @app.get("/credentials/{credential_id}", response_model=CredentialDefinition)
    def get_credential(credential_id: str) -> CredentialDefinition:
        credential = service.get_credential(credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Credential not found.")
        return credential

    @app.get("/workflows", response_model=list[WorkflowDefinition])
    def list_workflows() -> list[WorkflowDefinition]:
        return service.list_workflows()

    @app.post("/workflows", response_model=WorkflowDefinition, status_code=201)
    def create_workflow(workflow: WorkflowDraftPayload) -> WorkflowDefinition:
        return service.create_workflow(workflow)

    @app.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowDefinition])
    def list_workflow_versions(workflow_id: str) -> list[WorkflowDefinition]:
        try:
            return service.list_workflow_versions(workflow_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post(
        "/workflows/{workflow_id}/versions",
        response_model=WorkflowDefinition,
        status_code=201,
    )
    def create_workflow_version(
        workflow_id: str,
        workflow: WorkflowDraftPayload,
    ) -> WorkflowDefinition:
        try:
            return service.create_workflow_version(workflow_id, workflow)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post(
        "/workflows/{workflow_id}/versions/{version}/publish",
        response_model=WorkflowDefinition,
    )
    def publish_workflow(workflow_id: str, version: int) -> WorkflowDefinition:
        try:
            return service.publish_workflow(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
    def get_workflow(
        workflow_id: str,
        version: int | None = Query(default=None, ge=1),
    ) -> WorkflowDefinition:
        workflow = service.get_workflow(workflow_id, version)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found.")
        return workflow

    @app.post("/workflows/{workflow_id}/validate", response_model=WorkflowValidationResult)
    def validate_workflow(
        workflow_id: str,
        version: int | None = Query(default=None, ge=1),
    ) -> WorkflowValidationResult:
        try:
            return service.validate_workflow(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/plan", response_model=ExecutionPlan)
    def build_execution_plan(
        workflow_id: str,
        version: int | None = Query(default=None, ge=1),
    ) -> ExecutionPlan:
        try:
            return service.build_execution_plan(workflow_id, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/workflows/{workflow_id}/runs", response_model=list[WorkflowRun])
    def list_workflow_runs(workflow_id: str) -> list[WorkflowRun]:
        try:
            return service.list_workflow_runs(workflow_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error

    @app.post("/workflows/{workflow_id}/runs", response_model=WorkflowRun, status_code=202)
    def start_workflow_run(
        workflow_id: str,
        run_request: WorkflowRunRequest,
        version: int | None = Query(default=None, ge=1),
    ) -> WorkflowRun:
        try:
            return service.start_workflow_run(workflow_id, run_request, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Workflow not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/runs/{run_id}", response_model=WorkflowRun)
    def get_workflow_run(run_id: str) -> WorkflowRun:
        run = service.get_workflow_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run

    @app.get("/runs/{run_id}/events", response_model=list[WorkflowRunEvent])
    def list_workflow_run_events(run_id: str) -> list[WorkflowRunEvent]:
        try:
            return service.list_workflow_run_events(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found.") from error

    return app
