---
name: prd-review-service
description: Review PRD drafts through a deployed prd-pal HTTP service. Use when the user wants to call a shared private-cloud or internal prd-pal API instead of a local repository checkout. This skill submits PRD content to the review API, polls run status, fetches review results, and summarizes findings without exposing raw secrets.
---

# Prd Review Service

## Overview

Use a deployed prd-pal HTTP API as the source of truth for review results. Prefer sending PRD content as request JSON, treat the service primarily as a review kernel, poll the run until completion, and summarize the review output instead of echoing raw payloads.

## Workflow

### 1. Resolve the service endpoint and auth

Require a concrete base URL before sending review traffic.

- Use the user-provided prd-pal base URL, or a preconfigured internal URL.
- If the service requires authentication, send the API key in headers only. Never echo it back in chat.
- Before first use, prefer checking `GET <base-url>/health` and `GET <base-url>/ready`.

### 2. Prepare the PRD payload

Prefer request JSON content over server-side file paths.

- If the user gave a local PRD file, read the file and submit its content as `prd_text`.
- If the user pasted the draft directly, submit it as `prd_text`.
- Treat `prd_text` as the default contract for strong callers that can already fetch requirement content.
- Do not send remote connector `source` values such as URLs, Feishu, or Notion unless the user explicitly asks for them.
- Treat remote connector-backed `source` as an integration boundary for weak callers that can only provide the source location, not the document content itself.
- Do not send local machine paths as `prd_path` to a remote service unless the user confirms the server can access the same filesystem.

### 3. Start the review run

Submit:

`POST <base-url>/api/projects/<project_id>/reviews`

Example body:

```json
{
  "source_id": "<project-source-id>"
}
```

Read the returned `run_id`.

### 4. Poll until completion

Poll:

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>`

Wait until the run is completed or failed.

### 5. Fetch the result

Prefer:

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>/result`

If needed, fetch:

`GET <base-url>/api/projects/<project_id>/reviews/<run_id>/report?format=json`

Extract and summarize:

- `findings`
- `open_questions`
- `risk_items`
- `conflicts`
- review status and readiness signals

### 6. Respond in review-first mode

Provide:

- top ambiguities and missing requirement details
- concrete PRD rewrite suggestions
- a compact readiness judgment

Do not paste full raw reports, full request payloads, or authentication headers back into chat unless the user explicitly asks.

## Operating Rules

- Treat the deployed API as the system of record.
- Prefer `prd_text` for remote submission.
- Treat the remote API as review-first. Source fetching, human clarification conversations, and handoff decisions are usually better orchestrated by the caller's agent unless the user explicitly wants the service to own those steps.
- Avoid connector-backed remote `source` values unless the user explicitly requests them.
- Never reveal API keys, bearer tokens, or auth headers in your response.
- If the user wants downstream coding-agent handoff generation, explain that the current shared HTTP flow is review-first; use local CLI or MCP when handoff prep is required.
