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
- SQLite-backed persistence

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

### Provider Model

- LLM providers are abstracted behind a provider registry
- tool execution is abstracted behind tool providers
- observability sinks are abstracted behind observability providers
- credentials are stored separately from workflow definitions and resolved at execution time

## Built-In Providers

The current implementation includes built-in providers so the platform is functional without external vendor setup:

- `echo_llm`
  local placeholder LLM provider used by `llm_prompt`
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

On Linux/macOS:

```bash
export AWAP_DATABASE_URL="sqlite:///./custom-awap.db"
uv run awap-dev
```

### Notes About Persistence

- the schema is created automatically at startup
- there is no migration system yet
- if you make incompatible schema changes later, existing local databases may need manual handling

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

### Provider and Credential Routes

- `GET /providers`
- `GET /credentials`
- `POST /credentials`
- `GET /credentials/{credential_id}`

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

- no authentication or authorization yet
- no multi-tenant isolation
- no schema migration framework yet
- frontend graph editing is form-based rather than canvas-based
- `echo_llm` is a placeholder provider, not a real vendor integration
- no queue system beyond in-process background workers

## Recommended Next Steps

1. Add authentication, authorization, and multi-tenant boundaries.
2. Add schema migrations for safe persistence upgrades.
3. Add real provider plugins for external LLM vendors and operational tools.
4. Improve the editor with drag-and-drop graph layout and branching visualization.

## License

This repository includes a [LICENSE](LICENSE) file.
