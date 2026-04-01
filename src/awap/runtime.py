"""Workflow execution runtime."""

from __future__ import annotations

import re
from typing import Any, Callable

from awap.domain import CredentialSecret, WorkflowDefinition, WorkflowEdge, WorkflowNode
from awap.providers import ProviderRegistry

SubworkflowInvoker = Callable[[str, int | None, dict[str, Any]], dict[str, Any]]


class WorkflowExecutionEngine:
    """Executes workflow nodes using provider abstractions."""

    _template_pattern = re.compile(r"{{\s*([^}]+?)\s*}}")

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry

    def create_context(
        self,
        workflow: WorkflowDefinition,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "workflow": {
                "id": workflow.id,
                "version": workflow.version,
                "name": workflow.name,
            },
            "input": input_payload,
            "steps": {},
            "last": None,
            "join_inputs": {},
        }

    def execute_node(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None = None,
        *,
        invoke_subworkflow: SubworkflowInvoker | None = None,
    ) -> dict[str, Any]:
        handlers = {
            "manual_trigger": self._execute_manual_trigger,
            "schedule_trigger": self._execute_schedule_trigger,
            "llm_prompt": self._execute_llm_prompt,
            "decision": self._execute_decision,
            "join": self._execute_join,
            "sub_workflow": self._execute_sub_workflow,
            "for_each": self._execute_for_each,
            "http_request": self._execute_http_request,
            "notification": self._execute_notification,
        }
        handler = handlers.get(node.type)
        if handler is None:
            raise ValueError(f"No runtime executor is registered for node type '{node.type}'.")
        return handler(node, context, credential, invoke_subworkflow)

    def select_edges(
        self,
        node: WorkflowNode,
        output_payload: dict[str, Any],
        outgoing_edges: list[WorkflowEdge],
    ) -> list[WorkflowEdge]:
        unconditional = [
            edge for edge in outgoing_edges if edge.condition_value is None and not edge.is_default
        ]
        conditional = [edge for edge in outgoing_edges if edge.condition_value is not None]
        default_edges = [edge for edge in outgoing_edges if edge.is_default]
        if not conditional and not default_edges:
            return outgoing_edges

        route = self._normalize_route(output_payload.get("route"))
        matched = [
            edge
            for edge in conditional
            if self._normalize_route(edge.condition_value) == route
        ]
        if matched:
            return [*unconditional, *matched]
        return [*unconditional, *default_edges]

    def update_context(
        self,
        context: dict[str, Any],
        node: WorkflowNode,
        output_payload: dict[str, Any],
    ) -> None:
        context["steps"][node.id] = output_payload
        context["last"] = output_payload

    def record_join_inputs(
        self,
        context: dict[str, Any],
        target_node_id: str,
        source_node_id: str,
        payload: dict[str, Any],
    ) -> None:
        join_inputs = context.setdefault("join_inputs", {})
        node_inputs = join_inputs.setdefault(target_node_id, {})
        node_inputs[source_node_id] = payload

    def _execute_manual_trigger(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential, invoke_subworkflow
        return {
            "triggered": True,
            "trigger_type": node.type,
            "received_input_keys": sorted(context["input"].keys()),
        }

    def _execute_schedule_trigger(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential, invoke_subworkflow
        return {
            "triggered": True,
            "trigger_type": node.type,
            "schedule": node.config.get("cron"),
            "received_input_keys": sorted(context["input"].keys()),
        }

    def _execute_llm_prompt(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del invoke_subworkflow
        prompt = self._render_template(node.config["prompt_template"], context)
        provider_key = node.config.get("provider", "nvidia_build_free_chat")
        provider = self._provider_registry.get_llm_provider(provider_key)
        return provider.generate(
            model=node.config["model"],
            prompt=prompt,
            credential=credential,
            context=context,
            config=node.config,
        )

    def _execute_decision(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential, invoke_subworkflow
        condition_key = node.config.get("condition_key")
        expected_value = node.config.get("equals")
        actual_value = self._resolve_path(condition_key, context) if condition_key else None
        matched = actual_value == expected_value if condition_key else bool(context["last"])
        route = actual_value if condition_key else matched
        return {
            "condition_key": condition_key,
            "expected_value": expected_value,
            "actual_value": actual_value,
            "matched": matched,
            "route": route,
        }

    def _execute_join(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential, invoke_subworkflow
        return {
            "joined": True,
            "inputs": context.get("join_inputs", {}).get(node.id, {}),
        }

    def _execute_sub_workflow(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential
        if invoke_subworkflow is None:
            raise RuntimeError("Sub-workflow execution is not available in this runtime.")
        workflow_input = self._build_subworkflow_input(node.config, context)
        result = invoke_subworkflow(
            str(node.config["workflow_id"]),
            node.config.get("version"),
            workflow_input,
        )
        return {
            "workflow_id": node.config["workflow_id"],
            "workflow_version": node.config.get("version"),
            "input_payload": workflow_input,
            "result": result,
        }

    def _execute_for_each(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del credential
        if invoke_subworkflow is None:
            raise RuntimeError("For-each execution is not available in this runtime.")
        items = self._resolve_path(node.config["items_path"], context)
        if not isinstance(items, list):
            raise RuntimeError("For-each node requires items_path to resolve to a list.")
        item_key = node.config.get("item_key", "item")
        results: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            workflow_input = self._build_subworkflow_input(node.config, context)
            workflow_input[item_key] = item
            workflow_input["index"] = index
            result = invoke_subworkflow(
                str(node.config["workflow_id"]),
                node.config.get("version"),
                workflow_input,
            )
            results.append({"index": index, item_key: item, "result": result})
        return {
            "workflow_id": node.config["workflow_id"],
            "count": len(results),
            "results": results,
        }

    def _execute_http_request(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del invoke_subworkflow
        provider_key = node.config.get("provider", "http_tool")
        provider = self._provider_registry.get_tool_provider(provider_key)
        config = self._render_config(node.config, context)
        return provider.execute(
            node_type=node.type,
            config=config,
            credential=credential,
            context=context,
        )

    def _execute_notification(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
        invoke_subworkflow: SubworkflowInvoker | None,
    ) -> dict[str, Any]:
        del invoke_subworkflow
        provider_key = node.config.get("provider", "notification_tool")
        provider = self._provider_registry.get_tool_provider(provider_key)
        config = self._render_config(node.config, context)
        return provider.execute(
            node_type=node.type,
            config=config,
            credential=credential,
            context=context,
        )

    def _build_subworkflow_input(
        self,
        config: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if "input_mapping" in config and isinstance(config["input_mapping"], dict):
            mapped: dict[str, Any] = {}
            for key, path in config["input_mapping"].items():
                mapped[key] = self._resolve_path(str(path), context)
            return mapped
        base_path = config.get("input_path")
        if base_path:
            resolved = self._resolve_path(str(base_path), context)
            if isinstance(resolved, dict):
                return dict(resolved)
        return dict(context.get("input", {}))

    def _render_config(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self._render_template(value, context)
        if isinstance(value, list):
            return [self._render_config(item, context) for item in value]
        if isinstance(value, dict):
            return {key: self._render_config(item, context) for key, item in value.items()}
        return value

    def _render_template(self, template: str, context: dict[str, Any]) -> str:
        def replacer(match: re.Match[str]) -> str:
            value = self._resolve_path(match.group(1), context)
            return "" if value is None else str(value)

        return self._template_pattern.sub(replacer, template)

    def _resolve_path(self, path: str | None, context: dict[str, Any]) -> Any:
        if not path:
            return None

        current: Any = context
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            return None
        return current

    def _normalize_route(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return str(value).lower()
        text = str(value)
        if text.lower() in {"true", "false"}:
            return text.lower()
        return text
