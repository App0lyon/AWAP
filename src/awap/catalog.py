"""Built-in node catalog for workflow authoring."""

from awap.domain import NodeCategory, NodeTypeDefinition

DEFAULT_NODE_CATALOG: dict[str, NodeTypeDefinition] = {
    "manual_trigger": NodeTypeDefinition(
        key="manual_trigger",
        category=NodeCategory.trigger,
        display_name="Manual Trigger",
        description="Starts a workflow from a user action.",
    ),
    "schedule_trigger": NodeTypeDefinition(
        key="schedule_trigger",
        category=NodeCategory.trigger,
        display_name="Schedule Trigger",
        description="Starts a workflow on a recurring schedule.",
        required_config_fields=["cron"],
    ),
    "llm_prompt": NodeTypeDefinition(
        key="llm_prompt",
        category=NodeCategory.ai,
        display_name="LLM Prompt",
        description="Runs a prompt against a configured model provider.",
        required_config_fields=["prompt_template", "model"],
    ),
    "knowledge_retrieval": NodeTypeDefinition(
        key="knowledge_retrieval",
        category=NodeCategory.ai,
        display_name="Knowledge Retrieval",
        description="Searches a knowledge base and returns ranked chunks with citations.",
        required_config_fields=["knowledge_base_id", "query_template"],
    ),
    "ai_agent": NodeTypeDefinition(
        key="ai_agent",
        category=NodeCategory.ai,
        display_name="AI Agent",
        description="Runs a lightweight planning and tool-use loop with memory and reflection.",
        required_config_fields=["goal_template", "model"],
    ),
    "decision": NodeTypeDefinition(
        key="decision",
        category=NodeCategory.logic,
        display_name="Decision",
        description="Routes execution based on workflow state.",
    ),
    "approval": NodeTypeDefinition(
        key="approval",
        category=NodeCategory.logic,
        display_name="Approval",
        description="Pauses execution for a human review decision.",
        required_config_fields=["prompt_template"],
    ),
    "join": NodeTypeDefinition(
        key="join",
        category=NodeCategory.flow,
        display_name="Join",
        description="Waits for multiple active branches before continuing.",
    ),
    "sub_workflow": NodeTypeDefinition(
        key="sub_workflow",
        category=NodeCategory.flow,
        display_name="Sub-Workflow",
        description="Runs another workflow as a nested step.",
        required_config_fields=["workflow_id"],
    ),
    "for_each": NodeTypeDefinition(
        key="for_each",
        category=NodeCategory.flow,
        display_name="For Each",
        description="Iterates over a list and runs a sub-workflow for each item.",
        required_config_fields=["items_path", "workflow_id"],
    ),
    "http_request": NodeTypeDefinition(
        key="http_request",
        category=NodeCategory.action,
        display_name="HTTP Request",
        description="Calls an external HTTP endpoint.",
        required_config_fields=["method", "url"],
    ),
    "sql_query": NodeTypeDefinition(
        key="sql_query",
        category=NodeCategory.action,
        display_name="SQL Query",
        description="Executes a SQL query against a configured SQLite database.",
        required_config_fields=["query"],
    ),
    "file_write": NodeTypeDefinition(
        key="file_write",
        category=NodeCategory.action,
        display_name="File Write",
        description="Writes rendered content to a local file path.",
        required_config_fields=["path", "content"],
    ),
    "notification": NodeTypeDefinition(
        key="notification",
        category=NodeCategory.action,
        display_name="Notification",
        description="Sends a notification to an end user or operator.",
        required_config_fields=["channel", "message"],
    ),
}
