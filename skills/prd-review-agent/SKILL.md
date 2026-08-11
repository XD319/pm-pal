---
name: prd-review-agent
description: Review PRD drafts with the local pm-pal project. Use when the user asks to assess a PRD draft for completeness, ambiguity, open questions, risks, or readiness for downstream coding-agent handoff. This skill saves PRD text to a file when needed, runs the pm-pal CLI, reads the generated JSON report, and optionally prepares Codex, Claude Code, or OpenClaw handoff requests.
---

# Prd Review Agent

## Overview

Use the local pm-pal repository as the source of truth. Run the CLI from the repository root, prefer JSON output, summarize review results first, and only prepare downstream execution requests when the user asks for them.

## Workflow

### 1. Locate the project root

Use the workspace directory that contains:

- `pyproject.toml`
- `pm_pal/main.py`
- `docs/mcp.md`

If the current workspace does not contain those files, stop and ask for the pm-pal repository path.

### 2. Prepare the PRD input

Prefer file input.

- If the user already gave a PRD file path, use it.
- If the user pasted draft text, save it to `outputs/_skill/current_prd.md` unless they requested another path. This keeps the draft under a gitignored generated-output directory.
- Use UTF-8 markdown.

Prefer:

`python -m pm_pal.main review --input <prd-file> --json`

Avoid `--text` for long drafts unless file creation is blocked.

### 3. Run the review

From the project root, run:

`python -m pm_pal.main review --input <prd-file> --json`

Read:

- `run_id`
- `status`
- `metrics`
- `artifacts.report_json_path`
- `artifacts.report_md_path`

If the command fails or the returned status is not `completed`, report the failure and stop.

### 4. Read the generated report

Prefer the JSON artifact path returned by the review command.

If needed, run:

`python -m pm_pal.main report --run-id <run_id> --format json --json`

Extract and summarize:

- `findings`
- `open_questions`
- `risk_items`
- `conflicts`
- `review_mode` or gating details when present

### 5. Check for a clarification loop before handoff

Read the report payload's `clarification` field.

- If `clarification.triggered` is `true` and `clarification.status` is `pending`, pause the workflow and ask the user the pending clarification questions before any handoff or execution prep.
- Treat this as a multi-turn loop. Keep the `run_id` plus each question's `id`, `question`, and `reviewer` so the next user reply can be mapped back to the persisted run.
- When the user answers, submit the answers through native project interfaces instead of inventing an updated review result.

Preferred answer-submission path:

`answer_review_clarification(run_id=<run_id>, answers=[{"question_id": "...", "answer": "..."}])`

Fallback when working through the local HTTP API:

`POST /api/projects/<project_id>/reviews/<run_id>/clarification`

- If neither MCP nor the local HTTP API is available for answer submission, ask the user to apply the clarification into the PRD itself and run a new review instead of pretending the clarification was persisted.
- Do not continue to `prepare-handoff` while clarification is still pending.

### 6. Respond in review-first mode

Provide:

- top issues and ambiguities
- missing acceptance criteria or scope boundaries
- concrete PRD rewrite suggestions
- whether the draft is ready for execution handoff
- pending clarification questions when the clarification gate is active

Do not jump straight to coding or task execution unless the user explicitly asks.
Do not paste the full PRD or the full report JSON back into chat unless the user explicitly asks for it.

### 7. Prepare downstream agent handoff only on request

When the user wants execution prep, run:

`python -m pm_pal.main prepare-handoff --run-id <run_id> --agent all --json`

Available agents:

- `codex`
- `claude_code`
- `openclaw`
- `all`

Return:

- `request_count`
- each agent's `request_path`
- each agent's `context_path`
- each agent's `prompt_path` when present

## Operating Rules

- Prefer `--input` plus a saved markdown file.
- Prefer `--json` for commands that the agent needs to parse.
- Treat the CLI as the system of record. Do not invent review results.
- The local CLI does not currently expose a dedicated clarification-answer subcommand. For clarification writeback, prefer MCP `answer_review_clarification`, then the local HTTP API.
- Do not use connector-backed `--source` inputs such as URLs, Feishu, or Notion unless the user explicitly asks for them. Those paths may fetch external content or use configured credentials.
- Prefer summarizing findings over echoing raw PRD text, raw reports, or large JSON blobs.
- Mention the `run_id` in your response so the user can reuse it later.
- When clarification is pending, surface the pending questions first and wait for the user's answers before preparing downstream execution requests.
- When the user answers a pending clarification, preserve each `question_id` and submit the answers back to the project before summarizing the refreshed result.
- If the user asks for another review after edits, run a new review instead of assuming old results still apply.
- If the user asks for native tool integration instead of CLI, mention that MCP is also available via `review_requirement`, `answer_review_clarification`, and `prepare_agent_handoff`.
