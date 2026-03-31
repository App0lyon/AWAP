"""Provider abstractions for LLMs, tools, credentials, and observability."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol
from urllib import error, request

from awap.domain import (
    CredentialSecret,
    ProviderDefinition,
    ProviderKind,
    RunEventLevel,
    WorkflowRunEvent,
)
from awap.repository import WorkflowRepository

LOGGER = logging.getLogger(__name__)


class LLMProvider(Protocol):
    definition: ProviderDefinition

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        credential: CredentialSecret | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class ToolProvider(Protocol):
    definition: ProviderDefinition

    def execute(
        self,
        *,
        node_type: str,
        config: dict[str, Any],
        credential: CredentialSecret | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class ObservabilityProvider(Protocol):
    definition: ProviderDefinition

    def record(self, event: WorkflowRunEvent) -> None:
        ...


class LocalEchoLLMProvider:
    definition = ProviderDefinition(
        key="echo_llm",
        kind=ProviderKind.llm,
        display_name="Echo LLM",
        description="Local placeholder LLM provider for simulated completions.",
        supported_node_types=["llm_prompt"],
    )

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        credential: CredentialSecret | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": self.definition.key,
            "model": model,
            "prompt": prompt,
            "credential_configured": credential is not None,
            "response": f"[{model}] Simulated completion.",
        }


class HttpToolProvider:
    definition = ProviderDefinition(
        key="http_tool",
        kind=ProviderKind.tool,
        display_name="HTTP Tool",
        description="Tool provider that performs HTTP requests.",
        supported_node_types=["http_request"],
    )

    def execute(
        self,
        *,
        node_type: str,
        config: dict[str, Any],
        credential: CredentialSecret | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        mock_response = config.get("mock_response")
        if mock_response is not None:
            return {
                "provider": self.definition.key,
                "request": {
                    "method": config["method"].upper(),
                    "url": config["url"],
                },
                "response": mock_response,
            }

        headers = dict(config.get("headers", {}))
        if credential is not None:
            secret_payload = credential.secret_payload
            bearer_token = secret_payload.get("bearer_token")
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"
            extra_headers = secret_payload.get("headers")
            if isinstance(extra_headers, dict):
                headers.update(extra_headers)

        method = config["method"].upper()
        url = config["url"]
        body = config.get("body")
        timeout_seconds = float(config.get("timeout_seconds", 10))

        request_data = None
        if body is not None:
            if isinstance(body, str):
                request_data = body.encode("utf-8")
            else:
                request_data = json.dumps(body).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")

        http_request = request.Request(
            url=url,
            data=request_data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                body_text = response.read().decode("utf-8", errors="replace")
                return {
                    "provider": self.definition.key,
                    "request": {"method": method, "url": url},
                    "response": {
                        "status_code": response.status,
                        "headers": dict(response.headers.items()),
                        "body_preview": body_text[:4000],
                    },
                }
        except error.URLError as exc:
            raise RuntimeError(f"HTTP request failed: {exc}") from exc


class NotificationToolProvider:
    definition = ProviderDefinition(
        key="notification_tool",
        kind=ProviderKind.tool,
        display_name="Notification Tool",
        description="Tool provider that emits workflow notifications.",
        supported_node_types=["notification"],
    )

    def execute(
        self,
        *,
        node_type: str,
        config: dict[str, Any],
        credential: CredentialSecret | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": self.definition.key,
            "delivered": True,
            "channel": config["channel"],
            "message": config["message"],
            "credential_configured": credential is not None,
        }


class RepositoryObservabilityProvider:
    definition = ProviderDefinition(
        key="repository_observer",
        kind=ProviderKind.observability,
        display_name="Repository Observer",
        description="Persists structured workflow run events to the platform database.",
    )

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def record(self, event: WorkflowRunEvent) -> None:
        self._repository.append_run_event(
            run_id=event.run_id,
            workflow_id=event.workflow_id,
            workflow_version=event.workflow_version,
            event_type=event.event_type,
            message=event.message,
            level=event.level,
            provider_key=event.provider_key,
            step_index=event.step_index,
            payload=event.payload,
        )


class LoggerObservabilityProvider:
    definition = ProviderDefinition(
        key="logger_observer",
        kind=ProviderKind.observability,
        display_name="Logger Observer",
        description="Writes workflow run events to the application logger.",
    )

    def record(self, event: WorkflowRunEvent) -> None:
        level_map = {
            RunEventLevel.info: logging.INFO,
            RunEventLevel.warning: logging.WARNING,
            RunEventLevel.error: logging.ERROR,
        }
        LOGGER.log(
            level_map[event.level],
            "%s [%s] run=%s step=%s provider=%s",
            event.message,
            event.event_type,
            event.run_id,
            event.step_index,
            event.provider_key,
        )


class ProviderRegistry:
    def __init__(
        self,
        *,
        llm_providers: dict[str, LLMProvider],
        tool_providers: dict[str, ToolProvider],
        observability_providers: list[ObservabilityProvider],
    ) -> None:
        self._llm_providers = llm_providers
        self._tool_providers = tool_providers
        self._observability_providers = observability_providers

    def list_definitions(self) -> list[ProviderDefinition]:
        definitions = [
            *(provider.definition for provider in self._llm_providers.values()),
            *(provider.definition for provider in self._tool_providers.values()),
            *(provider.definition for provider in self._observability_providers),
        ]
        return sorted(definitions, key=lambda item: (item.kind.value, item.display_name.lower()))

    def get_llm_provider(self, key: str) -> LLMProvider:
        provider = self._llm_providers.get(key)
        if provider is None:
            raise ValueError(f"Unknown LLM provider '{key}'.")
        return provider

    def get_tool_provider(self, key: str) -> ToolProvider:
        provider = self._tool_providers.get(key)
        if provider is None:
            raise ValueError(f"Unknown tool provider '{key}'.")
        return provider

    def emit(self, event: WorkflowRunEvent) -> None:
        for provider in self._observability_providers:
            provider.record(event)


def build_default_provider_registry(repository: WorkflowRepository) -> ProviderRegistry:
    return ProviderRegistry(
        llm_providers={LocalEchoLLMProvider.definition.key: LocalEchoLLMProvider()},
        tool_providers={
            HttpToolProvider.definition.key: HttpToolProvider(),
            NotificationToolProvider.definition.key: NotificationToolProvider(),
        },
        observability_providers=[
            RepositoryObservabilityProvider(repository),
            LoggerObservabilityProvider(),
        ],
    )
