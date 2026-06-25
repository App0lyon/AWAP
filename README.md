# AWAP

AI Workflow Automation Platform

## What This Project Is

AWAP is a Python-first platform for creating, versioning, validating, executing, and monitoring AI workflows.

It currently provides:

- a FastAPI backend
- a browser-based workflow editor served by the same FastAPI app
- workflow graph authoring with nodes and edges
- workflow versioning with `draft` and `published` states
- execution planning and validation
- background workflow execution with durable run tracking
- provider abstractions for LLMs, tools, credentials, and observability
- bearer-token authentication and role-based access
- environment promotion with readiness checks and policy enforcement
- encrypted, scope-aware credentials
- lightweight schema migrations
- SQLite-backed persistence
- Postgres-compatible SQLAlchemy persistence for production deployments
- a queue abstraction with a SQLite lease-table adapter for local development

The frontend and backend are served together. There is no separate frontend build step right now.

## Current Feature Set

### Workflow Authoring

- create workflows as directed graphs of nodes and edges
- use built-in node types such as `manual_trigger`, `schedule_trigger`, `llm_prompt`, `decision`, `http_request`, and `notification`
- edit workflows in the browser
- save new versions instead of overwriting historical workflow definitions

### Workflow Lifecycle

- workflows have a stable logical `id`
- each saved definition has a numeric `version`
- versions can be `draft` or `published`
- publishing one version automatically resets the other versions of that workflow to `draft`

### Validation and Planning

- validate node configuration requirements
- validate references between nodes and edges
- reject cyclic workflow graphs
- generate execution plans in topological order

### Execution and Job Tracking

- start workflow runs through the API or the web editor
- execute runs in background worker threads
- track run state: `queued`, `running`, `succeeded`, `failed`
- track step state per node
- inspect run events for observability
- stream run events with Server-Sent Events
- pause, resume, cancel, and retry workflow runs
- route failed work into dead-letter records
- inspect monitoring alerts, worker health, and infrastructure status

### Provider Model

- LLM providers are abstracted behind a provider registry
- tool execution is abstracted behind tool providers
- observability sinks are abstracted behind observability providers
- credentials are stored separately from workflow definitions and resolved at execution time
- credentials can be scoped to workflows and environments
- environment policy can restrict HTTP hosts, SQL database paths, and file write roots
- run-event payloads are redacted before being stored

### Deployment Readiness

- inspect workflow-version readiness before promotion or environment execution
- check provider registration and credential configuration
- enforce readiness checks during environment promotion and environment-scoped runs
- report the active infrastructure model honestly: SQLite-backed run leases with in-process workers

## Built-In Providers

The current implementation includes built-in providers so the platform is functional without external vendor setup:

- `nvidia_build_free_chat`
  NVIDIA Build chat-completions provider used by `llm_prompt` and `ai_agent`
- `http_tool`
  performs real HTTP requests for `http_request`
- `notification_tool`
  simulates delivery for `notification`
- `repository_observer`
  stores run events in the database
- `logger_observer`
  writes run events to the application logger

## Technology Stack

- Python 3.11+
- `uv` for environment and dependency management
- FastAPI for the HTTP server
- SQLAlchemy for persistence
- SQLite as the default database
- Postgres with `psycopg` for production database deployments
- pytest for tests
- Ruff for linting
- plain HTML/CSS/JavaScript for the frontend editor

## Project Layout

```text
src/awap/
  api/         FastAPI app and routes
  ui/          Browser editor assets
  catalog.py   Built-in workflow node catalog
  database.py  Database engine and schema bootstrap
  domain.py    Pydantic models and workflow domain types
  main.py      Local development entrypoint
  providers.py Provider abstractions and built-in provider adapters
  repository.py Persistence layer
  runtime.py   Workflow execution runtime
  service.py   Application services
tests/
  test_api.py
  test_domain.py
  test_frontend.py
  test_providers.py
  test_runs.py
```

## Requirements

- Python 3.11 or newer
- `uv` installed on the machine

If `uv` is not installed yet:

```powershell
pip install uv
```

## Quick Start

### 1. Install dependencies

```powershell
uv sync --group dev
```

This creates `.venv` and installs all runtime and development dependencies.

### 2. Start the application

You can start the FastAPI app with either command:

```powershell
uv run awap-dev
```

or:

```powershell
uv run python -m uvicorn awap.api.app:create_app --factory --reload
```

The app starts on:

- backend + frontend: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`

### 3. Open the workflow editor

Open this in your browser:

```text
http://127.0.0.1:8000/
```

You can:

- create a workflow
- add nodes and edges
- save the workflow
- create new versions
- publish a version
- validate the selected version
- generate the execution plan
- start a run
- inspect runs and run events

## How To Start Everything

There is only one server to start.

The backend API and the frontend editor are both served by the FastAPI application, so this is enough:

```powershell
uv run awap-dev
```

Then use:

- `http://127.0.0.1:8000/` for the editor
- `http://127.0.0.1:8000/docs` for Swagger/OpenAPI

There is no separate Node/Vite/React dev server in the current architecture.

## Configuration

### Runtime Modes

AWAP has two intended runtime modes.

#### Local single-process AWAP

Local mode is the default:

```bash
export AWAP_MODE=local
uv run awap-dev
```

In this mode:

- the default database is `sqlite:///./awap.db`
- worker threads run in the FastAPI process
- `AWAP_WORKER_COUNT` defaults to `2`
- the queue adapter is the SQLite lease-table adapter
- the bootstrap admin token defaults to `awap-dev-admin-token`
- the local encryption seed is allowed for development credentials

This mode is convenient for development, tests, and demos. It is not the target production topology.

#### Production AWAP

Production mode requires explicit secrets and should use a production database:

```bash
export AWAP_MODE=production
export AWAP_DATABASE_URL="postgresql://awap:change-me@localhost:5432/awap"
export AWAP_BOOTSTRAP_ADMIN_TOKEN="replace-with-a-long-random-token"
export AWAP_SECRET_KEY="replace-with-a-long-random-secret"
uv run awap-dev
```

In this mode:

- `AWAP_BOOTSTRAP_ADMIN_TOKEN` is required
- `AWAP_SECRET_KEY` is required before credentials can be encrypted or decrypted
- Postgres URLs are normalized to the `psycopg` SQLAlchemy driver
- knowledge retrieval uses a pgvector sidecar table, `knowledge_vectors`, populated during document ingestion
- SQL migrations create composite indexes for workflow versions, run search, queued claims, events, approval tasks, environment releases, audit logs, dead letters, comments, and knowledge chunks
- the queue interface is explicit, so a managed queue adapter can replace the local SQLite lease-table adapter
- the API process defaults to `AWAP_WORKER_COUNT=0`; run workers separately with `uv run awap-worker`

Example production process split:

```bash
# API process
export AWAP_MODE=production
export AWAP_WORKER_COUNT=0
uv run awap-dev

# Worker process
export AWAP_MODE=production
export AWAP_WORKER_COUNT=4
uv run awap-worker
```

### Database

By default, the application uses:

```text
sqlite:///./awap.db
```

Override it with:

```powershell
$env:AWAP_DATABASE_URL = "sqlite:///./custom-awap.db"
uv run awap-dev
```

For Postgres:

```bash
export AWAP_DATABASE_URL="postgresql://awap:change-me@localhost:5432/awap"
uv run awap-dev
```

Postgres production deployments must have the `vector` extension available. AWAP migrations run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

and create the `knowledge_vectors` retrieval table with an HNSW vector index.

Optional pool settings:

```bash
export AWAP_DATABASE_POOL_SIZE=10
export AWAP_DATABASE_MAX_OVERFLOW=20
```

On Linux/macOS:

```bash
export AWAP_DATABASE_URL="sqlite:///./custom-awap.db"
uv run awap-dev
```

### Notes About Persistence

- the schema is created and migrated automatically at startup
- migrations are intentionally lightweight and versioned in `src/awap/migrations.py`
- incompatible data migrations may still need explicit migration code

## How The System Works

### Workflow Model

A workflow is a graph with:

- `nodes`
- `edges`
- `name`
- `description`
- `id`
- `version`
- `state`

### Versioning

- `POST /workflows` creates version `1` as `draft`
- `POST /workflows/{workflow_id}/versions` creates a new draft version
- `POST /workflows/{workflow_id}/versions/{version}/publish` publishes one version
- reads, planning, and validation accept an optional `version` query parameter

If no version is supplied during execution, the system prefers:

1. the published version
2. otherwise the latest version

### Execution Model

When a run starts:

1. the platform resolves the workflow version to execute
2. it validates the workflow
3. it builds an execution plan
4. it creates a durable run record
5. it executes steps in a background thread
6. it updates step state and run state in the database
7. it stores structured run events for observability

### Credentials

Credentials are stored independently from workflows.

Workflows reference credentials through node config such as:

```json
{
  "provider": "echo_llm",
  "credential_id": "credential-uuid"
}
```

The secret payload is not returned by the public credential listing endpoints.

## Web Editor Overview

The browser editor includes:

- workflow list
- version list
- node type palette
- provider list
- credential list
- workflow metadata editor
- node editor with JSON config
- edge editor
- flow preview
- validation output
- execution plan view
- run launcher
- run history
- run event viewer

Important current behavior:

- saving an edited existing workflow creates a new version rather than mutating the saved version in place
- node configuration is currently edited as JSON
- graph editing is form-based, not drag-and-drop

## API Overview

### Core Routes

- `GET /`
- `GET /health`
- `GET /docs`

### Workflow Routes

- `GET /workflows`
- `POST /workflows`
- `GET /workflows/{workflow_id}`
- `GET /workflows/{workflow_id}/versions`
- `POST /workflows/{workflow_id}/versions`
- `POST /workflows/{workflow_id}/versions/{version}/publish`
- `POST /workflows/{workflow_id}/validate`
- `POST /workflows/{workflow_id}/plan`

### Run Routes

- `GET /workflows/{workflow_id}/runs`
- `POST /workflows/{workflow_id}/runs`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/events/stream`
- `GET /runs/search`
- `POST /runs/{run_id}/pause`
- `POST /runs/{run_id}/resume`
- `POST /runs/{run_id}/cancel`
- `POST /runs/{run_id}/retry`

### Provider and Credential Routes

- `GET /providers`
- `GET /providers/{provider_key}/connection`
- `GET /credentials`
- `POST /credentials`
- `GET /credentials/{credential_id}`

### Environment, Readiness, and Operations Routes

- `GET /environments`
- `POST /environments`
- `GET /environments/{environment}/releases`
- `POST /workflows/{workflow_id}/promotions`
- `GET /workflows/{workflow_id}/versions/{version}/readiness`
- `GET /observability/summary`
- `GET /observability/alerts`
- `GET /worker-health`
- `GET /dead-letters`
- `GET /infrastructure/status`

## Example Development Flow

### Create and run a workflow from the UI

1. start the app with `uv run awap-dev`
2. open `http://127.0.0.1:8000/`
3. create or load a workflow
4. add nodes and edges
5. save it
6. validate it
7. generate the plan
8. run it
9. inspect the run and run events

### Run tests

```powershell
uv run pytest
```

### Run lint

```powershell
uv run ruff check
```

### Run CI-equivalent checks

```powershell
uv run ruff check
uv run pytest --cov=awap --cov-report=term-missing --cov-fail-under=80
```

## Development Commands

### Install/update environment

```powershell
uv sync --group dev
```

### Start app

```powershell
uv run awap-dev
```

### Start app with uvicorn directly

```powershell
uv run python -m uvicorn awap.api.app:create_app --factory --reload
```

### Run tests

```powershell
uv run pytest
```

### Run lint

```powershell
uv run ruff check
```

## Testing

The test suite currently covers:

- domain validation rules
- API behavior
- workflow versioning and publish flow
- workflow runs and job tracking
- provider and credential behavior
- frontend serving

Run everything with:

```powershell
uv run pytest
```

## Current Limitations

- no workspace or tenant isolation yet
- no managed external queue adapter yet; local workers use SQLite-backed leases through the queue abstraction
- no distributed worker deployment package yet
- secret management still relies on application-level encrypted payloads, not an external KMS or vault
- environment policy is enforceable, but policy authoring is still API-first
- frontend graph editing is form-based rather than drag-and-drop
- provider connection checks verify configuration but do not perform live vendor health probes by default
- local knowledge search uses the SQLite development vector adapter; production Postgres deployments use the pgvector retrieval table and HNSW index

## Recommended Next Steps

1. Add workspace or tenant isolation around workflows, credentials, runs, knowledge bases, and audit logs.
2. Implement a managed queue adapter, such as Redis, Postgres SKIP LOCKED, or a cloud queue, behind the queue interface.
3. Add an external secret backend integration and key rotation story.
4. Expand environment policy authoring and approval workflows in the UI.
5. Add live provider health probes and alert delivery integrations.

## License

This repository includes a [LICENSE](LICENSE) file.
