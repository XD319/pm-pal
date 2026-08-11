---
name: prd-review-service
description: Review PRD drafts through a deployed pm-pal HTTP service. Use when the user wants to call a shared private-cloud or internal pm-pal API instead of a local repository checkout. This skill creates or reuses a project source, starts a project-scoped review, polls run status, fetches results, and summarizes findings without exposing raw secrets.
---

# Prd Review Service

## Overview

Use a deployed pm-pal HTTP API as the source of truth for review results. Reviews are **project-scoped**: create (or reuse) a project, attach PRD content as a **source**, then `POST /api/projects/{project_id}/reviews` with `source_id`. Poll until completion and summarize findings — do not echo raw secrets or full report payloads.

## Workflow

### 1. Resolve the service endpoint and auth

- Use the user-provided pm-pal base URL, or a preconfigured internal URL.
- If auth is enabled, send `X-API-Key` or `Authorization: Bearer …` only in headers. Never echo credentials in chat.
- Before first use, prefer `GET <base-url>/health` and `GET <base-url>/ready`.

### 2. Ensure a project and source

The review body does **not** accept inline `prd_text`. Strong callers should upload content as a project source first.

1. Create or select a project: `POST /api/projects` or `GET /api/projects`
2. Add the PRD:
   - Text: `POST /api/projects/<project_id>/sources` with `title`, `source_type` (e.g. `prd_text`), `content`, `is_prd`
   - File: `POST /api/projects/<project_id>/sources/upload`
   - URL / connector: `POST /api/projects/<project_id>/sources/from-url` only when the user explicitly wants server-side fetch
3. Keep the returned `source_id`

Prefer reading a local file and posting its text as `content` on a source. Avoid remote connector URLs unless the user asks for them. Do not send local filesystem paths as if the remote server could read them.

### 3. Start the review run

`POST <base-url>/api/projects/<project_id>/reviews`

```json
{
  "source_id": "<project-source-id>",
  "mode": "quick"
}
```

Optional: `model_preset_id`. Read the returned `run_id`.

### 4. Poll until completion

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>`

Wait until the run is completed or failed.

### 5. Fetch the result

Prefer:

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>/result`

If needed:

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>/report?format=json`

Summarize:

- `findings`
- `open_questions`
- `risk_items`
- `conflicts`
- review status / readiness signals

### 6. Respond in review-first mode

Provide:

- top ambiguities and missing requirement details
- concrete PRD rewrite suggestions
- a compact readiness judgment

Do not paste full raw reports, full request payloads, or authentication headers unless the user explicitly asks.

## Operating Rules

- Treat the deployed API as the system of record.
- Always use project + `source_id` for HTTP reviews (not legacy `/api/review`).
- Prefer uploading PRD text as a source over connector-backed fetch.
- Never reveal API keys, bearer tokens, or auth headers in your response.
- For coding-agent handoff preparation, use local CLI (`pm-pal prepare-handoff`) or MCP when the shared HTTP surface is review-only.
