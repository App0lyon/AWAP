"""Workflow execution runtime."""

from __future__ import annotations

import re
from typing import Any

from awap.domain import CredentialSecret, WorkflowDefinition, WorkflowNode
from awap.providers import ProviderRegistry


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
        }

    def execute_node(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None = None,
    ) -> dict[str, Any]:
        handlers = {
            "manual_trigger": self._execute_manual_trigger,
            "schedule_trigger": self._execute_schedule_trigger,
            "llm_prompt": self._execute_llm_prompt,
            "decision": self._execute_decision,
            "http_request": self._execute_http_request,
            "notification": self._execute_notification,
        }
        handler = handlers.get(node.type)
        if handler is None:
            raise ValueError(f"No runtime executor is registered for node type '{node.type}'.")
        return handler(node, context, credential)

    def update_context(
        self,
        context: dict[str, Any],
        node: WorkflowNode,
        output_payload: dict[str, Any],
    ) -> None:
        context["steps"][node.id] = output_payload
        context["last"] = output_payload

    def _execute_manual_trigger(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
    ) -> dict[str, Any]:
        del credential
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
    ) -> dict[str, Any]:
        del credential
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
    ) -> dict[str, Any]:
        prompt = self._render_template(node.config["prompt_template"], context)
        provider_key = node.config.get("provider", "echo_llm")
        provider = self._provider_registry.get_llm_provider(provider_key)
        return provider.generate(
            model=node.config["model"],
            prompt=prompt,
            credential=credential,
            context=context,
        )

    def _execute_decision(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
    ) -> dict[str, Any]:
        del credential
        condition_key = node.config.get("condition_key")
        expected_value = node.config.get("equals")
        actual_value = self._resolve_path(condition_key, context) if condition_key else None
        matched = actual_value == expected_value if condition_key else bool(context["last"])
        return {
            "condition_key": condition_key,
            "expected_value": expected_value,
            "actual_value": actual_value,
            "matched": matched,
        }

    def _execute_http_request(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
        credential: CredentialSecret | None,
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        provider_key = node.config.get("provider", "notification_tool")
        provider = self._provider_registry.get_tool_provider(provider_key)
        config = self._render_config(node.config, context)
        return provider.execute(
            node_type=node.type,
            config=config,
            credential=credential,
            context=context,
        )

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
