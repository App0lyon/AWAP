"""Application services for the workflow platform."""

from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from awap.domain import (
    CredentialCreateRequest,
    CredentialDefinition,
    ExecutionPlan,
    NodeTypeDefinition,
    ProviderDefinition,
    RunEventLevel,
    WorkflowDefinition,
    WorkflowDraftPayload,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunRequest,
    WorkflowState,
    WorkflowValidationResult,
    WorkflowValidator,
)
from awap.providers import ProviderRegistry, build_default_provider_registry
from awap.repository import WorkflowRepository
from awap.runtime import WorkflowExecutionEngine


class WorkflowService:
    def __init__(
        self,
        repository: WorkflowRepository,
        node_catalog: dict[str, NodeTypeDefinition],
        provider_registry: ProviderRegistry | None = None,
        run_executor: Executor | None = None,
    ) -> None:
        self._repository = repository
        self._node_catalog = node_catalog
        self._validator = WorkflowValidator(node_catalog=node_catalog)
        self._provider_registry = provider_registry or build_default_provider_registry(repository)
        self._runtime = WorkflowExecutionEngine(self._provider_registry)
        self._run_executor = run_executor or ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="awap-runner",
        )

    def list_node_types(self) -> list[NodeTypeDefinition]:
        return sorted(self._node_catalog.values(), key=lambda item: item.display_name)

    def list_providers(self) -> list[ProviderDefinition]:
        return self._provider_registry.list_definitions()

    def create_credential(self, request: CredentialCreateRequest) -> CredentialDefinition:
        return self._repository.create_credential(request)

    def list_credentials(self) -> list[CredentialDefinition]:
        return self._repository.list_credentials()

    def get_credential(self, credential_id: str) -> CredentialDefinition | None:
        return self._repository.get_credential(credential_id)

    def create_workflow(self, workflow: WorkflowDraftPayload) -> WorkflowDefinition:
        definition = WorkflowDefinition(
            name=workflow.name,
            description=workflow.description,
            nodes=workflow.nodes,
            edges=workflow.edges,
        )
        return self._repository.save(definition)

    def create_workflow_version(
        self,
        workflow_id: str,
        workflow: WorkflowDraftPayload,
    ) -> WorkflowDefinition:
        if self._repository.get(workflow_id) is None:
            raise KeyError(workflow_id)

        definition = WorkflowDefinition(
            id=workflow_id,
            version=self._repository.get_next_version(workflow_id),
            name=workflow.name,
            description=workflow.description,
            nodes=workflow.nodes,
            edges=workflow.edges,
        )
        return self._repository.save(definition)

    def list_workflow_versions(self, workflow_id: str) -> list[WorkflowDefinition]:
        versions = self._repository.list_versions(workflow_id)
        if not versions:
            raise KeyError(workflow_id)
        return versions

    def publish_workflow(self, workflow_id: str, version: int) -> WorkflowDefinition:
        workflow = self._require_workflow(workflow_id, version)
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise ValueError("Cannot publish an invalid workflow version.")

        published = self._repository.publish(workflow_id, version)
        if published is None:
            raise KeyError(workflow_id)
        return published

    def list_workflows(self) -> list[WorkflowDefinition]:
        return self._repository.list()

    def get_workflow(
        self,
        workflow_id: str,
        version: int | None = None,
    ) -> WorkflowDefinition | None:
        return self._repository.get(workflow_id, version)

    def validate_workflow(
        self,
        workflow_id: str,
        version: int | None = None,
    ) -> WorkflowValidationResult:
        workflow = self._require_workflow(workflow_id, version)
        return self._validator.validate(workflow)

    def build_execution_plan(
        self,
        workflow_id: str,
        version: int | None = None,
    ) -> ExecutionPlan:
        workflow = self._require_workflow(workflow_id, version)
        return self._validator.create_execution_plan(workflow)

    def start_workflow_run(
        self,
        workflow_id: str,
        request: WorkflowRunRequest,
        version: int | None = None,
    ) -> WorkflowRun:
        workflow = self._select_execution_workflow(workflow_id, version)
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise ValueError("Cannot start a run for an invalid workflow version.")

        plan = self._validator.create_execution_plan(workflow)
        run = self._repository.create_run(workflow, plan, request.input_payload)
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.queued",
            message="Workflow run queued.",
            payload={"input_payload": request.input_payload},
        )
        self._run_executor.submit(
            self._execute_workflow_run,
            run.id,
            workflow,
            plan,
            request.input_payload,
        )
        return run

    def list_workflow_runs(self, workflow_id: str) -> list[WorkflowRun]:
        if self._repository.get(workflow_id) is None:
            raise KeyError(workflow_id)
        return self._repository.list_runs(workflow_id)

    def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return self._repository.get_run(run_id)

    def list_workflow_run_events(self, run_id: str) -> list[WorkflowRunEvent]:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return self._repository.list_run_events(run_id)

    def shutdown(self) -> None:
        shutdown = getattr(self._run_executor, "shutdown", None)
        if shutdown is not None:
            shutdown(wait=False, cancel_futures=False)

    def _require_workflow(
        self,
        workflow_id: str,
        version: int | None = None,
    ) -> WorkflowDefinition:
        workflow = self._repository.get(workflow_id, version)
        if workflow is None:
            raise KeyError(workflow_id)
        return workflow

    def _select_execution_workflow(
        self,
        workflow_id: str,
        version: int | None,
    ) -> WorkflowDefinition:
        if version is not None:
            return self._require_workflow(workflow_id, version)

        versions = self.list_workflow_versions(workflow_id)
        published = next(
            (workflow for workflow in versions if workflow.state is WorkflowState.published),
            None,
        )
        return published or versions[0]

    def _execute_workflow_run(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
        plan: ExecutionPlan,
        input_payload: dict[str, Any],
    ) -> None:
        node_map = {node.id: node for node in workflow.nodes}
        context = self._runtime.create_context(workflow, input_payload)
        step_results: list[dict[str, Any]] = []

        try:
            self._repository.mark_run_running(run_id)
            self._emit_event(
                run_id=run_id,
                workflow=workflow,
                event_type="run.started",
                message="Workflow run started.",
            )
            for step in plan.steps:
                self._repository.mark_step_running(run_id, step.index)
                node = node_map[step.node_id]
                provider_key = node.config.get("provider")
                credential_id = node.config.get("credential_id")
                credential = (
                    self._repository.get_credential_secret(credential_id)
                    if credential_id is not None
                    else None
                )
                self._emit_event(
                    run_id=run_id,
                    workflow=workflow,
                    event_type="step.started",
                    message=f"Step {step.index} started.",
                    provider_key=provider_key,
                    step_index=step.index,
                    payload={"node_id": step.node_id, "node_type": step.node_type},
                )
                try:
                    output_payload = self._runtime.execute_node(node, context, credential)
                except Exception as error:
                    error_message = str(error)
                    self._repository.mark_step_failed(run_id, step.index, error_message)
                    self._repository.mark_run_failed(run_id, error_message)
                    self._emit_event(
                        run_id=run_id,
                        workflow=workflow,
                        event_type="step.failed",
                        message=f"Step {step.index} failed.",
                        level=RunEventLevel.error,
                        provider_key=provider_key,
                        step_index=step.index,
                        payload={"error_message": error_message, "node_id": step.node_id},
                    )
                    self._emit_event(
                        run_id=run_id,
                        workflow=workflow,
                        event_type="run.failed",
                        message="Workflow run failed.",
                        level=RunEventLevel.error,
                        payload={"error_message": error_message},
                    )
                    return

                self._runtime.update_context(context, node, output_payload)
                self._repository.mark_step_succeeded(run_id, step.index, output_payload)
                self._emit_event(
                    run_id=run_id,
                    workflow=workflow,
                    event_type="step.succeeded",
                    message=f"Step {step.index} succeeded.",
                    provider_key=provider_key,
                    step_index=step.index,
                    payload={"node_id": step.node_id, "output": output_payload},
                )
                step_results.append(
                    {
                        "step_index": step.index,
                        "node_id": step.node_id,
                        "output": output_payload,
                    }
                )

            result_payload = {"steps": step_results, "last_output": context["last"]}
            self._repository.mark_run_succeeded(run_id, result_payload)
            self._emit_event(
                run_id=run_id,
                workflow=workflow,
                event_type="run.succeeded",
                message="Workflow run succeeded.",
                payload=result_payload,
            )
        except Exception as error:
            error_message = str(error)
            self._repository.mark_run_failed(run_id, error_message)
            self._emit_event(
                run_id=run_id,
                workflow=workflow,
                event_type="run.failed",
                message="Workflow run failed.",
                level=RunEventLevel.error,
                payload={"error_message": error_message},
            )

    def _emit_event(
        self,
        *,
        run_id: str,
        workflow: WorkflowDefinition,
        event_type: str,
        message: str,
        level: RunEventLevel = RunEventLevel.info,
        provider_key: str | None = None,
        step_index: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = WorkflowRunEvent(
            run_id=run_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            level=level,
            event_type=event_type,
            message=message,
            timestamp=datetime.now(UTC),
            provider_key=provider_key,
            step_index=step_index,
            payload=payload or {},
        )
        self._provider_registry.emit(event)
