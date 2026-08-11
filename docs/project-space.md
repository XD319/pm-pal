# Project Space

PM Pal organizes work around a **project space**. Create a project, add materials (evidence / PRD sources), drive tasks through the Agent, then review and deliver.

## Web UI

| Route | Purpose |
|-------|---------|
| `/workspace` | Home, conversations, Ask Agent |
| `/materials` | Evidence + PRD sources |
| `/decisions` | Insights / opportunities |
| `/deliveries` | PRD versions + delivery records |
| `/confirmations` | Pending Agent tasks |
| `/settings` | Provider / secrets status |

Bind context with `?project_id=<id>` (optional `conversation_id`).

### Recommended flow

1. Open `/workspace`, create or select a project.
2. Under **Materials**, attach a PRD (Feishu URL, file upload, or paste).
3. Use **Ask Agent**; approve or dismiss under **Confirmations**.
4. Review outcomes under **Decisions** / **Deliveries**; start a formal review via API when needed.

Agent defaults to draft-then-confirm and does not overwrite source documents. See `/api/agent/*` below.

## Review APIs

- `POST /api/projects/{project_id}/reviews` — start from a project source (`source_id`)
- `GET /api/projects/{project_id}/reviews/{run_id}` — status
- `GET .../result` · `.../report?format=md|json|html|csv`
- `GET /api/projects/by-run/{run_id}` — resolve project for a run

HTTP details and examples: [v2-api.md](./v2-api.md).

## Agent APIs

- `POST /api/agent/conversations` · `POST .../messages`
- `GET /api/agent/conversations` · `GET .../{id}`
- `GET /api/agent/tasks/{id}` · `POST .../confirm` · `POST .../retry`
- `GET /api/agent/tasks/{id}/progress/stream` — SSE

## Domain objects

Under `/api/projects/{project_id}/`: evidence, insights, opportunities, prd-versions, deliveries, summary, trace (see OpenAPI / source routers).

## Model provider

1. Generate a Fernet key and set `PM_PAL_SECRETS_MASTER_KEY` (or `MARRDP_SECRETS_MASTER_KEY`).
2. Check **Settings** for connection status; local runs can use `.env` model keys without saved connections.
3. Optional: create model presets via API and attach them to a project.

Secrets live in `{PM_PAL_DATA_DIR}/project_space.sqlite3` and are never returned by the API.

## Data layout

| Path | Contents |
|------|----------|
| `PM_PAL_DATA_DIR` (default `data/`) | SQLite, connector state |
| `{PM_PAL_DATA_DIR}/outputs/<run_id>/` | Review artifacts |
| `.../workspace.sqlite3` | Workspace helpers |
| `.../project_space.sqlite3` | Projects, sources, providers, domain objects |

CLI default `--outputs-root` is repo-relative `outputs` unless overridden; the HTTP server uses `PM_PAL_DATA_DIR`.

## Connectors

Feishu / Notion / GitHub / URL / local file. Webhooks enqueue sync for a single in-process worker.

- Events: `/api/feishu/events`, `/api/notion/events`, `/api/github/events`
- `POST /api/feishu/submit` → **410**; use project-scoped reviews

Signature and env vars: [callback-config.md](./callback-config.md).
