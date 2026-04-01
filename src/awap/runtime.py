"""Workflow execution runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from awap.domain import ApprovalDecision, CredentialSecret, WorkflowDefinition, WorkflowEdge, WorkflowNode
from awap.providers import ProviderRegistry

SubworkflowInvoker = Callable[[str, int | None, dict[str, Any]], dict[str, Any]]


@dataclass
class ApprovalRequiredError(Exception):
    title: str
    prompt: str
    step_index: int
    node_id: str


class WorkflowExecutionEngine:
    """Executes workflow nodes using provider abstractions."""

    _template_pattern = re.compile(r"{{\s*([^}]+?)\s*}}")

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry

    def create_context(self, workflow: WorkflowDefinition, input_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow": {"id": workflow.id, "version": workflow.version, "name": workflow.name},
            "input": input_payload,
            "steps": {},
            "last": None,
            "join_inputs": {},
            "agent_memory": {},
            "approvals": {},
        }

    def execute_node(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None = None, *, invoke_subworkflow: SubworkflowInvoker | None = None, step_index: int | None = None) -> dict[str, Any]:
        handlers = {
            "manual_trigger": self._execute_manual_trigger,
            "schedule_trigger": self._execute_schedule_trigger,
            "llm_prompt": self._execute_llm_prompt,
            "knowledge_retrieval": self._execute_knowledge_retrieval,
            "ai_agent": self._execute_ai_agent,
            "decision": self._execute_decision,
            "approval": self._execute_approval,
            "join": self._execute_join,
            "sub_workflow": self._execute_sub_workflow,
            "for_each": self._execute_for_each,
            "http_request": self._execute_tool_node,
            "sql_query": self._execute_tool_node,
            "file_write": self._execute_tool_node,
            "notification": self._execute_tool_node,
        }
        handler = handlers.get(node.type)
        if handler is None:
            raise ValueError(f"No runtime executor is registered for node type '{node.type}'.")
        return handler(node, context, credential, invoke_subworkflow, step_index)

    def select_edges(self, node: WorkflowNode, output_payload: dict[str, Any], outgoing_edges: list[WorkflowEdge]) -> list[WorkflowEdge]:
        unconditional = [edge for edge in outgoing_edges if edge.condition_value is None and not edge.is_default]
        conditional = [edge for edge in outgoing_edges if edge.condition_value is not None]
        default_edges = [edge for edge in outgoing_edges if edge.is_default]
        if not conditional and not default_edges:
            return outgoing_edges

        route = self._normalize_route(output_payload.get("route"))
        matched = [edge for edge in conditional if self._normalize_route(edge.condition_value) == route]
        if matched:
            return [*unconditional, *matched]
        return [*unconditional, *default_edges]

    def update_context(self, context: dict[str, Any], node: WorkflowNode, output_payload: dict[str, Any]) -> None:
        context["steps"][node.id] = output_payload
        context["last"] = output_payload
        if node.type == "approval":
            context.setdefault("approvals", {})[node.id] = output_payload

    def record_join_inputs(self, context: dict[str, Any], target_node_id: str, source_node_id: str, payload: dict[str, Any]) -> None:
        join_inputs = context.setdefault("join_inputs", {})
        node_inputs = join_inputs.setdefault(target_node_id, {})
        node_inputs[source_node_id] = payload

    def _execute_manual_trigger(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del node, credential, invoke_subworkflow, step_index
        return {"triggered": True, "trigger_type": "manual_trigger", "received_input_keys": sorted(context["input"].keys())}

    def _execute_schedule_trigger(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, invoke_subworkflow, step_index
        return {"triggered": True, "trigger_type": node.type, "schedule": node.config.get("cron"), "received_input_keys": sorted(context["input"].keys())}

    def _execute_llm_prompt(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del invoke_subworkflow, step_index
        prompt = self._render_template(node.config["prompt_template"], context)
        provider_key = node.config.get("provider", "nvidia_build_free_chat")
        provider = self._provider_registry.get_llm_provider(provider_key)
        output = provider.generate(model=node.config["model"], prompt=prompt, credential=credential, context=context, config=node.config)
        return self._apply_guardrails(node, output)

    def _execute_knowledge_retrieval(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, invoke_subworkflow, step_index
        query = self._render_template(node.config["query_template"], context)
        provider = self._provider_registry.get_tool_provider(node.config.get("provider", "knowledge_tool"))
        return provider.execute(
            node_type=node.type,
            config={"knowledge_base_id": node.config["knowledge_base_id"], "query": query, "top_k": node.config.get("top_k", 5)},
            credential=None,
            context=context,
        )

    def _execute_ai_agent(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del invoke_subworkflow, step_index
        goal = self._render_template(node.config["goal_template"], context)
        max_iterations = int(node.config.get("max_iterations", 3))
        memory = context.setdefault("agent_memory", {}).setdefault(node.id, [])
        iterations: list[dict[str, Any]] = []
        tool_sequence = list(node.config.get("tool_sequence", []))
        llm_provider = self._provider_registry.get_llm_provider(node.config.get("provider", "nvidia_build_free_chat"))

        for iteration_index in range(max_iterations):
            observation_parts: list[str] = [f"Goal: {goal}"]
            if memory:
                observation_parts.append("Memory:")
                observation_parts.extend(str(item) for item in memory[-5:])
            planned_tool = tool_sequence[iteration_index] if iteration_index < len(tool_sequence) else None
            tool_output: dict[str, Any] | None = None
            if planned_tool is not None:
                tool_output = self._run_agent_tool(planned_tool, context)
                memory.append({"tool": planned_tool.get("type"), "output": tool_output})
                observation_parts.append(f"Tool observation: {json.dumps(tool_output)}")
            prompt = "\n".join(observation_parts)
            llm_output = llm_provider.generate(model=node.config["model"], prompt=prompt, credential=credential, context=context, config=node.config)
            iteration = {"iteration": iteration_index + 1, "tool": planned_tool, "tool_output": tool_output, "draft": llm_output["response"]}
            iterations.append(iteration)
            memory.append({"draft": llm_output["response"]})
            if not planned_tool:
                break

        final_response = iterations[-1]["draft"] if iterations else ""
        reflection = None
        if node.config.get("enable_reflection", False):
            reflection_prompt = self._render_template(
                node.config.get("reflection_prompt_template", "Reflect on this draft answer and improve it:\n{{last.response}}"),
                {**context, "last": {"response": final_response}},
            )
            reflection_output = llm_provider.generate(model=node.config["model"], prompt=reflection_prompt, credential=credential, context=context, config=node.config)
            reflection = reflection_output["response"]
            final_response = reflection_output["response"]

        output = {
            "provider": node.config.get("provider", "nvidia_build_free_chat"),
            "goal": goal,
            "iterations": iterations,
            "memory": memory,
            "response": final_response,
            "reflection": reflection,
        }
        return self._apply_guardrails(node, output)

    def _run_agent_tool(self, planned_tool: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        tool_type = planned_tool.get("type")
        if tool_type == "knowledge_retrieval":
            provider = self._provider_registry.get_tool_provider("knowledge_tool")
            query = self._render_template(planned_tool["query_template"], context)
            return provider.execute(node_type="knowledge_retrieval", config={"knowledge_base_id": planned_tool["knowledge_base_id"], "query": query, "top_k": planned_tool.get("top_k", 3)}, credential=None, context=context)
        if tool_type == "http_request":
            provider = self._provider_registry.get_tool_provider("http_tool")
            config = self._render_config(planned_tool["config"], context)
            return provider.execute(node_type="http_request", config=config, credential=None, context=context)
        if tool_type == "sql_query":
            provider = self._provider_registry.get_tool_provider("sqlite_tool")
            config = self._render_config(planned_tool["config"], context)
            return provider.execute(node_type="sql_query", config=config, credential=None, context=context)
        if tool_type == "file_write":
            provider = self._provider_registry.get_tool_provider("file_tool")
            config = self._render_config(planned_tool["config"], context)
            return provider.execute(node_type="file_write", config=config, credential=None, context=context)
        raise RuntimeError(f"Unsupported agent tool type '{tool_type}'.")

    def _execute_decision(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, invoke_subworkflow, step_index
        condition_key = node.config.get("condition_key")
        expected_value = node.config.get("equals")
        actual_value = self._resolve_path(condition_key, context) if condition_key else None
        matched = actual_value == expected_value if condition_key else bool(context["last"])
        route = actual_value if condition_key else matched
        return {"condition_key": condition_key, "expected_value": expected_value, "actual_value": actual_value, "matched": matched, "route": route}

    def _execute_approval(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, invoke_subworkflow
        prompt = self._render_template(node.config["prompt_template"], context)
        existing = context.get("approvals", {}).get(node.id)
        if existing is not None:
            route = existing.get("decision")
            return {"approved": route == ApprovalDecision.approved.value, "decision": route, "comment": existing.get("comment", ""), "payload": existing.get("payload", {}), "route": route}
        raise ApprovalRequiredError(
            title=node.label or "Approval required",
            prompt=prompt,
            step_index=step_index or 0,
            node_id=node.id,
        )

    def _execute_join(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, invoke_subworkflow, step_index
        return {"joined": True, "inputs": context.get("join_inputs", {}).get(node.id, {})}

    def _execute_sub_workflow(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, step_index
        if invoke_subworkflow is None:
            raise RuntimeError("Sub-workflow execution is not available in this runtime.")
        workflow_input = self._build_subworkflow_input(node.config, context)
        result = invoke_subworkflow(str(node.config["workflow_id"]), node.config.get("version"), workflow_input)
        return {"workflow_id": node.config["workflow_id"], "workflow_version": node.config.get("version"), "input_payload": workflow_input, "result": result}

    def _execute_for_each(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del credential, step_index
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
            result = invoke_subworkflow(str(node.config["workflow_id"]), node.config.get("version"), workflow_input)
            results.append({"index": index, item_key: item, "result": result})
        return {"workflow_id": node.config["workflow_id"], "count": len(results), "results": results}

    def _execute_tool_node(self, node: WorkflowNode, context: dict[str, Any], credential: CredentialSecret | None, invoke_subworkflow: SubworkflowInvoker | None, step_index: int | None) -> dict[str, Any]:
        del invoke_subworkflow, step_index
        default_provider_map = {
            "http_request": "http_tool",
            "sql_query": "sqlite_tool",
            "file_write": "file_tool",
            "notification": "notification_tool",
        }
        provider_key = node.config.get("provider", default_provider_map[node.type])
        provider = self._provider_registry.get_tool_provider(provider_key)
        config = self._render_config(node.config, context)
        return provider.execute(node_type=node.type, config=config, credential=credential, context=context)

    def _build_subworkflow_input(self, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if "input_mapping" in config and isinstance(config["input_mapping"], dict):
            return {key: self._resolve_path(str(path), context) for key, path in config["input_mapping"].items()}
        base_path = config.get("input_path")
        if base_path:
            resolved = self._resolve_path(str(base_path), context)
            if isinstance(resolved, dict):
                return dict(resolved)
        return dict(context.get("input", {}))

    def _apply_guardrails(self, node: WorkflowNode, output_payload: dict[str, Any]) -> dict[str, Any]:
        response = output_payload.get("response")
        if not isinstance(response, str):
            return output_payload

        issues: list[str] = []
        blocked_terms = [term.lower() for term in node.config.get("blocked_terms", [])]
        required_terms = [term.lower() for term in node.config.get("required_terms", [])]
        response_lower = response.lower()
        for blocked in blocked_terms:
            if blocked in response_lower:
                issues.append(f"Blocked term present: {blocked}")
        for required in required_terms:
            if required not in response_lower:
                issues.append(f"Required term missing: {required}")
        output_schema = node.config.get("output_schema")
        if output_schema:
            try:
                parsed = json.loads(response)
                if not isinstance(parsed, dict):
                    issues.append("Output schema requires a JSON object response.")
                else:
                    missing_keys = [key for key in output_schema if key not in parsed]
                    if missing_keys:
                        issues.append(f"Missing schema keys: {', '.join(missing_keys)}")
            except json.JSONDecodeError:
                issues.append("Output schema requires valid JSON output.")

        if issues:
            fallback = node.config.get("fallback_response")
            if fallback is not None:
                output_payload["guardrail_issues"] = issues
                output_payload["response"] = str(fallback)
                output_payload["guardrail_fallback_applied"] = True
                return output_payload
            raise RuntimeError("; ".join(issues))
        return output_payload

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
