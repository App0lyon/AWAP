"""Built-in node catalog for workflow authoring."""

from awap.domain import NodeCategory, NodeTypeDefinition

DEFAULT_NODE_CATALOG: dict[str, NodeTypeDefinition] = {
    "manual_trigger": NodeTypeDefinition(
        key="manual_trigger",
        category=NodeCategory.trigger,
        display_name="Manual Trigger",
        description="Starts a workflow from a user action.",
        max_outgoing_edges=None,
    ),
    "schedule_trigger": NodeTypeDefinition(
        key="schedule_trigger",
        category=NodeCategory.trigger,
        display_name="Schedule Trigger",
        description="Starts a workflow on a recurring schedule.",
        required_config_fields=["cron"],
        max_outgoing_edges=None,
    ),
    "llm_prompt": NodeTypeDefinition(
        key="llm_prompt",
        category=NodeCategory.ai,
        display_name="LLM Prompt",
        description="Runs a prompt against a configured model provider.",
        required_config_fields=["prompt_template", "model"],
        max_outgoing_edges=None,
    ),
    "decision": NodeTypeDefinition(
        key="decision",
        category=NodeCategory.logic,
        display_name="Decision",
        description="Routes execution based on workflow state.",
        max_outgoing_edges=None,
    ),
    "join": NodeTypeDefinition(
        key="join",
        category=NodeCategory.flow,
        display_name="Join",
        description="Waits for multiple active branches before continuing.",
        max_outgoing_edges=None,
    ),
    "sub_workflow": NodeTypeDefinition(
        key="sub_workflow",
        category=NodeCategory.flow,
        display_name="Sub-Workflow",
        description="Runs another workflow as a nested step.",
        required_config_fields=["workflow_id"],
        max_outgoing_edges=None,
    ),
    "for_each": NodeTypeDefinition(
        key="for_each",
        category=NodeCategory.flow,
        display_name="For Each",
        description="Iterates over a list and runs a sub-workflow for each item.",
        required_config_fields=["items_path", "workflow_id"],
        max_outgoing_edges=None,
    ),
    "http_request": NodeTypeDefinition(
        key="http_request",
        category=NodeCategory.action,
        display_name="HTTP Request",
        description="Calls an external HTTP endpoint.",
        required_config_fields=["method", "url"],
        max_outgoing_edges=None,
    ),
    "notification": NodeTypeDefinition(
        key="notification",
        category=NodeCategory.action,
        display_name="Notification",
        description="Sends a notification to an end user or operator.",
        required_config_fields=["channel", "message"],
        max_outgoing_edges=None,
    ),
}
