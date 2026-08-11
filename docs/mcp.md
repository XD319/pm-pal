# Requirement Review MCP Guide

This document explains how to run the repository's MCP server with review-first positioning.

## Positioning

The MCP surface should be understood in two layers:

- Core tools: review-only tools used to create and fetch review results
- Extension tools: retained source-integration, clarification, orchestration, and governance tools built on top of those review results

If you are integrating this repository for the first time, start with the core tools only.

As a default boundary:

- Prefer `prd_text` or `prd_path` when the MCP client can already fetch source content.
- Use connector-backed `source` when the MCP client is a weak caller that only knows the remote document identifier or URL.

## Prerequisites

- Python 3.11+
- Required model credentials in the environment
- Repository root as the working directory

## Start The MCP Server

```powershell
python -m pm_pal.mcp_server.server
```

The server uses stdio transport and is normally launched by an MCP client.

## Core Review Tools

These are the primary tools to emphasize:

- `ping`
  - Health check for MCP connectivity
- `review_requirement`
  - Review-only facade for the project's main review-engine positioning
  - Accepts `source`, `prd_text`, `prd_path`, plus `metadata` or `options`
  - `source` supports local files, public text URLs, and authenticated Feishu/Lark documents
  - Returns `review_id`/`run_id`, `findings`, `open_questions`, `risk_items`, `conflicts`, `report_path`, and `review_mode`
- `review_prd`
  - Retained fuller workflow entrypoint for clients that still want metrics and downstream artifact paths
  - Accepts `prd_text`, `prd_path`, or `source`
  - Returns `run_id`, review metrics, and artifact paths
  - Surfaces controlled Feishu connector failures with explicit error codes such as `AUTHENTICATION_FAILED`, `PERMISSION_DENIED`, `DOCUMENT_NOT_FOUND`, and `UNSUPPORTED_DOCUMENT_TYPE`
- `get_report`
  - Fetches `report.md` or `report.json` for a completed run
- `prepare_agent_handoff`
  - Prepares adapter-specific request payloads for `codex`, `claude_code`, or `openclaw`
  - Accepts either `run_id` or fresh PRD inputs (`source`, `prd_text`, `prd_path`)
  - Returns request file paths, context paths, prompt paths, and structured request payloads

Recommended input choice:

- Use `prd_text` for strong callers that already fetched the requirement content.
- Use `source` for weak callers that need pm-pal to fetch and normalize the source document.

Recommended first integration flow:

1. Call `ping`
2. Call `review_requirement`
3. Use the returned `review_id` or `run_id`
4. Call `get_report`
5. Optionally call `prepare_agent_handoff` when a downstream coding agent should continue from the reviewed artifacts

Feishu source setup:

- Set `MARRDP_FEISHU_APP_ID` and `MARRDP_FEISHU_APP_SECRET` in the MCP server environment before calling `review_requirement` or `review_prd` with Feishu sources.
- Use `MARRDP_FEISHU_OPEN_BASE_URL` only when you need a non-default Open API base URL.
- Supported Feishu document types are `wiki`, `docx`, and legacy `docs` sources that can be converted to `docx`.

## MCP vs HTTP / Feishu

Use MCP when an IDE agent or automation can call tools over stdio and already has its own access control.

Use FastAPI when you need project space, webhooks (`/api/feishu/events` etc.), or shared multi-user hosting. Start reviews with `POST /api/projects/{project_id}/reviews` — not the removed `/api/feishu/submit`. Connector env and signatures: [callback-config.md](./callback-config.md).

## Core Example

Use any MCP client to call:

```json
{
  "tool": "review_requirement",
  "arguments": {
    "source": "docs/sample_prd.md"
  }
}
```

Then fetch the report:

```json
{
  "tool": "get_report",
  "arguments": {
    "run_id": "20260309T000000Z",
    "format": "md"
  }
}
```

Prepare all supported downstream handoffs from the same run:

```json
{
  "tool": "prepare_agent_handoff",
  "arguments": {
    "run_id": "20260309T000000Z",
    "agent": "all"
  }
}
```

## Core Outputs

HTTP server runs write under `{PM_PAL_DATA_DIR}/outputs/<run_id>/` (default `data/outputs/`). MCP/CLI may use `--outputs-root` (CLI default `outputs`).

Every review run centers on:

- `report.md`
- `report.json`
- `run_trace.json`

When the multi-reviewer path is selected, the run may also include:

- `review_report.json`
- `risk_items.json`
- `open_questions.json`
- `review_summary.md`

The implementation may also persist additional extension artifacts in the same run directory, but those are not required to understand the core MCP usage.

When the run starts from connector-backed `source`, the persisted artifacts also retain `source_metadata` where supported so downstream MCP clients can trace the origin of the reviewed document.

## Optional Human-Loop Tool

- `answer_review_clarification`
  - Applies user clarification answers to a persisted review result
  - Accepts `run_id`, `answers`, and `options`
  - Best used by caller-side agents that handle the user conversation but want project-side persistence for the refreshed review state

## Extension Tools

The repository retains additional MCP tools for downstream orchestration and governance:

- `generate_delivery_bundle`
- `approve_handoff`
- `get_review_workspace`
- `handoff_to_executor`
- `update_execution_task`
- `list_execution_tasks`
- `get_execution_status`
- `get_traceability`
- `get_template_registry`
- `get_audit_events`
- `retry_operation`

These tools are extension-layer tools. They remain available because the corresponding code is still in the repository.

## Extension Workflow Example

Only after a review result exists, an extension flow can continue with:

1. `generate_delivery_bundle`
2. `approve_handoff`
3. `handoff_to_executor`
4. `get_execution_status`
5. `get_traceability`

This is optional and should not be confused with the main review architecture.

## Notes For Client Authors

- Prefer `prd_text` for strong clients that can already fetch and normalize source content.
- Prefer `source` when the client is intentionally delegating document fetch and normalization to pm-pal.
- Keep `prd_text` and `prd_path` for backward compatibility.
- Treat `review_requirement` as the review-only facade.
- Treat `review_prd` as the richer compatibility surface when you intentionally need bundle-adjacent artifact paths in the same response.
- Treat review completion as successful even if you do not use any orchestration tool.
- Do not assume extension tools are deprecated; they are retained, just not first-layer.
