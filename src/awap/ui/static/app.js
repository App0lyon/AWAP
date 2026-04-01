const state = {
  authToken: window.localStorage.getItem("awap.authToken") || "awap-dev-admin-token",
  nodeTypes: [],
  providers: [],
  credentials: [],
  templates: [],
  workflows: [],
  versions: [],
  selectedWorkflowId: null,
  selectedVersion: null,
  draft: createBlankWorkflow(),
  validation: null,
  plan: null,
  versionDiff: null,
  runs: [],
  selectedRunId: null,
  runEvents: [],
  comments: [],
  auditLogs: [],
  operationsSummary: null,
  sourceControl: null,
  runPollTimer: null,
}

const els = {
  statusPill: document.querySelector("#status-pill"),
  workflowList: document.querySelector("#workflow-list"),
  workflowTemplateList: document.querySelector("#workflow-template-list"),
  versionList: document.querySelector("#version-list"),
  versionCaption: document.querySelector("#version-caption"),
  nodeTypeList: document.querySelector("#node-type-list"),
  providerList: document.querySelector("#provider-list"),
  credentialList: document.querySelector("#credential-list"),
  editorCaption: document.querySelector("#editor-caption"),
  workflowName: document.querySelector("#workflow-name"),
  workflowDescription: document.querySelector("#workflow-description"),
  workflowReleaseNotes: document.querySelector("#workflow-release-notes"),
  nodeEditorList: document.querySelector("#node-editor-list"),
  edgeEditorList: document.querySelector("#edge-editor-list"),
  canvasPreview: document.querySelector("#canvas-preview"),
  validationStatus: document.querySelector("#validation-status"),
  validationOutput: document.querySelector("#validation-output"),
  planCaption: document.querySelector("#plan-caption"),
  planOutput: document.querySelector("#plan-output"),
  versionDiffOutput: document.querySelector("#version-diff-output"),
  runInput: document.querySelector("#run-input"),
  runList: document.querySelector("#run-list"),
  runEventCaption: document.querySelector("#run-event-caption"),
  runEventList: document.querySelector("#run-event-list"),
  commentInput: document.querySelector("#comment-input"),
  commentList: document.querySelector("#comment-list"),
  operationsSummaryOutput: document.querySelector("#operations-summary-output"),
  sourceControlOutput: document.querySelector("#source-control-output"),
  auditLogList: document.querySelector("#audit-log-list"),
  credentialDialog: document.querySelector("#credential-dialog"),
  credentialForm: document.querySelector("#credential-form"),
  credentialProvider: document.querySelector("#credential-provider"),
  authToken: document.querySelector("#auth-token"),
}

const defaultConfigByType = {
  manual_trigger: {},
  webhook_trigger: {},
  schedule_trigger: { cron: "0 * * * *" },
  llm_prompt: {
    provider: "nvidia_build_free_chat",
    model: "meta/llama-3.1-8b-instruct",
    prompt_template: "Summarize {{input.text}}",
    mock_response: "Mocked NVIDIA response",
  },
  knowledge_retrieval: {
    provider: "knowledge_tool",
    knowledge_base_id: "",
    query_template: "Find information about {{input.topic}}",
    top_k: 3,
  },
  ai_agent: {
    provider: "nvidia_build_free_chat",
    model: "meta/llama-3.1-8b-instruct",
    goal_template: "Help with {{input.text}}",
    max_iterations: 3,
    tool_sequence: [],
    enable_reflection: true,
    reflection_prompt_template: "Reflect on this draft answer and improve it:\n{{last.response}}",
    mock_response: "Mocked NVIDIA agent response",
  },
  decision: {
    condition_key: "last.matched",
    equals: true,
  },
  approval: {
    prompt_template: "Approve action for {{input.subject}}?",
  },
  join: {},
  sub_workflow: {
    workflow_id: "",
    input_mapping: { text: "input.text" },
  },
  for_each: {
    workflow_id: "",
    items_path: "input.items",
    item_key: "item",
  },
  http_request: {
    provider: "http_tool",
    method: "GET",
    url: "https://example.com",
    mock_response: { ok: true },
  },
  sql_query: {
    provider: "sqlite_tool",
    database_path: "/tmp/awap-demo.db",
    query: "SELECT 1 AS ok",
    query_type: "select",
    parameters: [],
  },
  file_write: {
    provider: "file_tool",
    path: "/tmp/awap-output.txt",
    content: "{{input.text}}",
  },
  notification: {
    provider: "notification_tool",
    channel: "slack",
    message: "Workflow completed for {{input.subject}}",
  },
}

boot().catch(handleError)

async function boot() {
  els.authToken.value = state.authToken
  bindEvents()
  await refreshLibrary()
  await refreshOperations()
  renderAll()

  if (state.workflows.length > 0) {
    await loadWorkflow(state.workflows[0].id, state.workflows[0].version)
  } else {
    setStatus("Ready")
  }
}

function bindEvents() {
  document
    .querySelector("#new-workflow-button")
    .addEventListener("click", () => resetEditor(createBlankWorkflow()))
  document.querySelector("#refresh-all-button").addEventListener("click", refreshEverything)
  document.querySelector("#refresh-workflows-button").addEventListener("click", refreshWorkflows)
  document.querySelector("#add-node-button").addEventListener("click", onAddNode)
  document.querySelector("#add-edge-button").addEventListener("click", onAddEdge)
  document.querySelector("#save-workflow-button").addEventListener("click", saveNewWorkflow)
  document.querySelector("#save-version-button").addEventListener("click", saveNewVersion)
  document.querySelector("#publish-version-button").addEventListener("click", publishSelectedVersion)
  document.querySelector("#validate-button").addEventListener("click", validateSelected)
  document.querySelector("#plan-button").addEventListener("click", planSelected)
  document.querySelector("#run-button").addEventListener("click", startRun)
  document.querySelector("#refresh-runs-button").addEventListener("click", refreshRuns)
  document.querySelector("#add-credential-button").addEventListener("click", openCredentialDialog)
  document.querySelector("#save-auth-button").addEventListener("click", saveAuthToken)
  document.querySelector("#pause-run-button").addEventListener("click", pauseSelectedRun)
  document.querySelector("#resume-run-button").addEventListener("click", resumeSelectedRun)
  document.querySelector("#cancel-run-button").addEventListener("click", cancelSelectedRun)
  document.querySelector("#retry-run-button").addEventListener("click", retrySelectedRun)
  document.querySelector("#compare-version-button").addEventListener("click", compareSelectedVersion)
  document.querySelector("#save-comment-button").addEventListener("click", saveComment)

  els.workflowList.addEventListener("click", onWorkflowListClick)
  els.workflowTemplateList.addEventListener("click", onTemplateListClick)
  els.versionList.addEventListener("click", onVersionListClick)
  els.nodeTypeList.addEventListener("click", onNodePaletteClick)
  els.nodeEditorList.addEventListener("click", onNodeEditorClick)
  els.nodeEditorList.addEventListener("change", onNodeEditorChange)
  els.edgeEditorList.addEventListener("click", onEdgeEditorClick)
  els.edgeEditorList.addEventListener("change", onEdgeEditorChange)
  els.runList.addEventListener("click", onRunListClick)

  els.workflowName.addEventListener("change", syncDraftSafely)
  els.workflowDescription.addEventListener("change", syncDraftSafely)
  els.workflowReleaseNotes.addEventListener("change", syncDraftSafely)
  els.credentialForm.addEventListener("submit", saveCredential)
}

async function refreshEverything() {
  await refreshLibrary()
  await refreshOperations()
  if (state.selectedWorkflowId) {
    await loadWorkflow(state.selectedWorkflowId, state.selectedVersion)
  } else {
    renderAll()
  }
}

async function refreshLibrary() {
  setStatus("Loading")
  const [nodeTypes, providers, credentials, workflows, templates] = await Promise.all([
    api("/node-types"),
    api("/providers"),
    api("/credentials"),
    api("/workflows"),
    api("/workflow-templates"),
  ])
  state.nodeTypes = nodeTypes
  state.providers = providers
  state.credentials = credentials
  state.workflows = workflows
  state.templates = templates
  populateCredentialProviderOptions()
  renderLibrary()
  setStatus("Ready")
}

async function refreshOperations() {
  const [summary, sourceControl, auditLogs] = await Promise.allSettled([
    api("/observability/summary"),
    api("/source-control/status"),
    api("/audit-logs?limit=25"),
  ])
  state.operationsSummary = summary.status === "fulfilled" ? summary.value : null
  state.sourceControl = sourceControl.status === "fulfilled" ? sourceControl.value : null
  state.auditLogs = auditLogs.status === "fulfilled" ? auditLogs.value : []
}

async function refreshWorkflows() {
  state.workflows = await api("/workflows")
  renderWorkflowList()
}

async function loadWorkflow(workflowId, version = null) {
  setStatus("Loading Workflow")
  const query = version ? `?version=${version}` : ""
  const workflow = await api(`/workflows/${workflowId}${query}`)
  state.selectedWorkflowId = workflow.id
  state.selectedVersion = workflow.version
  state.draft = workflowToDraft(workflow)
  state.versions = await api(`/workflows/${workflowId}/versions`)
  state.comments = await api(`/workflows/${workflowId}/comments?workflow_version=${workflow.version}`)
  state.versionDiff =
    workflow.version > 1
      ? await api(`/workflows/${workflowId}/versions/compare?from_version=${workflow.version - 1}&to_version=${workflow.version}`)
      : null
  state.validation = null
  state.plan = null
  state.selectedRunId = null
  state.runEvents = []
  stopRunPolling()
  await refreshRuns()
  renderAll()
  setStatus("Editing")
}

async function refreshRuns() {
  if (!state.selectedWorkflowId) {
    state.runs = []
    renderRuns()
    return
  }
  state.runs = await api(`/workflows/${state.selectedWorkflowId}/runs`)
  renderRuns()
}

async function selectRun(runId) {
  const run = await api(`/runs/${runId}`)
  upsertRun(run)
  state.selectedRunId = runId
  state.runEvents = await api(`/runs/${runId}/events`)
  renderRuns()
  renderRunEvents()
  if (run.status === "queued" || run.status === "running") {
    startRunPolling(runId)
  } else {
    stopRunPolling()
  }
}

function startRunPolling(runId) {
  stopRunPolling()
  state.runPollTimer = window.setInterval(async () => {
    try {
      const run = await api(`/runs/${runId}`)
      upsertRun(run)
      state.runEvents = await api(`/runs/${runId}/events`)
      renderRuns()
      renderRunEvents()
      if (run.status !== "queued" && run.status !== "running") {
        stopRunPolling()
      }
    } catch (error) {
      stopRunPolling()
      handleError(error)
    }
  }, 1500)
}

function stopRunPolling() {
  if (state.runPollTimer) {
    window.clearInterval(state.runPollTimer)
    state.runPollTimer = null
  }
}

function saveAuthToken() {
  state.authToken = els.authToken.value.trim() || "awap-dev-admin-token"
  window.localStorage.setItem("awap.authToken", state.authToken)
  refreshEverything().catch(handleError)
}

async function saveNewWorkflow() {
  const payload = collectEditorPayload()
  const created = await api("/workflows", {
    method: "POST",
    body: JSON.stringify(payload),
  })
  await refreshLibrary()
  await loadWorkflow(created.id, created.version)
  setStatus("Workflow Saved")
}

async function saveNewVersion() {
  if (!state.selectedWorkflowId) {
    throw new Error("Select or save a workflow before creating a version.")
  }
  const payload = collectEditorPayload()
  const created = await api(`/workflows/${state.selectedWorkflowId}/versions`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
  await refreshLibrary()
  await loadWorkflow(created.id, created.version)
  setStatus("Version Saved")
}

async function publishSelectedVersion() {
  requireSelection()
  const published = await api(
    `/workflows/${state.selectedWorkflowId}/versions/${state.selectedVersion}/publish`,
    { method: "POST" }
  )
  await refreshLibrary()
  await loadWorkflow(published.id, published.version)
  setStatus("Version Published")
}

async function validateSelected() {
  requireSelection()
  const validation = await api(
    `/workflows/${state.selectedWorkflowId}/validate?version=${state.selectedVersion}`,
    { method: "POST" }
  )
  state.validation = validation
  renderValidation()
}

async function planSelected() {
  requireSelection()
  const plan = await api(
    `/workflows/${state.selectedWorkflowId}/plan?version=${state.selectedVersion}`,
    { method: "POST" }
  )
  state.plan = plan
  renderPlan()
}

async function startRun() {
  requireSelection()
  const inputPayload = parseJson(els.runInput.value || "{}", "Run input JSON is invalid.")
  const run = await api(
    `/workflows/${state.selectedWorkflowId}/runs?version=${state.selectedVersion}`,
    {
      method: "POST",
      body: JSON.stringify({ input_payload: inputPayload }),
    }
  )
  upsertRun(run)
  state.selectedRunId = run.id
  state.runEvents = []
  renderRuns()
  renderRunEvents()
  startRunPolling(run.id)
}

async function pauseSelectedRun() {
  if (!state.selectedRunId) {
    throw new Error("Select a run first.")
  }
  const run = await api(`/runs/${state.selectedRunId}/pause`, { method: "POST" })
  upsertRun(run)
  renderRuns()
}

async function resumeSelectedRun() {
  if (!state.selectedRunId) {
    throw new Error("Select a run first.")
  }
  const run = await api(`/runs/${state.selectedRunId}/resume`, { method: "POST" })
  upsertRun(run)
  renderRuns()
  if (run.status === "queued" || run.status === "running") {
    startRunPolling(run.id)
  }
}

async function cancelSelectedRun() {
  if (!state.selectedRunId) {
    throw new Error("Select a run first.")
  }
  const run = await api(`/runs/${state.selectedRunId}/cancel`, { method: "POST" })
  upsertRun(run)
  renderRuns()
}

async function retrySelectedRun() {
  if (!state.selectedRunId) {
    throw new Error("Select a run first.")
  }
  const run = await api(`/runs/${state.selectedRunId}/retry?from_failed_step=true`, { method: "POST" })
  upsertRun(run)
  state.selectedRunId = run.id
  renderRuns()
  startRunPolling(run.id)
}

async function compareSelectedVersion() {
  requireSelection()
  if (!state.selectedVersion || state.selectedVersion <= 1) {
    state.versionDiff = null
    renderVersionDiff()
    return
  }
  state.versionDiff = await api(
    `/workflows/${state.selectedWorkflowId}/versions/compare?from_version=${state.selectedVersion - 1}&to_version=${state.selectedVersion}`
  )
  renderVersionDiff()
}

async function saveCredential(event) {
  event.preventDefault()
  const secretPayload = parseJson(
    document.querySelector("#credential-secret").value || "{}",
    "Credential secret JSON is invalid."
  )
  await api("/credentials", {
    method: "POST",
    body: JSON.stringify({
      name: document.querySelector("#credential-name").value.trim(),
      kind: document.querySelector("#credential-kind").value,
      provider_key: document.querySelector("#credential-provider").value || null,
      description: document.querySelector("#credential-description").value.trim(),
      secret_payload: secretPayload,
    }),
  })
  els.credentialDialog.close()
  els.credentialForm.reset()
  await refreshLibrary()
  renderEditor()
}

function openCredentialDialog() {
  els.credentialDialog.showModal()
}

async function saveComment() {
  if (!state.selectedWorkflowId || !state.selectedVersion) {
    throw new Error("Select a workflow version first.")
  }
  const body = els.commentInput.value.trim()
  if (!body) {
    throw new Error("Write a comment before posting.")
  }
  await api(`/workflows/${state.selectedWorkflowId}/comments`, {
    method: "POST",
    body: JSON.stringify({
      workflow_id: state.selectedWorkflowId,
      workflow_version: state.selectedVersion,
      body,
    }),
  })
  els.commentInput.value = ""
  state.comments = await api(
    `/workflows/${state.selectedWorkflowId}/comments?workflow_version=${state.selectedVersion}`
  )
  state.auditLogs = await api("/audit-logs?limit=25")
  renderComments()
  renderAdminView()
}

function onWorkflowListClick(event) {
  const button = event.target.closest("[data-workflow-id]")
  if (!button) {
    return
  }
  loadWorkflow(button.dataset.workflowId, button.dataset.version ? Number(button.dataset.version) : null).catch(
    handleError
  )
}

function onVersionListClick(event) {
  const button = event.target.closest("[data-version]")
  if (!button || !state.selectedWorkflowId) {
    return
  }
  loadWorkflow(state.selectedWorkflowId, Number(button.dataset.version)).catch(handleError)
}

function onTemplateListClick(event) {
  const button = event.target.closest("[data-template-key]")
  if (!button) {
    return
  }
  const template = state.templates.find((item) => item.key === button.dataset.templateKey)
  if (!template) {
    return
  }
  resetEditor(structuredClone(template.workflow))
  setStatus(`Loaded ${template.display_name}`)
}

function onNodePaletteClick(event) {
  const button = event.target.closest("[data-add-node-type]")
  if (!button) {
    return
  }
  try {
    syncDraftFromEditor()
    state.draft.nodes.push(createDefaultNode(button.dataset.addNodeType, state.draft.nodes))
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function onNodeEditorClick(event) {
  const button = event.target.closest("[data-remove-node-index]")
  if (!button) {
    return
  }
  try {
    syncDraftFromEditor()
    const index = Number(button.dataset.removeNodeIndex)
    const removedNode = state.draft.nodes[index]
    state.draft.nodes.splice(index, 1)
    state.draft.edges = state.draft.edges.filter(
      (edge) => edge.source !== removedNode.id && edge.target !== removedNode.id
    )
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function onNodeEditorChange(event) {
  const select = event.target.closest(".node-type-select")
  if (!select) {
    syncDraftSafely()
    return
  }

  try {
    syncDraftFromEditor()
    const index = Number(select.dataset.index)
    state.draft.nodes[index].type = select.value
    state.draft.nodes[index].label = state.nodeTypes.find((item) => item.key === select.value)?.display_name || select.value
    state.draft.nodes[index].config = structuredClone(defaultConfigForType(select.value))
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function onEdgeEditorClick(event) {
  const button = event.target.closest("[data-remove-edge-index]")
  if (!button) {
    return
  }
  try {
    syncDraftFromEditor()
    state.draft.edges.splice(Number(button.dataset.removeEdgeIndex), 1)
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function onEdgeEditorChange() {
  syncDraftSafely()
}

function onRunListClick(event) {
  const button = event.target.closest("[data-run-id]")
  if (!button) {
    return
  }
  selectRun(button.dataset.runId).catch(handleError)
}

function onAddNode() {
  try {
    syncDraftFromEditor()
    state.draft.nodes.push(createDefaultNode("manual_trigger", state.draft.nodes))
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function onAddEdge() {
  try {
    syncDraftFromEditor()
    const nodes = state.draft.nodes
    state.draft.edges.push({
      source: nodes[0]?.id || "",
      target: nodes[1]?.id || nodes[0]?.id || "",
    })
    renderEditor()
  } catch (error) {
    handleError(error)
  }
}

function syncDraftSafely() {
  try {
    syncDraftFromEditor()
    renderCanvasPreview()
  } catch {
    // Keep the current UI responsive while the user is editing invalid JSON.
  }
}

function syncDraftFromEditor() {
  state.draft = collectEditorPayload()
  renderCaption()
}

function collectEditorPayload() {
  const nodes = Array.from(els.nodeEditorList.querySelectorAll(".node-card")).map((card) => {
    const type = card.querySelector(".node-type-select").value
    const config = parseJson(
      card.querySelector(".node-config").value || "{}",
      `Node config JSON is invalid for node ${card.dataset.nodeIndex}.`
    )
    const providerValue = card.querySelector(".node-provider-select").value
    const credentialValue = card.querySelector(".node-credential-select").value

    if (providerValue) {
      config.provider = providerValue
    } else {
      delete config.provider
    }

    if (credentialValue) {
      config.credential_id = credentialValue
    } else {
      delete config.credential_id
    }

    return {
      id: card.querySelector(".node-id-input").value.trim(),
      label: card.querySelector(".node-label-input").value.trim(),
      type,
      config,
    }
  })

  const edges = Array.from(els.edgeEditorList.querySelectorAll(".edge-card")).map((card) => ({
    source: card.querySelector(".edge-source-select").value,
    target: card.querySelector(".edge-target-select").value,
    condition_value: normalizeEdgeCondition(card.querySelector(".edge-condition-input").value),
    is_default: card.querySelector(".edge-default-input").checked,
    label: card.querySelector(".edge-label-input").value.trim(),
  }))

  return {
    name: els.workflowName.value.trim(),
    description: els.workflowDescription.value.trim(),
    release_notes: els.workflowReleaseNotes.value.trim(),
    nodes,
    edges,
    settings: state.draft.settings || { max_concurrent_runs: 3 },
  }
}

function renderAll() {
  renderLibrary()
  renderEditor()
  renderValidation()
  renderPlan()
  renderVersionDiff()
  renderRuns()
  renderRunEvents()
  renderComments()
  renderAdminView()
}

function renderLibrary() {
  renderWorkflowList()
  renderTemplates()
  renderVersionList()
  renderNodeTypes()
  renderProviders()
  renderCredentials()
}

function renderWorkflowList() {
  els.workflowList.innerHTML = state.workflows
    .map(
      (workflow) => `
        <button
          class="workflow-item ${workflow.id === state.selectedWorkflowId ? "is-selected" : ""}"
          data-workflow-id="${escapeHtml(workflow.id)}"
          data-version="${workflow.version}"
        >
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(workflow.name)}</strong>
            <span class="pill">v${workflow.version}</span>
          </div>
          <div class="item-meta">${escapeHtml(workflow.state)} draft lane</div>
          <div class="item-meta">${escapeHtml(workflow.description || "No description")}</div>
        </button>
      `
    )
    .join("")
}

function renderVersionList() {
  els.versionCaption.textContent = state.selectedWorkflowId || "No workflow selected"
  els.versionList.innerHTML = state.versions
    .map(
      (version) => `
        <button
          class="version-item ${version.version === state.selectedVersion ? "is-selected" : ""}"
          data-version="${version.version}"
        >
          <div class="item-headline">
            <strong class="item-title">Version ${version.version}</strong>
            <span class="pill">${escapeHtml(version.state)}</span>
          </div>
          <div class="item-meta">${escapeHtml(version.name)}</div>
        </button>
      `
    )
    .join("")
}

function renderTemplates() {
  els.workflowTemplateList.innerHTML = state.templates
    .map(
      (template) => `
        <button class="workflow-item" data-template-key="${escapeHtml(template.key)}">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(template.display_name)}</strong>
            <span class="pill">${escapeHtml(template.category)}</span>
          </div>
          <div class="item-meta">${escapeHtml(template.description)}</div>
        </button>
      `
    )
    .join("")
}

function renderNodeTypes() {
  els.nodeTypeList.innerHTML = state.nodeTypes
    .map(
      (nodeType) => `
        <button class="badge" data-add-node-type="${escapeHtml(nodeType.key)}">
          ${escapeHtml(nodeType.display_name)}
        </button>
      `
    )
    .join("")
}

function renderProviders() {
  els.providerList.innerHTML = state.providers
    .map(
      (provider) => `
        <div class="provider-item">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(provider.display_name)}</strong>
            <span class="pill">${escapeHtml(provider.kind)}</span>
          </div>
          <div class="item-meta">${escapeHtml(provider.key)}</div>
          <div class="item-meta">${escapeHtml(provider.description)}</div>
        </div>
      `
    )
    .join("")
}

function renderCredentials() {
  els.credentialList.innerHTML = state.credentials
    .map(
      (credential) => `
        <div class="credential-item">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(credential.name)}</strong>
            <span class="pill">${escapeHtml(credential.kind)}</span>
          </div>
          <div class="item-meta">${escapeHtml(credential.provider_key || "unscoped")}</div>
          <div class="item-meta">${escapeHtml(credential.description || "No description")}</div>
        </div>
      `
    )
    .join("")
}

function renderEditor() {
  els.workflowName.value = state.draft.name || ""
  els.workflowDescription.value = state.draft.description || ""
  els.workflowReleaseNotes.value = state.draft.release_notes || ""
  renderCaption()

  els.nodeEditorList.innerHTML = state.draft.nodes
    .map((node, index) => renderNodeCard(node, index))
    .join("")
  els.edgeEditorList.innerHTML = state.draft.edges
    .map((edge, index) => renderEdgeCard(edge, index))
    .join("")
  renderCanvasPreview()
}

function renderCaption() {
  if (state.selectedWorkflowId) {
    els.editorCaption.textContent = `${state.selectedWorkflowId} · version ${state.selectedVersion}`
  } else {
    els.editorCaption.textContent = "Draft a new workflow"
  }
}

function renderNodeCard(node, index) {
  const providerOptions = getProvidersForNodeType(node.type)
  const currentProvider = node.config.provider || defaultProviderForType(node.type) || ""
  const currentCredential = node.config.credential_id || ""
  const configWithoutBindings = omit(node.config, ["provider", "credential_id"])
  return `
    <article class="node-card" data-node-index="${index}">
      <div class="item-headline">
        <strong class="item-title">${escapeHtml(node.label || `Node ${index + 1}`)}</strong>
        <button class="button button-ghost" data-remove-node-index="${index}">Remove</button>
      </div>
      <div class="node-card-grid">
        <label class="field">
          <span>Node ID</span>
          <input class="node-id-input" value="${escapeHtml(node.id)}" />
        </label>
        <label class="field">
          <span>Label</span>
          <input class="node-label-input" value="${escapeHtml(node.label)}" />
        </label>
        <label class="field">
          <span>Type</span>
          <select class="node-type-select" data-index="${index}">
            ${state.nodeTypes
              .map(
                (item) => `
                  <option value="${escapeHtml(item.key)}" ${item.key === node.type ? "selected" : ""}>
                    ${escapeHtml(item.display_name)}
                  </option>
                `
              )
              .join("")}
          </select>
        </label>
        <label class="field">
          <span>Provider</span>
          <select class="node-provider-select">
            <option value="">Default</option>
            ${providerOptions
              .map(
                (provider) => `
                  <option
                    value="${escapeHtml(provider.key)}"
                    ${provider.key === currentProvider ? "selected" : ""}
                  >
                    ${escapeHtml(provider.display_name)}
                  </option>
                `
              )
              .join("")}
          </select>
        </label>
        <label class="field field-wide">
          <span>Credential</span>
          <select class="node-credential-select">
            <option value="">No credential</option>
            ${getCredentialsForProvider(currentProvider)
              .map(
                (credential) => `
                  <option
                    value="${escapeHtml(credential.id)}"
                    ${credential.id === currentCredential ? "selected" : ""}
                  >
                    ${escapeHtml(credential.name)}
                  </option>
                `
              )
              .join("")}
          </select>
        </label>
        <label class="field field-wide">
          <span>Config JSON</span>
          <textarea class="node-config code-area">${escapeHtml(jsonPretty(configWithoutBindings))}</textarea>
        </label>
      </div>
    </article>
  `
}

function renderEdgeCard(edge, index) {
  const nodeOptions = state.draft.nodes
    .map(
      (node) => `
        <option value="${escapeHtml(node.id)}">${escapeHtml(node.id)} · ${escapeHtml(node.label)}</option>
      `
    )
    .join("")

  return `
    <article class="edge-card" data-edge-index="${index}">
      <div class="item-headline">
        <strong class="item-title">Edge ${index + 1}</strong>
        <button class="button button-ghost" data-remove-edge-index="${index}">Remove</button>
      </div>
      <div class="edge-card-grid">
        <label class="field">
          <span>Source</span>
          <select class="edge-source-select">
            ${injectSelectedOption(nodeOptions, edge.source)}
          </select>
        </label>
        <label class="field">
          <span>Target</span>
          <select class="edge-target-select">
            ${injectSelectedOption(nodeOptions, edge.target)}
          </select>
        </label>
        <label class="field">
          <span>Route Match</span>
          <input class="edge-condition-input" value="${escapeHtml(edge.condition_value ?? "")}" />
        </label>
        <label class="field">
          <span>Label</span>
          <input class="edge-label-input" value="${escapeHtml(edge.label || "")}" />
        </label>
        <label class="field">
          <span>Default</span>
          <input class="edge-default-input" type="checkbox" ${edge.is_default ? "checked" : ""} />
        </label>
      </div>
    </article>
  `
}

function renderCanvasPreview() {
  if (state.draft.nodes.length === 0) {
    els.canvasPreview.innerHTML = ""
    return
  }

  const nodeMap = Object.fromEntries(state.draft.nodes.map((node) => [node.id, node]))
  const incomingCounts = Object.fromEntries(state.draft.nodes.map((node) => [node.id, 0]))
  const outgoingMap = state.draft.edges.reduce((map, edge) => {
    map[edge.source] = [...(map[edge.source] || []), edge]
    incomingCounts[edge.target] = (incomingCounts[edge.target] || 0) + 1
    return map
  }, {})

  const roots = state.draft.nodes
    .filter((node) => (incomingCounts[node.id] || 0) === 0)
    .map((node) => node.id)
  const depthById = Object.fromEntries(state.draft.nodes.map((node) => [node.id, 0]))
  const queue = [...roots]
  while (queue.length > 0) {
    const currentId = queue.shift()
    for (const edge of outgoingMap[currentId] || []) {
      const nextDepth = depthById[currentId] + 1
      if (nextDepth > (depthById[edge.target] || 0)) {
        depthById[edge.target] = nextDepth
      }
      queue.push(edge.target)
    }
  }

  const laneMap = {}
  for (const node of state.draft.nodes) {
    const lane = depthById[node.id] || 0
    laneMap[lane] = [...(laneMap[lane] || []), node]
  }
  const laneKeys = Object.keys(laneMap)
    .map(Number)
    .sort((left, right) => left - right)
  const cardWidth = 180
  const cardHeight = 110
  const laneGap = 220
  const rowGap = 150
  const positions = {}
  laneKeys.forEach((lane, laneIndex) => {
    laneMap[lane].forEach((node, rowIndex) => {
      positions[node.id] = {
        x: 24 + laneIndex * laneGap,
        y: 32 + rowIndex * rowGap,
      }
    })
  })
  const stageWidth = Math.max(520, laneKeys.length * laneGap + 120)
  const stageHeight =
    Math.max(
      240,
      ...state.draft.nodes.map((node) => (positions[node.id]?.y || 0) + cardHeight + 36)
    ) || 240

  const edgeMarkup = state.draft.edges
    .map((edge) => {
      const source = positions[edge.source]
      const target = positions[edge.target]
      if (!source || !target) {
        return ""
      }
      const startX = source.x + cardWidth
      const startY = source.y + cardHeight / 2
      const endX = target.x
      const endY = target.y + cardHeight / 2
      const midX = (startX + endX) / 2
      const label = edge.label || edge.condition_value || (edge.is_default ? "default" : "")
      return `
        <path class="canvas-edge" d="M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}" />
        ${
          label
            ? `<text class="canvas-edge-label" x="${midX}" y="${(startY + endY) / 2 - 6}">${escapeHtml(String(label))}</text>`
            : ""
        }
      `
    })
    .join("")

  const nodeMarkup = state.draft.nodes
    .map((node) => {
      const position = positions[node.id]
      const outgoingTargets = (outgoingMap[node.id] || []).map((edge) => edge.target).join(", ")
      return `
        <article class="canvas-card graph-card" style="left:${position.x}px; top:${position.y}px; width:${cardWidth}px; height:${cardHeight}px;">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(node.label)}</strong>
            <span class="pill">${escapeHtml(node.type)}</span>
          </div>
          <div class="item-meta">${escapeHtml(node.id)}</div>
          <div class="item-meta">To: ${escapeHtml(outgoingTargets || "none")}</div>
        </article>
      `
    })
    .join("")

  els.canvasPreview.innerHTML = `
    <div class="canvas-stage" style="width:${stageWidth}px; height:${stageHeight}px;">
      <svg class="canvas-svg" viewBox="0 0 ${stageWidth} ${stageHeight}" preserveAspectRatio="none">
        ${edgeMarkup}
      </svg>
      ${nodeMarkup}
    </div>
  `
}

function renderValidation() {
  if (!state.validation) {
    els.validationStatus.textContent = "Not run"
    els.validationOutput.textContent = "No validation result yet."
    return
  }
  els.validationStatus.textContent = state.validation.valid ? "Valid" : "Invalid"
  els.validationOutput.textContent = jsonPretty(state.validation)
}

function renderPlan() {
  if (!state.plan) {
    els.planCaption.textContent = "No plan loaded"
    els.planOutput.innerHTML = ""
    return
  }
  els.planCaption.textContent = `Version ${state.plan.version}`
  els.planOutput.innerHTML = state.plan.steps
    .map(
      (step) => `
        <div class="plan-item">
          <div class="item-headline">
            <strong class="item-title">${step.index}. ${escapeHtml(step.label)}</strong>
            <span class="pill">${escapeHtml(step.node_type)}</span>
          </div>
          <div class="item-meta">${escapeHtml(step.node_id)}</div>
        </div>
      `
    )
    .join("")
}

function renderVersionDiff() {
  if (!state.versionDiff) {
    els.versionDiffOutput.textContent =
      state.selectedVersion && state.selectedVersion > 1
        ? "Compare to the previous version to inspect release changes."
        : "No version diff loaded."
    return
  }
  els.versionDiffOutput.textContent = jsonPretty(state.versionDiff)
}

function renderRuns() {
  els.runList.innerHTML = state.runs
    .map(
      (run) => `
        <button class="run-item ${run.id === state.selectedRunId ? "is-selected" : ""}" data-run-id="${escapeHtml(run.id)}">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(run.id.slice(0, 8))}</strong>
            <span class="pill">${escapeHtml(run.status)}</span>
          </div>
          <div class="item-meta">Version ${run.workflow_version}</div>
          <div class="item-meta">${escapeHtml(run.created_at)}</div>
        </button>
      `
    )
    .join("")
}

function renderRunEvents() {
  els.runEventCaption.textContent = state.selectedRunId || "No run selected"
  els.runEventList.innerHTML = state.runEvents
    .map(
      (event) => `
        <div class="event-item" data-level="${escapeHtml(event.level)}">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(event.event_type)}</strong>
            <span class="pill">${escapeHtml(event.level)}</span>
          </div>
          <div class="item-meta">${escapeHtml(event.message)}</div>
          <div class="item-meta">
            ${escapeHtml(event.timestamp)}${event.provider_key ? ` · ${escapeHtml(event.provider_key)}` : ""}
          </div>
          <pre class="output-box">${escapeHtml(jsonPretty(event.payload || {}))}</pre>
        </div>
      `
    )
    .join("")
}

function renderComments() {
  els.commentList.innerHTML = state.comments
    .map(
      (comment) => `
        <div class="event-item">
          <div class="item-headline">
            <strong class="item-title">Version ${comment.workflow_version}</strong>
            <span class="pill">${escapeHtml(comment.author_id.slice(0, 8))}</span>
          </div>
          <div class="item-meta">${escapeHtml(comment.created_at)}</div>
          <div>${escapeHtml(comment.body)}</div>
        </div>
      `
    )
    .join("")
}

function renderAdminView() {
  els.operationsSummaryOutput.textContent = state.operationsSummary
    ? jsonPretty(state.operationsSummary)
    : "No operational summary loaded."
  els.sourceControlOutput.textContent = state.sourceControl
    ? jsonPretty(state.sourceControl)
    : "No source-control data loaded."
  els.auditLogList.innerHTML = state.auditLogs
    .map(
      (entry) => `
        <div class="event-item">
          <div class="item-headline">
            <strong class="item-title">${escapeHtml(entry.action)}</strong>
            <span class="pill">${escapeHtml(entry.actor_id || "system")}</span>
          </div>
          <div class="item-meta">${escapeHtml(entry.created_at)}</div>
          <pre class="output-box">${escapeHtml(jsonPretty(entry.payload || {}))}</pre>
        </div>
      `
    )
    .join("")
}

function populateCredentialProviderOptions() {
  els.credentialProvider.innerHTML = `
    <option value="">Unscoped</option>
    ${state.providers
      .filter((provider) => provider.kind !== "observability")
      .map(
        (provider) => `
          <option value="${escapeHtml(provider.key)}">${escapeHtml(provider.display_name)}</option>
        `
      )
      .join("")}
  `
}

function getProvidersForNodeType(nodeType) {
  return state.providers.filter((provider) => provider.supported_node_types.includes(nodeType))
}

function getCredentialsForProvider(providerKey) {
  return state.credentials.filter(
    (credential) => !credential.provider_key || credential.provider_key === providerKey
  )
}

function defaultProviderForType(nodeType) {
  return defaultConfigForType(nodeType).provider || ""
}

function defaultConfigForType(nodeType) {
  return structuredClone(defaultConfigByType[nodeType] || {})
}

function createDefaultNode(nodeType, nodes) {
  const displayName =
    state.nodeTypes.find((item) => item.key === nodeType)?.display_name || nodeType.replaceAll("_", " ")
  return {
    id: createNodeId(nodeType, nodes),
    type: nodeType,
    label: displayName,
    config: defaultConfigForType(nodeType),
  }
}

function createNodeId(nodeType, nodes) {
  const base = nodeType.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "").toLowerCase() || "node"
  let counter = 1
  let candidate = `${base}_${counter}`
  const ids = new Set(nodes.map((item) => item.id))
  while (ids.has(candidate)) {
    counter += 1
    candidate = `${base}_${counter}`
  }
  return candidate
}

function createBlankWorkflow() {
  return {
    name: "",
    description: "",
    release_notes: "",
    settings: { max_concurrent_runs: 3 },
    nodes: [
      {
        id: "manual_trigger_1",
        type: "manual_trigger",
        label: "Manual Trigger",
        config: defaultConfigForType("manual_trigger"),
      },
    ],
    edges: [],
  }
}

function workflowToDraft(workflow) {
  return {
    name: workflow.name,
    description: workflow.description,
    release_notes: workflow.release_notes || "",
    nodes: workflow.nodes,
    edges: workflow.edges,
    settings: workflow.settings || { max_concurrent_runs: 3 },
  }
}

function resetEditor(draft) {
  state.selectedWorkflowId = null
  state.selectedVersion = null
  state.versions = []
  state.validation = null
  state.plan = null
  state.versionDiff = null
  state.runs = []
  state.selectedRunId = null
  state.runEvents = []
  state.comments = []
  stopRunPolling()
  state.draft = draft
  renderAll()
  setStatus("New Draft")
}

function upsertRun(run) {
  const index = state.runs.findIndex((item) => item.id === run.id)
  if (index === -1) {
    state.runs.unshift(run)
  } else {
    state.runs[index] = run
  }
}

function requireSelection() {
  if (!state.selectedWorkflowId || !state.selectedVersion) {
    throw new Error("Select a saved workflow version first.")
  }
}

function setStatus(label) {
  els.statusPill.textContent = label
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${state.authToken}`,
      ...(options.headers || {}),
    },
    ...options,
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      detail = payload.detail || jsonPretty(payload)
    } catch {
      // Fall back to the default status text.
    }
    throw new Error(detail)
  }

  if (response.status === 204) {
    return null
  }

  return response.json()
}

function parseJson(rawValue, errorMessage) {
  try {
    return JSON.parse(rawValue)
  } catch (error) {
    throw new Error(`${errorMessage} ${error.message}`)
  }
}

function jsonPretty(value) {
  return JSON.stringify(value, null, 2)
}

function normalizeEdgeCondition(value) {
  if (!value) {
    return null
  }
  const trimmed = value.trim()
  if (trimmed === "true") {
    return true
  }
  if (trimmed === "false") {
    return false
  }
  if (!Number.isNaN(Number(trimmed)) && trimmed !== "") {
    return Number(trimmed)
  }
  return trimmed
}

function omit(object, keys) {
  const clone = structuredClone(object || {})
  for (const key of keys) {
    delete clone[key]
  }
  return clone
}

function injectSelectedOption(optionsMarkup, selectedValue) {
  return optionsMarkup.replace(
    new RegExp(`value="${escapeRegExp(selectedValue)}"`),
    `value="${escapeHtml(selectedValue)}" selected`
  )
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

function handleError(error) {
  console.error(error)
  setStatus("Error")
  window.alert(error.message || String(error))
}
