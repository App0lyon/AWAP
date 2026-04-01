"""Application services for the workflow platform."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from awap.domain import (
    CredentialCreateRequest,
    CredentialDefinition,
    ExecutionPlan,
    NodeTypeDefinition,
    ProviderDefinition,
    RunEventLevel,
    UserCreateRequest,
    UserDefinition,
    UserRole,
    UserWithToken,
    WorkflowDefinition,
    WorkflowDraftPayload,
    WorkflowEdge,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunRequest,
    WorkflowRunStatus,
    WorkflowRunStep,
    WorkflowRunStepStatus,
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
        *,
        worker_count: int = 2,
        bootstrap_username: str = "admin",
        bootstrap_token: str = "awap-dev-admin-token",
    ) -> None:
        self._repository = repository
        self._node_catalog = node_catalog
        self._validator = WorkflowValidator(node_catalog=node_catalog)
        self._provider_registry = provider_registry or build_default_provider_registry(repository)
        self._runtime = WorkflowExecutionEngine(self._provider_registry)
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._repository.ensure_bootstrap_user(bootstrap_username, bootstrap_token, UserRole.admin)
        for index in range(max(0, worker_count)):
            worker_id = f"awap-worker-{index + 1}"
            thread = threading.Thread(
                target=self._worker_loop,
                args=(worker_id,),
                daemon=True,
                name=worker_id,
            )
            thread.start()
            self._worker_threads.append(thread)

    def list_node_types(self) -> list[NodeTypeDefinition]:
        return sorted(self._node_catalog.values(), key=lambda item: item.display_name)

    def list_providers(self) -> list[ProviderDefinition]:
        return self._provider_registry.list_definitions()

    def create_user(self, request: UserCreateRequest) -> UserWithToken:
        return self._repository.create_user(request)

    def list_users(self) -> list[UserDefinition]:
        return self._repository.list_users()

    def create_credential(
        self,
        request: CredentialCreateRequest,
        *,
        created_by: str | None = None,
    ) -> CredentialDefinition:
        return self._repository.create_credential(request, created_by=created_by)

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
            settings=workflow.settings,
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
            settings=workflow.settings,
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
        if request.idempotency_key:
            existing = self._repository.find_run_by_idempotency_key(workflow_id, request.idempotency_key)
            if existing is not None:
                return existing
        if self._repository.count_active_runs(workflow_id) >= workflow.settings.max_concurrent_runs:
            raise ValueError("Workflow concurrency limit reached.")

        plan = self._validator.create_execution_plan(workflow)
        timeout_seconds = request.timeout_seconds or workflow.settings.run_timeout_seconds
        run = self._repository.create_run(
            workflow,
            plan,
            request.input_payload,
            idempotency_key=request.idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.queued",
            message="Workflow run queued.",
            payload={"input_payload": request.input_payload, "idempotency_key": request.idempotency_key},
        )
        return run

    def pause_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.mark_run_pause_requested(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.pause_requested",
            message="Pause requested for workflow run.",
        )
        return run

    def resume_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.resume_run(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.resumed",
            message="Workflow run resumed.",
        )
        return run

    def cancel_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.mark_run_cancel_requested(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.cancel_requested",
            message="Cancel requested for workflow run.",
        )
        return run

    def retry_workflow_run(self, run_id: str, *, from_failed_step: bool = False) -> WorkflowRun:
        source_run = self._repository.get_run(run_id)
        if source_run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(source_run.workflow_id, source_run.workflow_version)
        plan = self._validator.create_execution_plan(workflow)
        resume_from_step_index: int | None = None
        if from_failed_step:
            failed_step = next(
                (
                    step
                    for step in source_run.steps
                    if step.status in {WorkflowRunStepStatus.failed, WorkflowRunStepStatus.cancelled}
                ),
                None,
            )
            if failed_step is None:
                raise ValueError("Run has no failed or cancelled step to retry from.")
            resume_from_step_index = failed_step.index

        run = self._repository.create_run(
            workflow,
            plan,
            source_run.input_payload,
            retry_of_run_id=source_run.id,
            resume_from_step_index=resume_from_step_index,
            timeout_seconds=source_run.timeout_seconds,
        )
        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.queued",
            message="Retry workflow run queued.",
            payload={"retry_of_run_id": source_run.id, "resume_from_step_index": resume_from_step_index},
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
        self._stop_event.set()
        for thread in self._worker_threads:
            thread.join(timeout=1)

    def _worker_loop(self, worker_id: str) -> None:
        while not self._stop_event.is_set():
            run = self._repository.claim_next_queued_run(worker_id, lease_seconds=30)
            if run is None:
                time.sleep(0.1)
                continue
            try:
                self._execute_workflow_run(run, worker_id)
            except Exception:
                time.sleep(0.1)

    def _execute_workflow_run(self, run: WorkflowRun, worker_id: str) -> None:
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        plan = self._validator.create_execution_plan(workflow)
        node_map = {node.id: node for node in workflow.nodes}
        step_by_node_id = {step.node_id: step for step in plan.steps}
        incoming_map: dict[str, list[WorkflowEdge]] = {node.id: [] for node in workflow.nodes}
        outgoing_map: dict[str, list[WorkflowEdge]] = {node.id: [] for node in workflow.nodes}
        indegree = {node.id: 0 for node in workflow.nodes}
        for edge in workflow.edges:
            outgoing_map[edge.source].append(edge)
            incoming_map[edge.target].append(edge)
            indegree[edge.target] += 1

        queue: deque[str] = deque()
        enqueued: set[str] = set()
        executed: set[str] = set()
        join_tokens: dict[str, int] = {}
        step_results: list[dict[str, Any]] = []
        context = self._runtime.create_context(workflow, run.input_payload)

        if run.retry_of_run_id and run.resume_from_step_index:
            source_run = self._repository.get_run(run.retry_of_run_id)
            if source_run is None:
                self._repository.mark_run_failed(run.id, "Retry source run not found.")
                return
            for source_step in source_run.steps:
                if source_step.index >= run.resume_from_step_index:
                    break
                if source_step.output_payload is None:
                    continue
                node = node_map.get(source_step.node_id)
                if node is None:
                    continue
                self._runtime.update_context(context, node, source_step.output_payload)
                self._repository.mark_step_copied(run.id, source_step.index, source_step.output_payload)
                executed.add(source_step.node_id)
                step_results.append(
                    {
                        "step_index": source_step.index,
                        "node_id": source_step.node_id,
                        "output": source_step.output_payload,
                        "copied": True,
                    }
                )
            restart_step = next(
                (step for step in plan.steps if step.index == run.resume_from_step_index),
                None,
            )
            if restart_step is not None:
                queue.append(restart_step.node_id)
                enqueued.add(restart_step.node_id)
        else:
            trigger_root_ids = [
                node.id
                for node in workflow.nodes
                if self._node_catalog.get(node.type) is not None
                and self._node_catalog[node.type].category.value == "trigger"
            ]
            root_candidates = trigger_root_ids or [node_id for node_id, degree in indegree.items() if degree == 0]
            root_node_ids = sorted(root_candidates, key=lambda item: step_by_node_id[item].index)
            queue.extend(root_node_ids)
            enqueued.update(root_node_ids)

        self._emit_event(
            run_id=run.id,
            workflow=workflow,
            event_type="run.started",
            message="Workflow run started.",
        )

        try:
            while queue:
                control = self._check_run_control(run.id, workflow, worker_id)
                if control == "cancelled":
                    return

                node_id = queue.popleft()
                enqueued.discard(node_id)
                if node_id in executed:
                    continue

                step = step_by_node_id[node_id]
                node = node_map[node_id]
                self._repository.mark_step_running(run.id, step.index)
                provider_key = node.config.get("provider")
                credential_id = node.config.get("credential_id")
                credential = (
                    self._repository.get_credential_secret(credential_id)
                    if credential_id is not None
                    else None
                )
                self._emit_event(
                    run_id=run.id,
                    workflow=workflow,
                    event_type="step.started",
                    message=f"Step {step.index} started.",
                    provider_key=provider_key,
                    step_index=step.index,
                    payload={"node_id": step.node_id, "node_type": step.node_type},
                )

                output_payload = self._runtime.execute_node(
                    node,
                    context,
                    credential,
                    invoke_subworkflow=self._invoke_subworkflow,
                )
                self._runtime.update_context(context, node, output_payload)
                self._repository.mark_step_succeeded(run.id, step.index, output_payload)
                executed.add(node_id)
                step_results.append(
                    {
                        "step_index": step.index,
                        "node_id": step.node_id,
                        "output": output_payload,
                    }
                )
                self._emit_event(
                    run_id=run.id,
                    workflow=workflow,
                    event_type="step.succeeded",
                    message=f"Step {step.index} succeeded.",
                    provider_key=provider_key,
                    step_index=step.index,
                    payload={"node_id": step.node_id, "output": output_payload},
                )

                selected_edges = self._runtime.select_edges(node, output_payload, outgoing_map[node_id])
                for edge in selected_edges:
                    target_node = node_map[edge.target]
                    self._runtime.record_join_inputs(context, edge.target, node_id, output_payload)
                    if target_node.type == "join":
                        join_tokens[edge.target] = join_tokens.get(edge.target, 0) + 1
                        required_inputs = int(
                            target_node.config.get("required_inputs", len(incoming_map[edge.target]))
                        )
                        if (
                            join_tokens[edge.target] >= required_inputs
                            and edge.target not in executed
                            and edge.target not in enqueued
                        ):
                            queue.append(edge.target)
                            enqueued.add(edge.target)
                        continue
                    if edge.target not in executed and edge.target not in enqueued:
                        queue.append(edge.target)
                        enqueued.add(edge.target)

            final_run = self._repository.get_run(run.id)
            if final_run is not None:
                self._mark_remaining_steps(
                    run.id,
                    WorkflowRunStepStatus.skipped,
                    "Step was not activated by the selected execution path.",
                )
            result_payload = {"steps": step_results, "last_output": context["last"]}
            self._repository.mark_run_succeeded(run.id, result_payload)
            self._emit_event(
                run_id=run.id,
                workflow=workflow,
                event_type="run.succeeded",
                message="Workflow run succeeded.",
                payload=result_payload,
            )
        except Exception as error:
            error_message = str(error)
            failed_run = self._repository.get_run(run.id)
            if failed_run is not None:
                failed_step = next(
                    (step for step in failed_run.steps if step.status == step.status.running),
                    None,
                )
                if failed_step is not None:
                    self._repository.mark_step_failed(run.id, failed_step.index, error_message)
                    self._emit_event(
                        run_id=run.id,
                        workflow=workflow,
                        event_type="step.failed",
                        message=f"Step {failed_step.index} failed.",
                        level=RunEventLevel.error,
                        step_index=failed_step.index,
                        payload={"error_message": error_message, "node_id": failed_step.node_id},
                    )
            self._repository.mark_run_failed(run.id, error_message)
            self._emit_event(
                run_id=run.id,
                workflow=workflow,
                event_type="run.failed",
                message="Workflow run failed.",
                level=RunEventLevel.error,
                payload={"error_message": error_message},
            )

    def _check_run_control(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
        worker_id: str,
    ) -> str:
        current_run = self._repository.get_run(run_id)
        if current_run is None:
            return "missing"
        if current_run.timeout_seconds and current_run.started_at:
            elapsed = datetime.now(UTC) - current_run.started_at
            if elapsed > timedelta(seconds=current_run.timeout_seconds):
                self._repository.mark_run_failed(run_id, "Workflow run timed out.")
                self._emit_event(
                    run_id=run_id,
                    workflow=workflow,
                    event_type="run.failed",
                    message="Workflow run timed out.",
                    level=RunEventLevel.error,
                    payload={"timeout_seconds": current_run.timeout_seconds},
                )
                return "cancelled"
        if current_run.status == WorkflowRunStatus.cancelling:
            self._mark_remaining_steps(run_id, WorkflowRunStepStatus.cancelled, "Run cancelled.")
            self._repository.mark_run_cancelled(run_id, "Workflow run cancelled by operator.")
            self._emit_event(
                run_id=run_id,
                workflow=workflow,
                event_type="run.cancelled",
                message="Workflow run cancelled.",
            )
            return "cancelled"
        if current_run.status in {WorkflowRunStatus.pause_requested, WorkflowRunStatus.paused}:
            self._repository.mark_run_paused(run_id, worker_id)
            self._emit_event(
                run_id=run_id,
                workflow=workflow,
                event_type="run.paused",
                message="Workflow run paused.",
            )
            while not self._stop_event.is_set():
                time.sleep(0.1)
                current_run = self._repository.get_run(run_id)
                if current_run is None:
                    return "missing"
                if current_run.status == WorkflowRunStatus.cancelling:
                    self._mark_remaining_steps(run_id, WorkflowRunStepStatus.cancelled, "Run cancelled.")
                    self._repository.mark_run_cancelled(run_id, "Workflow run cancelled while paused.")
                    self._emit_event(
                        run_id=run_id,
                        workflow=workflow,
                        event_type="run.cancelled",
                        message="Workflow run cancelled.",
                    )
                    return "cancelled"
                if current_run.status == WorkflowRunStatus.running:
                    return "continue"
                if current_run.status == WorkflowRunStatus.queued:
                    self._repository.mark_run_running(run_id, worker_id)
                    return "continue"
            return "cancelled"
        return "continue"

    def _mark_remaining_steps(
        self,
        run_id: str,
        status: WorkflowRunStepStatus,
        message: str,
    ) -> None:
        current_run = self._repository.get_run(run_id)
        if current_run is None:
            return
        for step in current_run.steps:
            if step.status == WorkflowRunStepStatus.pending:
                if status == WorkflowRunStepStatus.cancelled:
                    self._repository.mark_step_cancelled(run_id, step.index, message)
                elif status == WorkflowRunStepStatus.skipped:
                    self._repository.mark_step_skipped(run_id, step.index, message)

    def _invoke_subworkflow(
        self,
        workflow_id: str,
        version: int | None,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        workflow = self._require_workflow(workflow_id, version)
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise RuntimeError("Cannot execute an invalid sub-workflow.")
        return self._execute_inline_workflow(workflow, input_payload)

    def _execute_inline_workflow(
        self,
        workflow: WorkflowDefinition,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        node_map = {node.id: node for node in workflow.nodes}
        outgoing_map: dict[str, list[WorkflowEdge]] = {node.id: [] for node in workflow.nodes}
        incoming_count = {node.id: 0 for node in workflow.nodes}
        for edge in workflow.edges:
            outgoing_map[edge.source].append(edge)
            incoming_count[edge.target] += 1

        trigger_root_ids = [
            node.id
            for node in workflow.nodes
            if self._node_catalog.get(node.type) is not None
            and self._node_catalog[node.type].category.value == "trigger"
        ]
        queue = deque(sorted(trigger_root_ids or (node_id for node_id, count in incoming_count.items() if count == 0)))
        enqueued = set(queue)
        executed: set[str] = set()
        join_tokens: dict[str, int] = {}
        join_inputs: dict[str, int] = {
            node.id: len([edge for edge in workflow.edges if edge.target == node.id])
            for node in workflow.nodes
            if node.type == "join"
        }
        context = self._runtime.create_context(workflow, input_payload)
        step_results: list[dict[str, Any]] = []

        while queue:
            node_id = queue.popleft()
            enqueued.discard(node_id)
            if node_id in executed:
                continue
            node = node_map[node_id]
            credential_id = node.config.get("credential_id")
            credential = (
                self._repository.get_credential_secret(credential_id)
                if credential_id is not None
                else None
            )
            output_payload = self._runtime.execute_node(
                node,
                context,
                credential,
                invoke_subworkflow=self._invoke_subworkflow,
            )
            self._runtime.update_context(context, node, output_payload)
            executed.add(node_id)
            step_results.append({"node_id": node_id, "output": output_payload})
            for edge in self._runtime.select_edges(node, output_payload, outgoing_map[node_id]):
                target_node = node_map[edge.target]
                self._runtime.record_join_inputs(context, edge.target, node_id, output_payload)
                if target_node.type == "join":
                    join_tokens[edge.target] = join_tokens.get(edge.target, 0) + 1
                    if (
                        join_tokens[edge.target] >= int(target_node.config.get("required_inputs", join_inputs[edge.target]))
                        and edge.target not in executed
                        and edge.target not in enqueued
                    ):
                        queue.append(edge.target)
                        enqueued.add(edge.target)
                    continue
                if edge.target not in executed and edge.target not in enqueued:
                    queue.append(edge.target)
                    enqueued.add(edge.target)

        return {"steps": step_results, "last_output": context["last"]}

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
        published = next((workflow for workflow in versions if workflow.state.value == "published"), None)
        return published or versions[0]

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
