# Requirement Review API

This document describes the current FastAPI surface with review-first positioning.

For installation and Feishu rollout steps, read these first:

- [quick-start.md](./quick-start.md)
- [feishu-setup.md](./feishu-setup.md)

## Positioning

The HTTP API is centered on review result generation.

Core API flow:

1. Create or select a project
2. Submit a project-scoped review (`POST /api/projects/{project_id}/reviews`)
3. Poll review status
4. Fetch the generated report

Legacy global review endpoints (`POST /api/review`, `GET /api/runs`, `GET /api/compare`, `GET /api/trends`, `GET /api/stats`, `GET /api/report/{run_id}`, `GET /api/audit`) were removed in Phase 2. CLI and MCP continue to call the review service layer directly.

## Start The Server

```bash
python main.py
```

Or directly:

```bash
uvicorn prd_pal.server.app:app --host 0.0.0.0 --port 8000 --reload
```

## Shared-Environment Hardening

The FastAPI surface now supports a simple environment-controlled auth layer and a submission rate limiter.

Environment variables:

- `MARRDP_API_AUTH_DISABLED=true|false`
- `MARRDP_API_KEY=`
- `MARRDP_API_BEARER_TOKEN=`
- `MARRDP_API_RATE_LIMIT_DISABLED=true|false`
- `MARRDP_API_RATE_LIMIT_MAX_REQUESTS=5`
- `MARRDP_API_RATE_LIMIT_WINDOW_SEC=60`

Recommended modes:

- Local development: keep `MARRDP_API_AUTH_DISABLED=true` and `MARRDP_API_RATE_LIMIT_DISABLED=true`.
- Shared environment: set `MARRDP_API_AUTH_DISABLED=false`, configure `MARRDP_API_KEY` and/or `MARRDP_API_BEARER_TOKEN`, then enable rate limiting with `MARRDP_API_RATE_LIMIT_DISABLED=false`.

Auth behavior:

- Send `X-API-Key: <secret>` or `Authorization: Bearer <token>`.
- If auth is enabled but no credentials are configured, the API returns a controlled `503` instead of exposing the surface anonymously.
- Invalid or missing credentials return controlled `401` responses.
- Run-level Feishu H5 requests may omit the API key only when they carry matching `open_id`, `tenant_key`, and `embed=feishu` context for that run on project-scoped review routes.
- Project management endpoints such as `GET /api/projects` still require API credentials when auth is enabled.

Rate-limit behavior:

- The limiter applies to `POST /api/projects/{project_id}/reviews`.
- When the limit is exceeded, the API returns `429` with `detail.code = rate_limit_exceeded` and a `Retry-After` header.

Controlled errors:

- Request validation failures return `422` with `detail.code = request_validation_error`.
- Unexpected server failures return `500` with `detail.code = internal_server_error`.

## Core Review Endpoints

Project-scoped review APIs live under `/api/projects/{project_id}/reviews`. The legacy `POST /api/review` route was removed; see [project-space.md](./project-space.md).

### `POST /api/projects/{project_id}/reviews`

Create one review run for a project source.

Request body accepts:

- `source_id`
- `mode`
- `model_preset_id`

Example with API key:

```bash
curl -X POST "http://127.0.0.1:8000/api/projects/project_abc/reviews" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: local-dev-secret" \
  -d '{"source_id": "source_xyz", "mode": "quick"}'
```

### `GET /api/projects/{project_id}/reviews/{run_id}`

Poll run status.

### `GET /api/projects/{project_id}/reviews/{run_id}/result`

Fetch structured review output when the run completes.

### `GET /api/projects/{project_id}/reviews/{run_id}/report?format=md|json|html|csv`

Download report artifacts for a completed run.

---

## Legacy endpoints removed (Phase 2)

The following routes are no longer exposed over HTTP:

- `POST /api/review` and all `/api/review/{run_id}...` variants
- `GET /api/runs`
- `GET /api/compare`
- `GET /api/trends`
- `GET /api/stats`
- `GET /api/report/{run_id}`
- `GET /api/audit` (project-scoped audit may return in a later phase)

Use CLI (`python -m prd_pal.main`) or MCP for direct service-layer access without these HTTP routes.

---

## Feishu Entry Endpoints

The service also exposes a thin Feishu entry layer. This layer does not implement review logic itself. It only performs protocol conversion, signature verification, event handling, and review submission handoff into the existing review queue.

Production review submission is expected to enter through Feishu plugin or message-card signed callbacks. The browser H5 page is for result viewing, clarification, and follow-up actions; it is not a production direct-submit client for `/api/feishu/submit`.

## Feishu Plugin Rollout Checklist

Use this checklist when the engineering team is wiring the Feishu app to the review engine.

1. Expose the backend over HTTPS.
2. Prepare one Feishu app with:
   - document access capability for the Feishu connector
   - event subscription callback delivery
   - plugin or card action HTTP calls into this backend
   - an H5 page entry that opens the run detail page
3. Copy `.env.example` to `.env` and fill in all required Feishu variables.
4. Start the backend and finish the challenge handshake on `/api/feishu/events`.
5. Submit one signed review callback to `/api/feishu/submit`.
6. Open the H5 result page with `embed=feishu`, `open_id`, and `tenant_key`.
7. Trigger one clarification answer through `/api/feishu/clarification`.
8. Verify `outputs/<run_id>/entry_context.json` and `outputs/<run_id>/audit_log.jsonl`.

### Required Feishu App Configuration

At minimum, the Feishu app team needs to configure:

- App credentials:
  - `App ID`
  - `App Secret`
- Webhook signing secret:
  - used as `MARRDP_FEISHU_WEBHOOK_SECRET`
- Event callback URL:
  - `POST https://<your-domain>/api/feishu/events`
- Review submit callback URL:
  - `POST https://<your-domain>/api/feishu/submit`
- Clarification callback URL:
  - `POST https://<your-domain>/api/feishu/clarification`
- H5 page URL template:
  - `https://<your-domain>/run/<run_id>?embed=feishu&open_id=<open_id>&tenant_key=<tenant_key>`

### Required Environment Variables

These are the minimum Feishu-related variables for a production-capable backend:

- `MARRDP_FEISHU_APP_ID`
- `MARRDP_FEISHU_APP_SECRET`
- `MARRDP_FEISHU_SIGNATURE_DISABLED`
- `MARRDP_FEISHU_WEBHOOK_SECRET` when signatures are enforced
- `MARRDP_PUBLIC_BASE_URL`

Recommended companion variables:

- `MARRDP_FEISHU_OPEN_BASE_URL=https://open.feishu.cn`
- `MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC=300`
- `MARRDP_FEISHU_NOTIFICATION_DRY_RUN=true|false`
- `MARRDP_FEISHU_NOTIFICATION_CHANNELS=webhook|openapi|both`
- `MARRDP_FEISHU_NOTIFICATION_RECEIVE_ID_TYPE=open_id|user_id|union_id|email|chat_id`
- `MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID=`
- `MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL=`
- `MARRDP_API_AUTH_DISABLED=false`
- `MARRDP_API_KEY` and/or `MARRDP_API_BEARER_TOKEN`

Environment variables for Feishu request verification:

- `MARRDP_FEISHU_SIGNATURE_DISABLED=true|false`
- `MARRDP_FEISHU_WEBHOOK_SECRET=`
- `MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC=300`

Recommended modes:

- Local development: keep `MARRDP_FEISHU_SIGNATURE_DISABLED=true`.
- Shared Feishu integration: set `MARRDP_FEISHU_SIGNATURE_DISABLED=false`, configure `MARRDP_FEISHU_WEBHOOK_SECRET`, and keep a reasonable timestamp tolerance.
- If signatures are enabled and `MARRDP_FEISHU_WEBHOOK_SECRET` is empty, callback endpoints return `detail.code = feishu_signature_not_configured`.

### `POST /api/feishu/events`

Feishu event ingress.

Current behavior:

- Supports challenge handshake responses.
- Performs basic signature verification when Feishu signature checks are enabled.
- Returns a simple acknowledgment for non-challenge events.

Challenge example:

```bash
curl -X POST "http://127.0.0.1:8000/api/feishu/events" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"url_verification\",\"challenge\":\"challenge-token\"}"
```

Response:

```json
{
  "challenge": "challenge-token"
}
```

### `POST /api/feishu/submit`

Submit a review from a Feishu-side integration payload.

Request body accepts:

- `source`
- `prd_text`
- `mode`
- `open_id`
- `user_id`
- `tenant_key`
- `metadata`
- Optional LLM override fields already supported by `POST /api/review`

Behavior notes:

- The endpoint reuses the existing review submission flow and returns the same `run_id` contract.
- In production this endpoint is for Feishu signed callbacks only. Do not use the browser H5 page as a direct submitter.
- `source` is preferred. If `source` is omitted, `prd_text` is required.
- The Feishu adapter stores context such as `open_id`, `tenant_key`, and `trigger_source=feishu` inside `audit_context.client_metadata`.
- The backend also persists entry metadata to `outputs/<run_id>/entry_context.json` for lightweight access control and audit tracing.

Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/feishu/submit" \
  -H "Content-Type: application/json" \
  -d "{\"source\":\"feishu://docx/doc-token\",\"mode\":\"quick\",\"open_id\":\"ou_xxx\",\"tenant_key\":\"tenant_xxx\"}"
```

Response:

```json
{
  "run_id": "20260309T000000Z"
}
```

### `POST /api/feishu/clarification`

Submit one clarification answer from a Feishu card or plugin action.

Request body accepts:

- `run_id`
- `question_id`
- `answer`
- `open_id`
- `user_id`
- `tenant_key`
- `metadata`

Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/feishu/clarification" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\":\"20260309T000000Z\",\"question_id\":\"clarify-1\",\"answer\":\"Use successful dashboard arrival within 30 seconds.\",\"open_id\":\"ou_xxx\",\"tenant_key\":\"tenant_xxx\"}"
```

Typical response:

```json
{
  "run_id": "20260309T000000Z",
  "clarification_status": "answered",
  "has_pending_questions": false,
  "clarification": {},
  "result_page": {
    "path": "/run/20260309T000000Z",
    "url": "/run/20260309T000000Z"
  }
}
```

### `GET /api/runs`

List recent review runs discovered under `outputs/`.

This endpoint is file-based and lightweight: it scans run directories and reports artifact presence without eagerly parsing large review payloads.

Example:

```bash
curl -H "X-API-Key: local-dev-secret" "http://127.0.0.1:8000/api/runs"
```

Typical response:

```json
{
  "count": 2,
  "runs": [
    {
      "run_id": "20260309T030405Z",
      "status": "completed",
      "created_at": "2026-03-09T03:04:05+00:00",
      "updated_at": "2026-03-09T03:05:12+00:00",
      "artifact_presence": {
        "report_md": true,
        "report_json": true,
        "run_trace": true,
        "review_report_json": true,
        "risk_items_json": true,
        "open_questions_json": true,
        "review_summary_md": true
      }
    }
  ]
}
```

Each run includes:

- `run_id`
- `status`
- `created_at`
- `updated_at`
- `artifact_presence`

### `GET /api/review/{run_id}`

Fetch job status and node progress.

Example:

```bash
curl -H "X-API-Key: local-dev-secret" "http://127.0.0.1:8000/api/review/20260309T000000Z"
```

Typical response fields:

- `status`
- `progress.percent`
- `progress.current_node`
- `progress.nodes`
- `report_paths`
- `error` when the run has failed with a controlled connector or processing error

For failed Feishu connector runs, the response keeps `status: failed` and includes a structured error such as `AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `DOCUMENT_NOT_FOUND`, or `UNSUPPORTED_DOCUMENT_TYPE`.

### `GET /api/review/{run_id}/result`

Fetch the parsed result payload and stable artifact paths for a completed run.

If the run is still in progress, the endpoint returns `409` with `detail.code = result_not_ready`. If the run failed before writing `report.json`, the endpoint returns `409` with the controlled failure code and message so connector failures remain visible to API clients.

For Feishu-origin runs, access is guarded by the persisted entry context in `outputs/<run_id>/entry_context.json`. Provide matching `open_id` and `tenant_key` as query parameters or headers when opening the result page or calling protected APIs:

- Query parameters:
  - `?open_id=<open_id>&tenant_key=<tenant_key>`
- Headers:
  - `X-Feishu-Open-Id: <open_id>`
  - `X-Feishu-Tenant-Key: <tenant_key>`

This same explicit context applies to run status, result, report download, artifact preview, clarification, revision, roadmap, and SSE progress APIs. The backend does not use `Referer` as an identity source.

If the context is missing or does not match, the API returns controlled `403` responses such as:

- `detail.code = feishu_context_required`
- `detail.code = run_access_denied`

### `GET /api/report/{run_id}?format=md|json`

Download the main report artifact.

Examples:

```bash
curl -H "X-API-Key: local-dev-secret" "http://127.0.0.1:8000/api/report/20260309T000000Z?format=md"
curl -H "X-API-Key: local-dev-secret" "http://127.0.0.1:8000/api/report/20260309T000000Z?format=json"
```

For Feishu-origin runs, include the same explicit Feishu context:

```bash
curl "http://127.0.0.1:8000/api/report/20260309T000000Z?format=md&embed=feishu&open_id=ou_xxx&tenant_key=tenant_xxx"
```

## Primary Output Interpretation

For API consumers, every review run centers on:

- `report.md`
- `report.json`
- `run_trace.json`

When the multi-reviewer path is selected, the run may also include:

- `review_report.json`
- `risk_items.json`
- `open_questions.json`
- `review_summary.md`

The current service implementation may also write retained extension artifacts in the same directory. That does not change the API's review-first positioning.

When a review is created from `source`, connector metadata is preserved in run artifacts where supported. In particular, `report.json`, `run_trace.json`, and `delivery_bundle.json` retain `source_metadata` for downstream inspection.

## Supporting Governance Endpoints

The API also exposes supporting endpoints that belong to the extension or governance layer:

- `GET /api/templates`
- `GET /api/templates/{template_type}`
- `GET /api/audit`

These endpoints help inspect prompt/template metadata and audit events, but they are not required for the main review flow.

## H5 Result Page Integration

The frontend result page supports a Feishu embed mode:

- URL: `/run/<run_id>?embed=feishu&open_id=<open_id>&tenant_key=<tenant_key>`

Engineering notes:

1. Use the standard frontend build already served by FastAPI.
2. Open the URL inside Feishu WebView or H5 container.
3. Keep `open_id` and `tenant_key` on the URL or inject them as headers through your app gateway.
4. The page automatically switches to the compact layout when `embed=feishu` is present.
5. The same `open_id` and `tenant_key` are sent to protected result, report, clarification, follow-up action, and SSE progress APIs.

## Feishu Notifications

The Feishu notification layer can render interactive message cards and deliver them through webhook, OpenAPI, or both:

```dotenv
MARRDP_FEISHU_NOTIFICATION_DRY_RUN=true
MARRDP_FEISHU_NOTIFICATION_CHANNELS=webhook
MARRDP_FEISHU_NOTIFICATION_RECEIVE_ID_TYPE=open_id
MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID=
MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL=
MARRDP_PUBLIC_BASE_URL=https://<your-domain>
```

OpenAPI delivery uses the app tenant access token and sends `msg_type=interactive` with card JSON in `content`. The recipient is resolved from the run Feishu context first, then `MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID`. Non-dry-run OpenAPI delivery fails explicitly if credentials or recipient are missing.

`MARRDP_FEISHU_NOTIFICATION_CHANNELS=both` records OpenAPI and webhook attempts separately in `notifications.jsonl` and `audit_log.jsonl`. There is no automatic fallback from one channel to the other.

## Local Mock And Joint Debug

### Option A: Skip signature verification locally

Set:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=true
```

Then use plain `curl` requests against:

- `/api/feishu/events`
- `/api/feishu/submit`
- `/api/feishu/clarification`

### Option B: Exercise signature logic

Set:

```dotenv
MARRDP_FEISHU_SIGNATURE_DISABLED=false
MARRDP_FEISHU_WEBHOOK_SECRET=replace-with-local-secret
```

Then send Feishu-style signed requests from your local mock client or API test collection.

### Local End-To-End Check

1. `docker-compose up --build`
2. Submit a signed run callback through `/api/feishu/submit`
3. Open `/run/<run_id>?embed=feishu&open_id=<open_id>&tenant_key=<tenant_key>`
4. Confirm the run directory contains:
   - `report.json`
   - `entry_context.json`
   - `audit_log.jsonl`

## Production Deployment Notes

1. Put the backend behind HTTPS and a stable public hostname.
2. Persist `/app/outputs` to disk or network storage.
3. Keep Feishu signature verification enabled.
4. Rotate `MARRDP_FEISHU_APP_SECRET`, `MARRDP_FEISHU_WEBHOOK_SECRET`, and API credentials through your secret manager.
5. Re-run the event subscription challenge handshake after callback URL changes.
6. Smoke-test:
   - one `events` challenge
   - one `submit`
   - one result-page open in Feishu H5
   - one clarification answer

## What The HTTP API Does Not Currently Expose

The current FastAPI app does not expose approval or execution-orchestration endpoints directly.

Those retained orchestration capabilities are currently emphasized through the MCP layer rather than the HTTP layer.

