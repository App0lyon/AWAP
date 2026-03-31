"""Built-in node catalog for early workflow construction."""

from awap.domain import NodeCategory, NodeTypeDefinition

DEFAULT_NODE_CATALOG: dict[str, NodeTypeDefinition] = {
    "manual_trigger": NodeTypeDefinition(
        key="manual_trigger",
        category=NodeCategory.trigger,
        display_name="Manual Trigger",
        description="Starts a workflow from a user action.",
        max_outgoing_edges=1,
    ),
    "schedule_trigger": NodeTypeDefinition(
        key="schedule_trigger",
        category=NodeCategory.trigger,
        display_name="Schedule Trigger",
        description="Starts a workflow on a recurring schedule.",
        required_config_fields=["cron"],
        max_outgoing_edges=1,
    ),
    "llm_prompt": NodeTypeDefinition(
        key="llm_prompt",
        category=NodeCategory.ai,
        display_name="LLM Prompt",
        description="Runs a prompt against a configured model provider.",
        required_config_fields=["prompt_template", "model"],
        max_outgoing_edges=1,
    ),
    "decision": NodeTypeDefinition(
        key="decision",
        category=NodeCategory.logic,
        display_name="Decision",
        description="Routes execution based on workflow state.",
    ),
    "http_request": NodeTypeDefinition(
        key="http_request",
        category=NodeCategory.action,
        display_name="HTTP Request",
        description="Calls an external HTTP endpoint.",
        required_config_fields=["method", "url"],
        max_outgoing_edges=1,
    ),
    "notification": NodeTypeDefinition(
        key="notification",
        category=NodeCategory.action,
        display_name="Notification",
        description="Sends a notification to an end user or operator.",
        required_config_fields=["channel", "message"],
        max_outgoing_edges=1,
    ),
}
