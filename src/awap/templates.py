"""Built-in workflow templates for onboarding and quick-start creation."""

from __future__ import annotations

from awap.domain import WorkflowDraftPayload, WorkflowNode, WorkflowEdge, WorkflowTemplateDefinition


BUILTIN_WORKFLOW_TEMPLATES: list[WorkflowTemplateDefinition] = [
    WorkflowTemplateDefinition(
        key="support-triage",
        display_name="Support Triage",
        description="Classify inbound support requests and notify the right team.",
        category="operations",
        workflow=WorkflowDraftPayload(
            name="Support Triage",
            description="Classify a support request, route by urgency, and notify an operator.",
            release_notes="Starter template for helpdesk-style automations.",
            nodes=[
                WorkflowNode(id="start", type="manual_trigger", label="Start"),
                WorkflowNode(id="classify", type="llm_prompt", label="Classify", config={"provider": "nvidia_build_free_chat", "model": "meta/llama-3.1-8b-instruct", "prompt_template": "Classify the request: {{input.ticket}}", "mock_response": "{\"priority\":\"high\"}"}),
                WorkflowNode(id="notify", type="notification", label="Notify", config={"channel": "slack", "message": "New support request needs review"}),
            ],
            edges=[WorkflowEdge(source="start", target="classify"), WorkflowEdge(source="classify", target="notify")],
        ),
    ),
    WorkflowTemplateDefinition(
        key="approval-gate",
        display_name="Approval Gate",
        description="Pause for a human decision before continuing an outbound action.",
        category="governance",
        workflow=WorkflowDraftPayload(
            name="Approval Gate",
            description="Collect approval before sending an external notification.",
            release_notes="Starter template for human-in-the-loop workflows.",
            nodes=[
                WorkflowNode(id="start", type="manual_trigger", label="Start"),
                WorkflowNode(id="approval", type="approval", label="Approval", config={"prompt_template": "Approve {{input.subject}}?"}),
                WorkflowNode(id="notify", type="notification", label="Notify", config={"channel": "slack", "message": "Approved {{input.subject}}"}),
            ],
            edges=[WorkflowEdge(source="start", target="approval"), WorkflowEdge(source="approval", target="notify", condition_value="approved")],
        ),
    ),
    WorkflowTemplateDefinition(
        key="knowledge-answer",
        display_name="Knowledge Answer",
        description="Retrieve grounded context from a knowledge base before generating an answer.",
        category="ai",
        workflow=WorkflowDraftPayload(
            name="Knowledge Answer",
            description="Look up context from a knowledge base and answer with a model.",
            release_notes="Starter template for retrieval-augmented workflows.",
            nodes=[
                WorkflowNode(id="start", type="manual_trigger", label="Start"),
                WorkflowNode(id="retrieve", type="knowledge_retrieval", label="Retrieve", config={"provider": "knowledge_tool", "knowledge_base_id": "", "query_template": "{{input.question}}", "top_k": 3}),
                WorkflowNode(id="answer", type="llm_prompt", label="Answer", config={"provider": "nvidia_build_free_chat", "model": "meta/llama-3.1-8b-instruct", "prompt_template": "Answer using this context: {{steps.retrieve.chunks}}", "mock_response": "Grounded answer"}),
            ],
            edges=[WorkflowEdge(source="start", target="retrieve"), WorkflowEdge(source="retrieve", target="answer")],
        ),
    ),
]
