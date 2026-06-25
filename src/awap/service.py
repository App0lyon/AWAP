"""Application services for the workflow platform."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from awap.artifacts import ARTIFACT_REFERENCE_KEY, LocalArtifactStore
from awap.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalTaskDefinition,
    AuditLogEntry,
    CredentialCreateRequest,
    CredentialDefinition,
    DeadLetterDefinition,
    EvaluationCaseResult,
    EvaluationRunCreateRequest,
    EvaluationRunDefinition,
    EvaluationStatus,
    ExecutionPlan,
    InfrastructureStatus,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDefinition,
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentDefinition,
    KnowledgeSearchResult,
    MonitoringAlert,
    NodeCategory,
    NodeTypeDefinition,
    ObservabilitySummary,
    PromptTemplateCreateRequest,
    PromptTemplateDefinition,
    ProviderConnectionCheck,
    ProviderDefinition,
    ProviderKind,
    RunEventLevel,
    SourceControlStatus,
    UserCreateRequest,
    UserDefinition,
    UserRole,
    UserWithToken,
    WorkerHealthDefinition,
    WorkflowCommentCreateRequest,
    WorkflowCommentDefinition,
    WorkflowDefinition,
    WorkflowDraftPayload,
    WorkflowEdge,
    WorkflowEnvironmentCreateRequest,
    WorkflowEnvironmentDefinition,
    WorkflowEnvironmentReleaseDefinition,
    WorkflowExportBundle,
    WorkflowImportRequest,
    WorkflowPromotionRequest,
    WorkflowReadinessCheck,
    WorkflowReadinessReport,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowRunRequest,
    WorkflowRunStatus,
    WorkflowRunStepStatus,
    WorkflowTemplateDefinition,
    WorkflowTriggerStateDefinition,
    WorkflowValidationResult,
    WorkflowValidator,
    WorkflowVersionDiff,
)
from awap.evaluation import score_evaluation_case
from awap.providers import ProviderRegistry, build_default_provider_registry
from awap.queue import RunQueue, SQLiteRunQueue
from awap.repository import WorkflowRepository
from awap.runtime import ApprovalRequiredError, WorkflowExecutionEngine
from awap.schedule import cron_matches, schedule_bucket, utc_now
from awap.templates import BUILTIN_WORKFLOW_TEMPLATES


class WorkflowService:
    def __init__(
        self,
        repository: WorkflowRepository,
        node_catalog: dict[str, NodeTypeDefinition],
        provider_registry: ProviderRegistry | None = None,
        *,
        worker_count: int = 2,
        bootstrap_username: str = "admin",
        bootstrap_token: str | None = "awap-dev-admin-token",
        run_queue: RunQueue | None = None,
    ) -> None:
        self._repository = repository
        self._run_queue = run_queue or SQLiteRunQueue(repository)
        self._node_catalog = node_catalog
        self._validator = WorkflowValidator(node_catalog=node_catalog)
        self._provider_registry = provider_registry or build_default_provider_registry(repository)
        self._runtime = WorkflowExecutionEngine(self._provider_registry)
        self._stored_payload_limit_bytes = int(os.getenv("AWAP_MAX_STORED_PAYLOAD_BYTES", "32768"))
        self._artifact_store = LocalArtifactStore(
            Path(os.getenv("AWAP_ARTIFACT_DIR", "/tmp/awap-artifacts"))
        )
        self._stop_event = threading.Event()
        self._worker_threads: list[threading.Thread] = []
        self._worker_health: dict[str, WorkerHealthDefinition] = {}
        self._scheduler_thread: threading.Thread | None = None
        if bootstrap_token is not None:
            self._repository.ensure_bootstrap_user(bootstrap_username, bootstrap_token, UserRole.admin)
        self._repository.ensure_environment("dev", description="Development environment", is_default=True)
        self._repository.ensure_environment("staging", description="Staging environment")
        self._repository.ensure_environment("prod", description="Production environment")
        for index in range(max(0, worker_count)):
            worker_id = f"awap-worker-{index + 1}"
            self._worker_health[worker_id] = WorkerHealthDefinition(worker_id=worker_id, running=True, leased_run_id=None, updated_at=datetime.now(UTC))
            thread = threading.Thread(target=self._worker_loop, args=(worker_id,), daemon=True, name=worker_id)
            thread.start()
            self._worker_threads.append(thread)
        self._worker_health["awap-scheduler"] = WorkerHealthDefinition(worker_id="awap-scheduler", running=True, leased_run_id=None, updated_at=datetime.now(UTC))
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="awap-scheduler")
        self._scheduler_thread.start()

    def list_node_types(self) -> list[NodeTypeDefinition]:
        return sorted(self._node_catalog.values(), key=lambda item: item.display_name)

    def list_providers(self) -> list[ProviderDefinition]:
        return self._provider_registry.list_definitions()

    def create_user(self, request: UserCreateRequest) -> UserWithToken:
        return self._repository.create_user(request)

    def list_users(self) -> list[UserDefinition]:
        return self._repository.list_users()

    def create_credential(self, request: CredentialCreateRequest, *, created_by: str | None = None) -> CredentialDefinition:
        return self._repository.create_credential(request, created_by=created_by)

    def list_credentials(self) -> list[CredentialDefinition]:
        return self._repository.list_credentials()

    def get_credential(self, credential_id: str) -> CredentialDefinition | None:
        return self._repository.get_credential(credential_id)

    def create_knowledge_base(self, request: KnowledgeBaseCreateRequest, *, created_by: str | None = None) -> KnowledgeBaseDefinition:
        return self._repository.create_knowledge_base(request, created_by=created_by)

    def list_knowledge_bases(self) -> list[KnowledgeBaseDefinition]:
        return self._repository.list_knowledge_bases()

    def create_knowledge_document(self, request: KnowledgeDocumentCreateRequest, *, created_by: str | None = None) -> KnowledgeDocumentDefinition:
        return self._repository.create_knowledge_document(request, created_by=created_by)

    def list_knowledge_documents(
        self,
        knowledge_base_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[KnowledgeDocumentDefinition]:
        return self._repository.list_knowledge_documents(
            knowledge_base_id,
            limit=limit,
            offset=offset,
        )

    def search_knowledge(
        self,
        knowledge_base_id: str,
        query: str,
        *,
        top_k: int = 5,
        offset: int = 0,
    ) -> KnowledgeSearchResult:
        return KnowledgeSearchResult(
            knowledge_base_id=knowledge_base_id,
            query=query,
            chunks=self._repository.search_knowledge(
                knowledge_base_id,
                query,
                top_k=top_k,
                offset=offset,
            ),
        )

    def list_approval_tasks(
        self,
        run_id: str | None = None,
        decision: ApprovalDecision | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ApprovalTaskDefinition]:
        return self._repository.list_approval_tasks(
            run_id=run_id,
            decision=decision,
            limit=limit,
            offset=offset,
        )

    def decide_approval_task(
        self,
        approval_task_id: str,
        request: ApprovalDecisionRequest,
        *,
        decided_by: str | None = None,
    ) -> ApprovalTaskDefinition:
        task = self._repository.decide_approval_task(approval_task_id, request, decided_by=decided_by)
        if task is None:
            raise KeyError(approval_task_id)
        run = self._repository.get_run(task.run_id)
        if run is not None:
            execution_state = dict(self._materialize_stored_payload(run.execution_state or {}))
            context = dict(execution_state.get("context") or {})
            approvals = dict(context.get("approvals") or {})
            approvals[task.node_id] = {
                "decision": request.decision.value,
                "comment": request.comment,
                "payload": request.payload,
            }
            context["approvals"] = approvals
            execution_state["context"] = context
            self._repository.update_run_execution_state(
                task.run_id,
                self._prepare_stored_payload(
                    execution_state,
                    run_id=task.run_id,
                    label="run.execution_state",
                ),
            )
        if run is not None and run.status == WorkflowRunStatus.waiting_human:
            self._repository.resume_run(task.run_id)
        self._append_audit_log("approval.decided", actor_id=decided_by, workflow_id=task.workflow_id, workflow_version=task.workflow_version, run_id=task.run_id, payload={"approval_task_id": task.id, "decision": request.decision.value})
        return task

    def create_prompt_template(self, request: PromptTemplateCreateRequest, *, created_by: str | None = None) -> PromptTemplateDefinition:
        return self._repository.create_prompt_template(request, created_by=created_by)

    def list_prompt_templates(self, name: str | None = None) -> list[PromptTemplateDefinition]:
        return self._repository.list_prompt_templates(name=name)

    def get_prompt_template(self, prompt_template_id: str) -> PromptTemplateDefinition | None:
        return self._repository.get_prompt_template(prompt_template_id)

    def create_evaluation_run(self, request: EvaluationRunCreateRequest, *, created_by: str | None = None) -> EvaluationRunDefinition:
        llm_provider = self._provider_registry.get_llm_provider(request.provider_key)
        results: list[EvaluationCaseResult] = []
        for index, case in enumerate(request.test_cases, start=1):
            context = self._runtime.create_context(
                WorkflowDefinition(name=request.name, nodes=[]),
                case.input_payload,
            )
            prompt = self._runtime._render_template(request.prompt_template, context)  # noqa: SLF001
            if request.knowledge_base_id:
                retrieval = self.search_knowledge(request.knowledge_base_id, prompt, top_k=3)
                prompt = f"{prompt}\n\nContext:\n" + "\n".join(chunk.content for chunk in retrieval.chunks)
            output = llm_provider.generate(
                model=request.model,
                prompt=prompt,
                credential=None,
                context=context,
                config={"mock_response": request.mock_response} if request.mock_response is not None else {},
            )
            results.append(score_evaluation_case(index, case, output["response"]))

        passed_cases = sum(1 for result in results if result.passed)
        average_score = sum(result.score for result in results) / len(results) if results else 0.0
        evaluation = EvaluationRunDefinition(
            name=request.name,
            prompt_template=request.prompt_template,
            model=request.model,
            provider_key=request.provider_key,
            knowledge_base_id=request.knowledge_base_id,
            status=EvaluationStatus.completed,
            total_cases=len(results),
            passed_cases=passed_cases,
            average_score=average_score,
            results=results,
            created_at=datetime.now(UTC),
            created_by=created_by,
        )
        return self._repository.create_evaluation_run(evaluation)

    def list_evaluation_runs(self) -> list[EvaluationRunDefinition]:
        return self._repository.list_evaluation_runs()

    def get_evaluation_run(self, evaluation_run_id: str) -> EvaluationRunDefinition | None:
        return self._repository.get_evaluation_run(evaluation_run_id)

    def create_environment(self, request: WorkflowEnvironmentCreateRequest) -> WorkflowEnvironmentDefinition:
        return self._repository.create_environment(request)

    def list_environments(self) -> list[WorkflowEnvironmentDefinition]:
        return self._repository.list_environments()

    def list_environment_releases(
        self,
        environment: str | None = None,
        workflow_id: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowEnvironmentReleaseDefinition]:
        return self._repository.list_environment_releases(
            environment=environment,
            workflow_id=workflow_id,
            limit=limit,
            offset=offset,
        )

    def get_workflow_readiness(
        self,
        workflow_id: str,
        version: int,
        *,
        environment: str | None = None,
    ) -> WorkflowReadinessReport:
        workflow = self._require_workflow(workflow_id, version)
        checks: list[WorkflowReadinessCheck] = []

        validation = self._validator.validate(workflow)
        checks.append(
            WorkflowReadinessCheck(
                name="graph_validation",
                passed=validation.valid,
                message="Workflow graph is valid." if validation.valid else "; ".join(validation.errors),
            )
        )

        target_environment = None
        if environment:
            target_environment = self._repository.get_environment(environment)
            checks.append(
                WorkflowReadinessCheck(
                    name="environment_exists",
                    passed=target_environment is not None,
                    message=f"Environment '{environment}' exists."
                    if target_environment is not None
                    else f"Environment '{environment}' does not exist.",
                )
            )

        if workflow.version > 1:
            checks.append(
                WorkflowReadinessCheck(
                    name="release_notes",
                    passed=bool(workflow.release_notes.strip()),
                    message="Release notes are present."
                    if workflow.release_notes.strip()
                    else "Release notes are required for versioned deployment review.",
                    severity=RunEventLevel.warning,
                )
            )

        provider_keys = {provider.key for provider in self._provider_registry.list_definitions()}
        for node in workflow.nodes:
            provider_key = node.config.get("provider")
            if provider_key:
                checks.append(
                    WorkflowReadinessCheck(
                        name=f"provider:{node.id}",
                        passed=provider_key in provider_keys,
                        message=f"Provider '{provider_key}' is registered for node '{node.id}'."
                        if provider_key in provider_keys
                        else f"Provider '{provider_key}' is not registered for node '{node.id}'.",
                    )
                )

            credential_id = node.config.get("credential_id")
            if credential_id:
                credential = self._repository.get_credential_secret(
                    credential_id,
                    environment=environment,
                    workflow_id=workflow.id,
                )
                checks.append(
                    WorkflowReadinessCheck(
                        name=f"credential:{node.id}",
                        passed=credential is not None,
                        message=f"Credential for node '{node.id}' is available in this scope."
                        if credential is not None
                        else f"Credential for node '{node.id}' is missing or not scoped to this workflow/environment.",
                    )
                )
            elif target_environment is not None and target_environment.policy.require_scoped_credentials and node.type in {
                "llm_prompt",
                "ai_agent",
                "http_request",
            }:
                checks.append(
                    WorkflowReadinessCheck(
                        name=f"credential_required:{node.id}",
                        passed=False,
                        message=f"Node '{node.id}' requires an environment-scoped credential.",
                    )
                )

            if node.type == "knowledge_retrieval":
                knowledge_base_id = str(node.config.get("knowledge_base_id") or "")
                knowledge_base = (
                    self._repository.get_knowledge_base(knowledge_base_id) if knowledge_base_id else None
                )
                checks.append(
                    WorkflowReadinessCheck(
                        name=f"knowledge_base:{node.id}",
                        passed=knowledge_base is not None,
                        message=f"Knowledge base for node '{node.id}' exists."
                        if knowledge_base is not None
                        else f"Node '{node.id}' references a missing knowledge base.",
                    )
                )

            if node.type in {"sub_workflow", "for_each"}:
                referenced_workflow_id = str(node.config.get("workflow_id") or "")
                referenced = (
                    self._repository.get(referenced_workflow_id, node.config.get("version"))
                    if referenced_workflow_id
                    else None
                )
                checks.append(
                    WorkflowReadinessCheck(
                        name=f"subworkflow:{node.id}",
                        passed=referenced is not None,
                        message=f"Referenced workflow for node '{node.id}' exists."
                        if referenced is not None
                        else f"Node '{node.id}' references a missing workflow.",
                    )
                )

            if target_environment is not None:
                checks.extend(self._check_environment_policy(workflow.id, node, target_environment))

        ready = all(check.passed or check.severity is RunEventLevel.warning for check in checks)
        return WorkflowReadinessReport(
            workflow_id=workflow.id,
            version=workflow.version,
            environment=environment,
            ready=ready,
            checks=checks,
        )

    def promote_workflow(self, workflow_id: str, request: WorkflowPromotionRequest, *, promoted_by: str | None = None) -> WorkflowEnvironmentReleaseDefinition:
        workflow = self._require_workflow(workflow_id, request.version)
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise ValueError("Cannot promote an invalid workflow version.")
        if self._repository.get_environment(request.environment) is None:
            raise KeyError(request.environment)
        readiness = self.get_workflow_readiness(
            workflow_id,
            request.version,
            environment=request.environment,
        )
        if not readiness.ready:
            blockers = [check.message for check in readiness.checks if not check.passed]
            raise ValueError("Workflow is not ready for promotion: " + "; ".join(blockers))
        release = self._repository.create_environment_release(request.environment, workflow_id, request.version, promoted_by=promoted_by)
        self._append_audit_log("workflow.promoted", actor_id=promoted_by, workflow_id=workflow_id, workflow_version=request.version, payload={"environment": request.environment})
        return release

    def search_runs(
        self,
        *,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        environment: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        return self._repository.search_runs(
            workflow_id=workflow_id,
            status=status,
            environment=environment,
            limit=limit,
            offset=offset,
        )

    def get_observability_summary(self) -> ObservabilitySummary:
        return self._repository.build_observability_summary()

    def get_monitoring_alerts(self) -> list[MonitoringAlert]:
        alerts: list[MonitoringAlert] = []
        failed_runs = self._repository.search_runs(status=WorkflowRunStatus.failed, limit=10)
        for run in failed_runs:
            alerts.append(
                MonitoringAlert(
                    severity=RunEventLevel.error,
                    title="Workflow run failed",
                    message=run.error_message or "A workflow run failed without an error message.",
                    run_id=run.id,
                    workflow_id=run.workflow_id,
                )
            )
        for dead_letter in self._repository.list_dead_letters(limit=10):
            alerts.append(
                MonitoringAlert(
                    severity=RunEventLevel.error,
                    title="Dead letter captured",
                    message=dead_letter.error_message,
                    run_id=dead_letter.run_id,
                    workflow_id=dead_letter.workflow_id,
                )
            )
        stale_after = datetime.now(UTC) - timedelta(seconds=60)
        for worker in self._worker_health.values():
            if worker.updated_at < stale_after:
                alerts.append(
                    MonitoringAlert(
                        severity=RunEventLevel.warning,
                        title="Worker heartbeat is stale",
                        message=f"Worker '{worker.worker_id}' has not reported health recently.",
                    )
                )
        return alerts

    def get_infrastructure_status(self) -> InfrastructureStatus:
        return InfrastructureStatus(
            queue_backend=self._run_queue.backend_name,
            notes=[
                "Runs and leases are durable in the SQL database.",
                "Workers use a queue adapter; local development uses the SQLite lease-table adapter.",
                "A managed queue backend is still required for production-grade distributed execution.",
            ]
        )

    def check_provider_connection(
        self,
        provider_key: str,
        *,
        credential_id: str | None = None,
        environment: str | None = None,
        workflow_id: str | None = None,
    ) -> ProviderConnectionCheck:
        providers = {provider.key: provider for provider in self._provider_registry.list_definitions()}
        provider = providers.get(provider_key)
        if provider is None:
            return ProviderConnectionCheck(
                provider_key=provider_key,
                available=False,
                configured=False,
                message=f"Provider '{provider_key}' is not registered.",
            )
        if provider.kind is not ProviderKind.llm:
            return ProviderConnectionCheck(
                provider_key=provider_key,
                available=True,
                configured=True,
                message=f"Provider '{provider_key}' is available.",
            )
        credential = (
            self._repository.get_credential_secret(
                credential_id,
                environment=environment,
                workflow_id=workflow_id,
            )
            if credential_id
            else None
        )
        configured = bool(
            credential
            and (credential.secret_payload.get("bearer_token") or credential.secret_payload.get("api_key"))
        ) or bool(os.getenv("NVIDIA_API_KEY"))
        return ProviderConnectionCheck(
            provider_key=provider_key,
            available=True,
            configured=configured,
            message="Provider credentials are configured."
            if configured
            else "Provider is registered but no usable credential or environment API key is configured.",
        )

    def list_trigger_states(self) -> list[WorkflowTriggerStateDefinition]:
        return self._repository.list_trigger_states()

    def list_dead_letters(
        self,
        workflow_id: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DeadLetterDefinition]:
        return self._repository.list_dead_letters(workflow_id=workflow_id, limit=limit, offset=offset)

    def list_worker_health(self) -> list[WorkerHealthDefinition]:
        return sorted(self._worker_health.values(), key=lambda item: item.worker_id)

    def get_source_control_status(self) -> SourceControlStatus:
        repo_root = Path(__file__).resolve().parents[2]
        try:
            branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, text=True).strip() or None
            head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip() or None
            changed_files = [
                line.split(maxsplit=1)[1]
                for line in subprocess.check_output(["git", "status", "--short"], cwd=repo_root, text=True).splitlines()
                if line.strip()
            ]
            return SourceControlStatus(branch=branch, head_commit=head_commit, dirty=bool(changed_files), changed_files=changed_files)
        except Exception:
            return SourceControlStatus()

    def list_workflow_templates(self) -> list[WorkflowTemplateDefinition]:
        return BUILTIN_WORKFLOW_TEMPLATES

    def create_workflow_comment(self, request: WorkflowCommentCreateRequest, *, author_id: str) -> WorkflowCommentDefinition:
        comment = self._repository.create_comment(request, author_id=author_id)
        self._append_audit_log("workflow.comment_created", actor_id=author_id, workflow_id=request.workflow_id, workflow_version=request.workflow_version, payload={"comment_id": comment.id})
        return comment

    def list_workflow_comments(
        self,
        workflow_id: str,
        workflow_version: int | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowCommentDefinition]:
        return self._repository.list_comments(
            workflow_id,
            workflow_version,
            limit=limit,
            offset=offset,
        )

    def list_audit_logs(
        self,
        workflow_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        return self._repository.list_audit_logs(
            workflow_id=workflow_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )

    def compare_workflow_versions(self, workflow_id: str, from_version: int, to_version: int) -> WorkflowVersionDiff:
        left = self._require_workflow(workflow_id, from_version)
        right = self._require_workflow(workflow_id, to_version)

        left_nodes = {node.id: node.model_dump() for node in left.nodes}
        right_nodes = {node.id: node.model_dump() for node in right.nodes}
        left_edges = {f"{edge.source}->{edge.target}:{edge.condition_value}:{edge.is_default}:{edge.label}": edge.model_dump() for edge in left.edges}
        right_edges = {f"{edge.source}->{edge.target}:{edge.condition_value}:{edge.is_default}:{edge.label}": edge.model_dump() for edge in right.edges}

        shared_node_ids = set(left_nodes).intersection(right_nodes)
        shared_edge_ids = set(left_edges).intersection(right_edges)
        changed_nodes = sorted(node_id for node_id in shared_node_ids if left_nodes[node_id] != right_nodes[node_id])
        changed_edges = sorted(edge_id for edge_id in shared_edge_ids if left_edges[edge_id] != right_edges[edge_id])
        return WorkflowVersionDiff(
            workflow_id=workflow_id,
            from_version=from_version,
            to_version=to_version,
            added_nodes=sorted(set(right_nodes) - set(left_nodes)),
            removed_nodes=sorted(set(left_nodes) - set(right_nodes)),
            changed_nodes=changed_nodes,
            added_edges=sorted(set(right_edges) - set(left_edges)),
            removed_edges=sorted(set(left_edges) - set(right_edges)),
            changed_edges=changed_edges,
            from_release_notes=left.release_notes,
            to_release_notes=right.release_notes,
        )

    def create_workflow(self, workflow: WorkflowDraftPayload, *, owner_id: str | None = None) -> WorkflowDefinition:
        created = self._repository.save(
            WorkflowDefinition(
                name=workflow.name,
                description=workflow.description,
                release_notes=workflow.release_notes,
                nodes=workflow.nodes,
                edges=workflow.edges,
                settings=workflow.settings,
                owner_id=owner_id,
            )
        )
        self._append_audit_log("workflow.created", actor_id=owner_id, workflow_id=created.id, workflow_version=created.version, payload={"name": created.name})
        return created

    def create_workflow_version(self, workflow_id: str, workflow: WorkflowDraftPayload, *, actor_id: str | None = None) -> WorkflowDefinition:
        if self._repository.get(workflow_id) is None:
            raise KeyError(workflow_id)
        owner_id = self._repository.get(workflow_id).owner_id if self._repository.get(workflow_id) is not None else None
        created = self._repository.save(
            WorkflowDefinition(
                id=workflow_id,
                version=self._repository.get_next_version(workflow_id),
                name=workflow.name,
                description=workflow.description,
                release_notes=workflow.release_notes,
                nodes=workflow.nodes,
                edges=workflow.edges,
                settings=workflow.settings,
                owner_id=owner_id,
            )
        )
        self._append_audit_log("workflow.version_created", actor_id=actor_id, workflow_id=created.id, workflow_version=created.version, payload={"name": created.name})
        return created

    def list_workflow_versions(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowDefinition]:
        versions = self._repository.list_versions(workflow_id, limit=limit, offset=offset)
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
        self._append_audit_log("workflow.published", workflow_id=workflow_id, workflow_version=version, payload={"state": "published"})
        return published

    def list_workflows(self, *, limit: int | None = None, offset: int = 0) -> list[WorkflowDefinition]:
        return self._repository.list(limit=limit, offset=offset)

    def get_workflow(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition | None:
        return self._repository.get(workflow_id, version)

    def validate_workflow(self, workflow_id: str, version: int | None = None) -> WorkflowValidationResult:
        workflow = self._require_workflow(workflow_id, version)
        return self._validator.validate(workflow)

    def build_execution_plan(self, workflow_id: str, version: int | None = None) -> ExecutionPlan:
        workflow = self._require_workflow(workflow_id, version)
        return self._validator.create_execution_plan(workflow)

    def start_workflow_run(self, workflow_id: str, request: WorkflowRunRequest, version: int | None = None) -> WorkflowRun:
        workflow = self._select_execution_workflow(workflow_id, version, request.environment)
        trigger_node_ids = self._resolve_trigger_node_ids(workflow, preferred_types={"manual_trigger", "webhook_trigger"})
        return self._queue_run(workflow, request, trigger_node_ids=trigger_node_ids)

    def trigger_workflow_webhook(self, workflow_id: str, request: WorkflowRunRequest, version: int | None = None) -> WorkflowRun:
        workflow = self._select_execution_workflow(workflow_id, version, request.environment)
        trigger_node_ids = self._resolve_trigger_node_ids(workflow, preferred_types={"webhook_trigger", "manual_trigger"})
        payload = dict(request.input_payload)
        payload.setdefault("trigger", {"type": "webhook"})
        return self._queue_run(workflow, request.model_copy(update={"input_payload": payload}), trigger_node_ids=trigger_node_ids)

    def export_workflow(self, workflow_id: str) -> WorkflowExportBundle:
        versions = list(reversed(self.list_workflow_versions(workflow_id)))
        return WorkflowExportBundle(workflow_id=workflow_id, versions=versions, exported_at=datetime.now(UTC))

    def import_workflow(self, request: WorkflowImportRequest) -> WorkflowDefinition:
        versions = sorted(request.bundle.versions, key=lambda item: item.version)
        if not versions:
            raise ValueError("Import bundle must contain at least one workflow version.")

        first = versions[0]
        first_payload = WorkflowDraftPayload(
            name=request.name_override or first.name,
            description=first.description,
            nodes=first.nodes,
            edges=first.edges,
            settings=first.settings,
        )
        if request.as_new_workflow:
            imported = self.create_workflow(first_payload)
        else:
            imported = self._repository.save(
                WorkflowDefinition(
                    id=request.bundle.workflow_id,
                    version=1,
                    name=first_payload.name,
                    description=first_payload.description,
                    nodes=first_payload.nodes,
                    edges=first_payload.edges,
                    settings=first_payload.settings,
                )
            )

        for version in versions[1:]:
            created = self.create_workflow_version(
                imported.id,
                WorkflowDraftPayload(
                    name=request.name_override or version.name,
                    description=version.description,
                    nodes=version.nodes,
                    edges=version.edges,
                    settings=version.settings,
                ),
            )
            if version.state.value == "published":
                self.publish_workflow(imported.id, created.version)

        if first.state.value == "published":
            self.publish_workflow(imported.id, 1)
        return self.get_workflow(imported.id) or imported

    def _queue_run(self, workflow: WorkflowDefinition, request: WorkflowRunRequest, *, trigger_node_ids: list[str] | None = None) -> WorkflowRun:
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise ValueError("Cannot start a run for an invalid workflow version.")
        if request.environment:
            readiness = self.get_workflow_readiness(
                workflow.id,
                workflow.version,
                environment=request.environment,
            )
            if not readiness.ready:
                blockers = [check.message for check in readiness.checks if not check.passed]
                raise ValueError("Cannot start an environment run: " + "; ".join(blockers))
        if request.idempotency_key:
            existing = self._repository.find_run_by_idempotency_key(workflow.id, request.idempotency_key)
            if existing is not None:
                return existing
        if self._repository.count_active_runs(workflow.id) >= workflow.settings.max_concurrent_runs:
            raise ValueError("Workflow concurrency limit reached.")
        plan = self._validator.create_execution_plan(workflow)
        timeout_seconds = request.timeout_seconds or workflow.settings.run_timeout_seconds
        run_id = str(uuid4())
        stored_input_payload = self._prepare_stored_payload(
            request.input_payload,
            run_id=run_id,
            label="run.input_payload",
        )
        run = self._repository.create_run(
            workflow,
            plan,
            stored_input_payload,
            run_id=run_id,
            environment=request.environment,
            trigger_node_ids=trigger_node_ids,
            idempotency_key=request.idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.queued", message="Workflow run queued.", payload={"input_payload": request.input_payload, "idempotency_key": request.idempotency_key, "environment": request.environment, "trigger_node_ids": trigger_node_ids or []})
        return run

    def pause_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.mark_run_pause_requested(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.pause_requested", message="Pause requested for workflow run.")
        self._append_audit_log("run.pause_requested", workflow_id=run.workflow_id, workflow_version=run.workflow_version, run_id=run.id)
        return run

    def resume_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.resume_run(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.resumed", message="Workflow run resumed.")
        self._append_audit_log("run.resumed", workflow_id=run.workflow_id, workflow_version=run.workflow_version, run_id=run.id)
        return run

    def cancel_workflow_run(self, run_id: str) -> WorkflowRun:
        run = self._repository.mark_run_cancel_requested(run_id)
        if run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.cancel_requested", message="Cancel requested for workflow run.")
        self._append_audit_log("run.cancel_requested", workflow_id=run.workflow_id, workflow_version=run.workflow_version, run_id=run.id)
        return run

    def retry_workflow_run(self, run_id: str, *, from_failed_step: bool = False) -> WorkflowRun:
        source_run = self._repository.get_run(run_id)
        if source_run is None:
            raise KeyError(run_id)
        workflow = self._require_workflow(source_run.workflow_id, source_run.workflow_version)
        plan = self._validator.create_execution_plan(workflow)
        resume_from_step_index: int | None = None
        if from_failed_step:
            failed_step = next((step for step in source_run.steps if step.status in {WorkflowRunStepStatus.failed, WorkflowRunStepStatus.cancelled}), None)
            if failed_step is None:
                raise ValueError("Run has no failed or cancelled step to retry from.")
            resume_from_step_index = failed_step.index
        retry_run_id = str(uuid4())
        source_input_payload = self._materialize_stored_payload(source_run.input_payload)
        stored_input_payload = self._prepare_stored_payload(
            source_input_payload,
            run_id=retry_run_id,
            label="run.input_payload",
        )
        run = self._repository.create_run(
            workflow,
            plan,
            stored_input_payload,
            run_id=retry_run_id,
            environment=source_run.environment,
            trigger_node_ids=source_run.trigger_node_ids,
            retry_of_run_id=source_run.id,
            resume_from_step_index=resume_from_step_index,
            timeout_seconds=source_run.timeout_seconds,
        )
        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.queued", message="Retry workflow run queued.", payload={"retry_of_run_id": source_run.id, "resume_from_step_index": resume_from_step_index})
        self._append_audit_log("run.retried", workflow_id=run.workflow_id, workflow_version=run.workflow_version, run_id=run.id, payload={"retry_of_run_id": source_run.id, "resume_from_step_index": resume_from_step_index})
        return run

    def list_workflow_runs(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        if self._repository.get(workflow_id) is None:
            raise KeyError(workflow_id)
        return self._repository.list_runs(workflow_id, limit=limit, offset=offset)

    def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return self._repository.get_run(run_id)

    def list_workflow_run_events(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRunEvent]:
        run = self._repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return self._repository.list_run_events(run_id, limit=limit, offset=offset)

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=1)
        for thread in self._worker_threads:
            thread.join(timeout=1)

    def _worker_loop(self, worker_id: str) -> None:
        while not self._stop_event.is_set():
            self._worker_health[worker_id] = self._worker_health[worker_id].model_copy(update={"leased_run_id": None, "updated_at": datetime.now(UTC)})
            run = self._run_queue.claim_next_run(worker_id, lease_seconds=30)
            if run is None:
                time.sleep(0.1)
                continue
            try:
                self._worker_health[worker_id] = self._worker_health[worker_id].model_copy(update={"leased_run_id": run.id, "updated_at": datetime.now(UTC)})
                self._execute_workflow_run(run, worker_id)
            except Exception:
                time.sleep(0.1)
            finally:
                self._worker_health[worker_id] = self._worker_health[worker_id].model_copy(update={"leased_run_id": None, "updated_at": datetime.now(UTC)})

    def _scheduler_loop(self) -> None:
        scheduler_id = "awap-scheduler"
        while not self._stop_event.is_set():
            now = utc_now()
            self._worker_health[scheduler_id] = self._worker_health[scheduler_id].model_copy(update={"leased_run_id": None, "updated_at": now})
            try:
                for workflow_stub in self._repository.list():
                    workflow = self._select_execution_workflow(workflow_stub.id, None, None)
                    schedule_nodes = [node for node in workflow.nodes if node.type == "schedule_trigger" and node.config.get("cron")]
                    if not schedule_nodes:
                        continue
                    current_bucket = schedule_bucket(now)
                    for node in schedule_nodes:
                        if not cron_matches(str(node.config["cron"]), now):
                            continue
                        trigger_state = self._repository.get_trigger_state(workflow.id, workflow.version, node.id)
                        if trigger_state is not None and trigger_state.last_trigger_bucket == current_bucket:
                            continue
                        try:
                            run = self._queue_run(
                                workflow,
                                WorkflowRunRequest(
                                    input_payload={
                                        "trigger": {
                                            "type": "schedule",
                                            "node_id": node.id,
                                            "cron": node.config["cron"],
                                            "scheduled_at": now.isoformat(),
                                        }
                                    }
                                ),
                                trigger_node_ids=[node.id],
                            )
                            self._repository.upsert_trigger_state(workflow.id, workflow.version, node.id, last_trigger_bucket=current_bucket, last_run_id=run.id)
                        except ValueError:
                            continue
            except Exception:
                pass
            time.sleep(1.0)

    def _execute_workflow_run(self, run: WorkflowRun, worker_id: str) -> None:
        run_input_payload = self._materialize_stored_payload(run.input_payload)
        workflow = self._require_workflow(run.workflow_id, run.workflow_version)
        environment = self._repository.get_environment(run.environment) if run.environment else None
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

        if run.execution_state:
            state = self._materialize_stored_payload(run.execution_state)
            queue = deque(state.get("queue", []))
            enqueued = set(state.get("enqueued", []))
            executed = set(state.get("executed", []))
            join_tokens = {key: int(value) for key, value in state.get("join_tokens", {}).items()}
            step_results = self._step_results_from_run(run)
            attempts = {key: int(value) for key, value in state.get("attempts", {}).items()}
            context = state.get("context") or self._runtime.create_context(workflow, run_input_payload, environment)
        else:
            queue = deque()
            enqueued: set[str] = set()
            executed: set[str] = set()
            join_tokens: dict[str, int] = {}
            step_results: list[dict[str, Any]] = []
            attempts: dict[str, int] = {}
            context = self._runtime.create_context(workflow, run_input_payload, environment)
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
                    source_output_payload = self._materialize_stored_payload(source_step.output_payload)
                    node = node_map.get(source_step.node_id)
                    if node is None:
                        continue
                    self._runtime.update_context(context, node, source_output_payload)
                    self._repository.mark_step_copied(
                        run.id,
                        source_step.index,
                        self._prepare_stored_payload(
                            source_output_payload,
                            run_id=run.id,
                            label=f"steps.{source_step.index}.output_payload",
                        ),
                    )
                    executed.add(source_step.node_id)
                    step_results.append({"step_index": source_step.index, "node_id": source_step.node_id, "output": source_output_payload, "copied": True})
                restart_step = next((step for step in plan.steps if step.index == run.resume_from_step_index), None)
                if restart_step is not None:
                    queue.append(restart_step.node_id)
                    enqueued.add(restart_step.node_id)
            else:
                trigger_root_ids = run.trigger_node_ids or [
                    node.id
                    for node in workflow.nodes
                    if self._node_catalog.get(node.type) is not None
                    and self._node_catalog[node.type].category == NodeCategory.trigger
                ]
                root_candidates = trigger_root_ids or [node_id for node_id, degree in indegree.items() if degree == 0]
                root_node_ids = sorted(root_candidates, key=lambda item: step_by_node_id[item].index)
                queue.extend(root_node_ids)
                enqueued.update(root_node_ids)

        self._emit_event(run_id=run.id, workflow=workflow, event_type="run.started", message="Workflow run started.")
        self._save_execution_state(run.id, queue, enqueued, executed, join_tokens, step_results, context, attempts)

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
                    self._repository.get_credential_secret(
                        credential_id,
                        environment=run.environment,
                        workflow_id=workflow.id,
                    )
                    if credential_id is not None
                    else None
                )
                self._emit_event(run_id=run.id, workflow=workflow, event_type="step.started", message=f"Step {step.index} started.", provider_key=provider_key, step_index=step.index, payload={"node_id": step.node_id, "node_type": step.node_type})

                try:
                    output_payload = self._runtime.execute_node(node, context, credential, invoke_subworkflow=self._invoke_subworkflow, step_index=step.index)
                except ApprovalRequiredError as approval:
                    task = self._repository.get_pending_approval_for_run_step(run.id, step.index)
                    if task is None:
                        task = self._repository.create_approval_task(
                            ApprovalTaskDefinition(
                                run_id=run.id,
                                workflow_id=workflow.id,
                                workflow_version=workflow.version,
                                step_index=step.index,
                                node_id=approval.node_id,
                                title=approval.title,
                                prompt=approval.prompt,
                                created_at=datetime.now(UTC),
                            )
                        )
                    self._repository.mark_step_waiting_human(run.id, step.index, "Waiting for human approval.")
                    self._repository.mark_run_waiting_human(run.id)
                    queue.appendleft(node_id)
                    enqueued.add(node_id)
                    self._save_execution_state(run.id, queue, enqueued, executed, join_tokens, step_results, context, attempts)
                    self._emit_event(
                        run_id=run.id,
                        workflow=workflow,
                        event_type="step.waiting_human",
                        message=f"Step {step.index} is waiting for human approval.",
                        step_index=step.index,
                        payload=task.model_dump(mode="json"),
                    )
                    return
                except Exception as error:
                    attempt, output_payload = self._handle_retryable_failure(
                        run=run,
                        workflow=workflow,
                        node=node,
                        step_index=step.index,
                        queue=queue,
                        enqueued=enqueued,
                        executed=executed,
                        join_tokens=join_tokens,
                        step_results=step_results,
                        context=context,
                        attempts=attempts,
                        error=error,
                    )
                    if output_payload is None:
                        continue
                    attempts[node_id] = attempt
                else:
                    attempts[node_id] = 0

                self._runtime.update_context(context, node, output_payload)
                stored_output_payload = self._prepare_stored_payload(
                    output_payload,
                    run_id=run.id,
                    label=f"steps.{step.index}.output_payload",
                )
                self._repository.mark_step_succeeded(run.id, step.index, stored_output_payload)
                executed.add(node_id)
                step_results.append({"step_index": step.index, "node_id": step.node_id, "output": output_payload})
                self._emit_event(run_id=run.id, workflow=workflow, event_type="step.succeeded", message=f"Step {step.index} succeeded.", provider_key=provider_key, step_index=step.index, payload={"node_id": step.node_id, "output": output_payload})

                selected_edges = self._runtime.select_edges(node, output_payload, outgoing_map[node_id])
                for edge in selected_edges:
                    target_node = node_map[edge.target]
                    self._runtime.record_join_inputs(context, edge.target, node_id, output_payload)
                    if target_node.type == "join":
                        join_tokens[edge.target] = join_tokens.get(edge.target, 0) + 1
                        required_inputs = int(target_node.config.get("required_inputs", len(incoming_map[edge.target])))
                        if join_tokens[edge.target] >= required_inputs and edge.target not in executed and edge.target not in enqueued:
                            queue.append(edge.target)
                            enqueued.add(edge.target)
                        continue
                    if edge.target not in executed and edge.target not in enqueued:
                        queue.append(edge.target)
                        enqueued.add(edge.target)

                self._save_execution_state(run.id, queue, enqueued, executed, join_tokens, step_results, context, attempts)

            self._mark_remaining_steps(run.id, WorkflowRunStepStatus.skipped, "Step was not activated by the selected execution path.")
            result_payload = {"steps": step_results, "last_output": context["last"]}
            stored_result_payload = self._prepare_stored_payload(
                result_payload,
                run_id=run.id,
                label="run.result_payload",
            )
            self._repository.mark_run_succeeded(run.id, stored_result_payload)
            self._emit_event(run_id=run.id, workflow=workflow, event_type="run.succeeded", message="Workflow run succeeded.", payload=result_payload)
        except Exception as error:
            error_message = str(error)
            failed_run = self._repository.get_run(run.id)
            if failed_run is not None:
                failed_step = next((step for step in failed_run.steps if step.status == WorkflowRunStepStatus.running), None)
                if failed_step is not None:
                    self._repository.mark_step_failed(run.id, failed_step.index, error_message)
                    self._emit_event(run_id=run.id, workflow=workflow, event_type="step.failed", message=f"Step {failed_step.index} failed.", level=RunEventLevel.error, step_index=failed_step.index, payload={"error_message": error_message, "node_id": failed_step.node_id})
                    self._repository.create_dead_letter(
                        DeadLetterDefinition(
                            run_id=run.id,
                            workflow_id=workflow.id,
                            workflow_version=workflow.version,
                            step_index=failed_step.index,
                            node_id=failed_step.node_id,
                            environment=run.environment,
                            error_message=error_message,
                            payload=self._prepare_stored_payload(
                                {"input_payload": run_input_payload},
                                run_id=run.id,
                                label="dead_letter.payload",
                            ),
                            created_at=datetime.now(UTC),
                        )
                    )
            self._repository.mark_run_failed(run.id, error_message)
            self._emit_event(run_id=run.id, workflow=workflow, event_type="run.failed", message="Workflow run failed.", level=RunEventLevel.error, payload={"error_message": error_message})

    def _check_run_control(self, run_id: str, workflow: WorkflowDefinition, worker_id: str) -> str:
        current_run = self._repository.get_run(run_id)
        if current_run is None:
            return "missing"
        if current_run.timeout_seconds and current_run.started_at:
            elapsed = datetime.now(UTC) - current_run.started_at
            if elapsed > timedelta(seconds=current_run.timeout_seconds):
                self._repository.mark_run_failed(run_id, "Workflow run timed out.")
                self._emit_event(run_id=run_id, workflow=workflow, event_type="run.failed", message="Workflow run timed out.", level=RunEventLevel.error, payload={"timeout_seconds": current_run.timeout_seconds})
                return "cancelled"
        if current_run.status == WorkflowRunStatus.cancelling:
            self._mark_remaining_steps(run_id, WorkflowRunStepStatus.cancelled, "Run cancelled.")
            self._repository.mark_run_cancelled(run_id, "Workflow run cancelled by operator.")
            self._emit_event(run_id=run_id, workflow=workflow, event_type="run.cancelled", message="Workflow run cancelled.")
            return "cancelled"
        if current_run.status in {WorkflowRunStatus.pause_requested, WorkflowRunStatus.paused, WorkflowRunStatus.waiting_human}:
            pause_event_type = "run.waiting_human" if current_run.status == WorkflowRunStatus.waiting_human else "run.paused"
            if current_run.status == WorkflowRunStatus.waiting_human:
                self._repository.mark_run_waiting_human(run_id)
            else:
                self._repository.mark_run_paused(run_id, worker_id)
            self._emit_event(run_id=run_id, workflow=workflow, event_type=pause_event_type, message="Workflow run is waiting for resume.")
            while not self._stop_event.is_set():
                time.sleep(0.1)
                current_run = self._repository.get_run(run_id)
                if current_run is None:
                    return "missing"
                if current_run.status == WorkflowRunStatus.cancelling:
                    self._mark_remaining_steps(run_id, WorkflowRunStepStatus.cancelled, "Run cancelled.")
                    self._repository.mark_run_cancelled(run_id, "Workflow run cancelled while paused.")
                    self._emit_event(run_id=run_id, workflow=workflow, event_type="run.cancelled", message="Workflow run cancelled.")
                    return "cancelled"
                if current_run.status == WorkflowRunStatus.queued:
                    self._repository.mark_run_running(run_id, worker_id)
                    return "continue"
            return "cancelled"
        return "continue"

    def _save_execution_state(self, run_id: str, queue: deque[str], enqueued: set[str], executed: set[str], join_tokens: dict[str, int], step_results: list[dict[str, Any]], context: dict[str, Any], attempts: dict[str, int]) -> None:
        execution_state = {
            "queue": list(queue),
            "enqueued": sorted(enqueued),
            "executed": sorted(executed),
            "join_tokens": join_tokens,
            "step_result_count": len(step_results),
            "context": context,
            "attempts": attempts,
        }
        self._repository.update_run_execution_state(
            run_id,
            self._prepare_stored_payload(
                execution_state,
                run_id=run_id,
                label="run.execution_state",
            ),
        )

    def _step_results_from_run(self, run: WorkflowRun) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for step in sorted(run.steps, key=lambda item: item.index):
            if step.output_payload is None:
                continue
            if step.status not in {
                WorkflowRunStepStatus.succeeded,
                WorkflowRunStepStatus.copied,
            }:
                continue
            output_payload = self._materialize_stored_payload(step.output_payload)
            results.append(
                {
                    "step_index": step.index,
                    "node_id": step.node_id,
                    "output": output_payload,
                    "copied": step.status == WorkflowRunStepStatus.copied,
                }
            )
        return results

    def _mark_remaining_steps(self, run_id: str, status: WorkflowRunStepStatus, message: str) -> None:
        current_run = self._repository.get_run(run_id)
        if current_run is None:
            return
        for step in current_run.steps:
            if step.status == WorkflowRunStepStatus.pending:
                if status == WorkflowRunStepStatus.cancelled:
                    self._repository.mark_step_cancelled(run_id, step.index, message)
                elif status == WorkflowRunStepStatus.skipped:
                    self._repository.mark_step_skipped(run_id, step.index, message)

    def _invoke_subworkflow(self, workflow_id: str, version: int | None, input_payload: dict[str, Any]) -> dict[str, Any]:
        workflow = self._require_workflow(workflow_id, version)
        validation = self._validator.validate(workflow)
        if not validation.valid:
            raise RuntimeError("Cannot execute an invalid sub-workflow.")
        return self._execute_inline_workflow(workflow, input_payload)

    def _handle_retryable_failure(
        self,
        *,
        run: WorkflowRun,
        workflow: WorkflowDefinition,
        node: Any,
        step_index: int,
        queue: deque[str],
        enqueued: set[str],
        executed: set[str],
        join_tokens: dict[str, int],
        step_results: list[dict[str, Any]],
        context: dict[str, Any],
        attempts: dict[str, int],
        error: Exception,
    ) -> tuple[int, dict[str, Any] | None]:
        policy = node.config.get("retry_policy") or {}
        max_attempts = max(1, int(policy.get("max_attempts", 1)))
        current_attempt = int(attempts.get(node.id, 0)) + 1
        if current_attempt < max_attempts:
            base_delay = float(policy.get("backoff_seconds", 0.0))
            multiplier = float(policy.get("backoff_multiplier", 2.0))
            delay_seconds = base_delay * (multiplier ** max(current_attempt - 1, 0))
            attempts[node.id] = current_attempt
            queue.appendleft(node.id)
            enqueued.add(node.id)
            self._save_execution_state(run.id, queue, enqueued, executed, join_tokens, step_results, context, attempts)
            self._emit_event(
                run_id=run.id,
                workflow=workflow,
                event_type="step.retry_scheduled",
                message=f"Step {step_index} scheduled for retry.",
                level=RunEventLevel.warning,
                step_index=step_index,
                payload={"attempt": current_attempt, "next_attempt": current_attempt + 1, "delay_seconds": delay_seconds, "error_message": str(error), "node_id": node.id},
            )
            if delay_seconds > 0:
                time.sleep(min(delay_seconds, 2.0))
            return current_attempt, None

        fallback_output = policy.get("fallback_output")
        if fallback_output is not None:
            rendered = self._runtime._render_config(fallback_output, context)  # noqa: SLF001
            output = rendered if isinstance(rendered, dict) else {"response": rendered}
            output["fallback_applied"] = True
            output["error_message"] = str(error)
            return current_attempt, output
        raise error

    def _execute_inline_workflow(self, workflow: WorkflowDefinition, input_payload: dict[str, Any]) -> dict[str, Any]:
        node_map = {node.id: node for node in workflow.nodes}
        outgoing_map: dict[str, list[WorkflowEdge]] = {node.id: [] for node in workflow.nodes}
        incoming_count = {node.id: 0 for node in workflow.nodes}
        for edge in workflow.edges:
            outgoing_map[edge.source].append(edge)
            incoming_count[edge.target] += 1

        trigger_root_ids = [node.id for node in workflow.nodes if self._node_catalog.get(node.type) is not None and self._node_catalog[node.type].category == NodeCategory.trigger]
        queue = deque(sorted(trigger_root_ids or (node_id for node_id, count in incoming_count.items() if count == 0)))
        enqueued = set(queue)
        executed: set[str] = set()
        join_tokens: dict[str, int] = {}
        join_inputs: dict[str, int] = {node.id: len([edge for edge in workflow.edges if edge.target == node.id]) for node in workflow.nodes if node.type == "join"}
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
                self._repository.get_credential_secret(credential_id, workflow_id=workflow.id)
                if credential_id is not None
                else None
            )
            output_payload = self._runtime.execute_node(node, context, credential, invoke_subworkflow=self._invoke_subworkflow)
            self._runtime.update_context(context, node, output_payload)
            executed.add(node_id)
            step_results.append({"node_id": node_id, "output": output_payload})
            for edge in self._runtime.select_edges(node, output_payload, outgoing_map[node_id]):
                target_node = node_map[edge.target]
                self._runtime.record_join_inputs(context, edge.target, node_id, output_payload)
                if target_node.type == "join":
                    join_tokens[edge.target] = join_tokens.get(edge.target, 0) + 1
                    if join_tokens[edge.target] >= int(target_node.config.get("required_inputs", join_inputs[edge.target])) and edge.target not in executed and edge.target not in enqueued:
                        queue.append(edge.target)
                        enqueued.add(edge.target)
                    continue
                if edge.target not in executed and edge.target not in enqueued:
                    queue.append(edge.target)
                    enqueued.add(edge.target)

        return {"steps": step_results, "last_output": context["last"]}

    def _require_workflow(self, workflow_id: str, version: int | None = None) -> WorkflowDefinition:
        workflow = self._repository.get(workflow_id, version)
        if workflow is None:
            raise KeyError(workflow_id)
        return workflow

    def _select_execution_workflow(self, workflow_id: str, version: int | None, environment: str | None = None) -> WorkflowDefinition:
        if version is not None:
            return self._require_workflow(workflow_id, version)
        if environment is not None:
            release = self._repository.get_environment_release(environment, workflow_id)
            if release is not None:
                return self._require_workflow(workflow_id, release.workflow_version)
        versions = self.list_workflow_versions(workflow_id)
        published = next((workflow for workflow in versions if workflow.state.value == "published"), None)
        return published or versions[0]

    def _resolve_trigger_node_ids(self, workflow: WorkflowDefinition, *, preferred_types: set[str]) -> list[str]:
        matching = [node.id for node in workflow.nodes if node.type in preferred_types]
        if matching:
            return matching
        fallback = [
            node.id
            for node in workflow.nodes
            if self._node_catalog.get(node.type) is not None and self._node_catalog[node.type].category == NodeCategory.trigger and node.type != "schedule_trigger"
        ]
        return fallback

    def _check_environment_policy(
        self,
        workflow_id: str,
        node: Any,
        environment: WorkflowEnvironmentDefinition,
    ) -> list[WorkflowReadinessCheck]:
        checks: list[WorkflowReadinessCheck] = []
        policy = environment.policy
        if node.type == "http_request" and policy.allowed_http_hosts:
            host = urlparse(str(node.config.get("url") or "")).hostname
            checks.append(
                WorkflowReadinessCheck(
                    name=f"http_policy:{node.id}",
                    passed=host in policy.allowed_http_hosts,
                    message=f"HTTP host '{host}' is allowed in environment '{environment.name}'."
                    if host in policy.allowed_http_hosts
                    else f"HTTP host '{host}' is not allowed in environment '{environment.name}'.",
                )
            )
        if node.type == "file_write":
            configured_path = str(node.config.get("path") or "")
            resolved_path = Path(configured_path).expanduser().resolve() if configured_path else None
            allowed_roots = [Path(root).expanduser().resolve() for root in policy.allowed_file_write_roots]
            allowed = (
                resolved_path is not None
                and any(resolved_path == root or root in resolved_path.parents for root in allowed_roots)
            )
            checks.append(
                WorkflowReadinessCheck(
                    name=f"file_policy:{node.id}",
                    passed=allowed,
                    message=f"File write path for node '{node.id}' is inside an allowed root."
                    if allowed
                    else f"File write path for node '{node.id}' is outside allowed roots.",
                )
            )
        if node.type == "sql_query" and policy.allowed_sql_database_paths:
            configured_path = str(node.config.get("database_path") or "")
            resolved_path = str(Path(configured_path).expanduser().resolve()) if configured_path else ""
            allowed_paths = [str(Path(path).expanduser().resolve()) for path in policy.allowed_sql_database_paths]
            checks.append(
                WorkflowReadinessCheck(
                    name=f"sql_policy:{node.id}",
                    passed=resolved_path in allowed_paths,
                    message=f"SQL database path for node '{node.id}' is allowed."
                    if resolved_path in allowed_paths
                    else f"SQL database path for node '{node.id}' is not allowed in environment '{environment.name}'.",
                )
            )
        return checks

    def _prepare_stored_payload(self, payload: Any, *, run_id: str, label: str) -> Any:
        if self._stored_payload_limit_bytes <= 0:
            return payload
        if self._payload_size_bytes(payload) <= self._stored_payload_limit_bytes:
            return payload
        if isinstance(payload, dict) and payload.get(ARTIFACT_REFERENCE_KEY) is True:
            return payload
        if isinstance(payload, dict):
            prepared = {
                key: self._prepare_stored_payload(
                    value,
                    run_id=run_id,
                    label=f"{label}.{key}",
                )
                for key, value in payload.items()
            }
            if self._payload_size_bytes(prepared) <= self._stored_payload_limit_bytes:
                return prepared
        elif isinstance(payload, list):
            prepared = [
                self._prepare_stored_payload(
                    value,
                    run_id=run_id,
                    label=f"{label}.{index}",
                )
                for index, value in enumerate(payload)
            ]
            if self._payload_size_bytes(prepared) <= self._stored_payload_limit_bytes:
                return prepared
        return self._artifact_store.write_json(run_id=run_id, label=label, value=payload)

    def _materialize_stored_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            if payload.get(ARTIFACT_REFERENCE_KEY) is True:
                return self._artifact_store.read_json(payload)
            return {key: self._materialize_stored_payload(value) for key, value in payload.items()}
        if isinstance(payload, list):
            return [self._materialize_stored_payload(value) for value in payload]
        return payload

    def _payload_size_bytes(self, payload: Any) -> int:
        return len(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))

    def _redact_payload(self, payload: Any) -> Any:
        sensitive_keys = {
            "api_key",
            "authorization",
            "bearer_token",
            "body",
            "content",
            "headers",
            "input_payload",
            "output",
            "password",
            "prompt",
            "response",
            "secret",
            "secret_payload",
            "token",
        }
        if isinstance(payload, dict):
            redacted: dict[str, Any] = {}
            for key, value in payload.items():
                if key.lower() in sensitive_keys:
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = self._redact_payload(value)
            return redacted
        if isinstance(payload, list):
            return [self._redact_payload(item) for item in payload]
        return payload

    def _append_audit_log(
        self,
        action: str,
        *,
        actor_id: str | None = None,
        workflow_id: str | None = None,
        workflow_version: int | None = None,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._repository.append_audit_log(
            AuditLogEntry(
                action=action,
                actor_id=actor_id,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                run_id=run_id,
                payload=payload or {},
                created_at=datetime.now(UTC),
            )
        )

    def _emit_event(self, *, run_id: str, workflow: WorkflowDefinition, event_type: str, message: str, level: RunEventLevel = RunEventLevel.info, provider_key: str | None = None, step_index: int | None = None, payload: dict[str, Any] | None = None) -> None:
        redacted_payload = self._redact_payload(payload or {})
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
            payload=self._prepare_stored_payload(
                redacted_payload,
                run_id=run_id,
                label=f"events.{event_type}.payload",
            ),
        )
        self._provider_registry.emit(event)
