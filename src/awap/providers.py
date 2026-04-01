"""Provider abstractions for LLMs, tools, credentials, and observability."""

from __future__ import annotations

import json
import logging
import os
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
NVIDIA_CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class LLMProvider(Protocol):
    definition: ProviderDefinition

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        credential: CredentialSecret | None,
        context: dict[str, Any],
        config: dict[str, Any],
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


class NvidiaBuildFreeLLMProvider:
    definition = ProviderDefinition(
        key="nvidia_build_free_chat",
        kind=ProviderKind.llm,
        display_name="NVIDIA Build Free Chat",
        description=(
            "Calls NVIDIA Build hosted chat-completions endpoints using bearer auth. "
            "Configure models that are available through free Build endpoints."
        ),
        supported_node_types=["llm_prompt"],
    )

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        credential: CredentialSecret | None,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del context

        if "mock_response" in config:
            return {
                "provider": self.definition.key,
                "model": model,
                "prompt": prompt,
                "response": config["mock_response"],
                "mocked": True,
            }

        api_key = self._resolve_api_key(credential)
        if not api_key:
            raise RuntimeError(
                "NVIDIA API key not configured. Set NVIDIA_API_KEY or attach a bearer token credential."
            )

        temperature = float(config.get("temperature", 0.2))
        max_tokens = int(config.get("max_tokens", 1024))
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        raw = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        http_request = request.Request(
            url=NVIDIA_CHAT_COMPLETIONS_URL,
            data=raw,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=float(config.get("timeout_seconds", 60))) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"NVIDIA chat completion failed: {exc.code} {message}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"NVIDIA chat completion failed: {exc}") from exc

        choices = body.get("choices") or []
        message = ""
        finish_reason = None
        if choices:
            message = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason")

        return {
            "provider": self.definition.key,
            "model": model,
            "prompt": prompt,
            "response": message,
            "finish_reason": finish_reason,
            "usage": body.get("usage", {}),
            "mocked": False,
        }

    def _resolve_api_key(self, credential: CredentialSecret | None) -> str | None:
        if credential is not None:
            if credential.secret_payload.get("bearer_token"):
                return str(credential.secret_payload["bearer_token"])
            if credential.secret_payload.get("api_key"):
                return str(credential.secret_payload["api_key"])
        return os.getenv("NVIDIA_API_KEY")


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
        del node_type, context
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
            api_key = secret_payload.get("api_key")
            if api_key and "Authorization" not in headers:
                headers["X-API-Key"] = str(api_key)
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
        del node_type, context
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
        llm_providers={
            NvidiaBuildFreeLLMProvider.definition.key: NvidiaBuildFreeLLMProvider(),
        },
        tool_providers={
            HttpToolProvider.definition.key: HttpToolProvider(),
            NotificationToolProvider.definition.key: NotificationToolProvider(),
        },
        observability_providers=[
            RepositoryObservabilityProvider(repository),
            LoggerObservabilityProvider(),
        ],
    )
