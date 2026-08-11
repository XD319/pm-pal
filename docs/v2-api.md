# HTTP API

FastAPI app: `PM Pal Project Space API` (version `5.0`).

Related docs: [quick-start.md](./quick-start.md) · [project-space.md](./project-space.md) · [callback-config.md](./callback-config.md) · [mcp.md](./mcp.md)

## Core flow

1. Create a project — `POST /api/projects`
2. Add a source — `POST /api/projects/{project_id}/sources` (or `.../from-url`, `.../upload`)
3. Start a review — `POST /api/projects/{project_id}/reviews` with `{ "source_id", "mode?", "model_preset_id?" }`
4. Poll — `GET /api/projects/{project_id}/reviews/{run_id}`
5. Result / report — `.../result` · `.../report?format=md|json|html|csv`

CLI and MCP call the review service layer directly (no project_id required).

## Start the server

```bash
python main.py
# or: uvicorn pm_pal.server.app:app --host 0.0.0.0 --port 8000 --reload
```

## Auth and rate limits

Dual-read env: `PM_PAL_*` preferred, `MARRDP_*` fallback.

| Variable | Typical local | Shared host |
|----------|---------------|-------------|
| `PM_PAL_API_AUTH_DISABLED` | `true` | `false` |
| `PM_PAL_API_KEY` / `PM_PAL_API_BEARER_TOKEN` | empty | set |
| `PM_PAL_API_RATE_LIMIT_DISABLED` | `true` | `false` |

- Send `X-API-Key` or `Authorization: Bearer …`
- Auth on with no credentials configured → controlled `503`
- Rate limit applies to `POST .../reviews` → `429` + `Retry-After`

## Review endpoints

```bash
# Create project
curl -X POST "http://127.0.0.1:8000/api/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"Demo"}'

# Add PRD text as a source
curl -X POST "http://127.0.0.1:8000/api/projects/<project_id>/sources" \
  -H "Content-Type: application/json" \
  -d '{"title":"Sample","source_type":"prd_text","content":"# PRD\n...","is_prd":true}'

# Start review
curl -X POST "http://127.0.0.1:8000/api/projects/<project_id>/reviews" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: local-dev-secret" \
  -d '{"source_id":"<source_id>","mode":"quick"}'
```

Also useful:

- `GET /api/projects/by-run/{run_id}` — resolve owning project
- Clarification / revision / roadmap / artifact / SSE routes under the same `/reviews/{run_id}/...` prefix

## Other `/api` surfaces

| Area | Prefix | Notes |
|------|--------|-------|
| Projects / sources / providers | `/api/projects`, `/api/provider-*`, `/api/model-presets` | Project space |
| Domain objects | `/api/projects/{id}/evidence`, `insights`, `opportunities`, `prd-versions`, `deliveries`, … | Evidence → delivery loop |
| Agent | `/api/agent/conversations`, `/api/agent/tasks/...` | Confirmable tasks; see [project-space.md](./project-space.md) |
| Templates | `/api/templates` | Prompt/template metadata |
| Connectors | `/api/feishu/events`, `/api/notion/events`, `/api/github/events` | Webhooks only enqueue sync; details in [callback-config.md](./callback-config.md) |
| Feishu helpers | `/api/feishu/workspaces/...`, `/api/feishu/clarification` | Workspace review helpers |
| Health | `/health`, `/ready` | Load balancer probes |

`POST /api/feishu/submit` returns **410** — use project-scoped reviews.

## Removed (do not call)

- `POST /api/review` and all `/api/review/{run_id}...` variants
- `GET /api/runs`, `/api/compare`, `/api/trends`, `/api/stats`, `/api/report/{run_id}`, `/api/audit`
- `/api/pm`, `/api/decision`, `/api/v1` (product-agent)

## Outputs

HTTP runs write under `{PM_PAL_DATA_DIR}/outputs/<run_id>/` (default `data/outputs/`):

- `report.md`, `report.json`, `run_trace.json`
- parallel path may add `review_report.json`, `risk_items.json`, `open_questions.json`, …

## UI note

The Web shell is workspace-v5 (`/workspace`, `/materials`, `/deliveries`, …). Feishu cards and APIs may still emit `/run/<run_id>` (or legacy `/projects/{id}/reviews/{run_id}`); the SPA resolves the project via `GET /api/projects/by-run/{run_id}` and redirects to `/deliveries?project_id=…&run_id=…`.
