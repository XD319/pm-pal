# Deployment Guide

This guide describes recommended adoption paths for prd-pal with project space as the primary product surface.

Start here for first-time setup:

- [quick-start.md](./quick-start.md)
- [project-space.md](./project-space.md)
- [feishu-setup.md](./feishu-setup.md)

## Path 1: Local Skill + Local Repository

Use this when:

- PRD content is sensitive
- a single user or a small team is validating the workflow
- rapid iteration matters more than centralized management

Recommended entrypoints:

- local skill: `skills/prd-review-agent/`
- CLI: `python -m prd_pal.main review --input <file> --json`
- MCP: `python -m prd_pal.mcp_server.server`
- preferred caller contract: `prd_text` / local files first, connector-backed `source` only when explicitly needed

## Path 2: Shared Service + Project Space UI

Use this when:

- multiple users need the same review service
- you want one deployed version of the backend with project-scoped APIs
- you need centralized logging, auth, connector sync, and runtime settings

Recommended entrypoints:

- remote skill: `skills/prd-review-service/`
- HTTP API: project-scoped FastAPI routes on port `8000`
- Web UI: React project space at `/`
- preferred caller contract: create/select a project, attach sources, then `POST /api/projects/{project_id}/reviews`

## Recommended System Boundary

Treat the deployed review service as the review kernel first and the source-ingestion layer second.

- Strong callers should fetch third-party documents themselves and submit `prd_text`.
- Weak callers may rely on project-side `source` ingestion when they can only provide a document identifier or URL.
- Clarification loops and handoff decisions are usually best orchestrated by the caller's agent, while the project keeps the persisted review state and optional follow-up APIs.

## Container Deployment

Build and run:

```bash
docker-compose up --build
```

The container exposes:

- `GET /health` — process health (`service: prd-pal`)
- `GET /ready` — startup completion and outputs-directory writability
- `GET/POST /api/projects/...` — project space management and reviews
- `POST /api/feishu/events`, `/api/feishu/submit`, `/api/feishu/clarification`
- `POST /api/notion/events`, `POST /api/github/events` — connector sync webhooks

Legacy global routes such as `POST /api/review` and `GET /api/report/{run_id}` were removed in Phase 2. Use project-scoped report routes instead.

## Health Checks

Use these checks for load balancers, orchestrators, and monitoring:

- `GET /health` — process-level health
- `GET /ready` — startup completion plus output-directory writability

`Dockerfile` and `docker-compose.yml` include health checks using `/health`.

## Connector Callbacks

For production webhook setup (Feishu encrypt/signature, Notion signing, GitHub App/PAT), read [callback-config.md](./callback-config.md).

## Security Notes

- Prefer private deployment for internal PRDs.
- Use local-skill mode when PRD text should not leave the developer machine.
- For remote skill mode, prefer submitting `prd_text` directly instead of remote connector sources unless explicitly needed.
- If connector-backed `source` is enabled, treat third-party auth, permissions, and rate limits as integration-layer concerns rather than the core review contract.
- Configure `MARRDP_API_AUTH_DISABLED=false` and API credentials for shared deployments.
- Do not return full report payloads or auth headers to users unless they explicitly ask for them.

## Recommended Rollout

1. Start with the local skill and local CLI.
2. Keep MCP available for agent-native integrations.
3. Normalize around `prd_text` for strong callers before turning on enterprise source connectors.
4. Deploy the FastAPI service privately with project space enabled.
5. Add auth, TLS, and reverse proxying at the platform layer.
6. Configure connector webhooks per [callback-config.md](./callback-config.md).
7. Introduce the remote skill for shared service access.
8. Turn on Feishu, Notion, or GitHub realtime sync only when weak-caller support or centralized ingestion is required.

## CI Validation

GitHub Actions workflow `.github/workflows/ci.yml` runs:

- `pip install -e ".[test]"` and `pytest -q`
- `npm ci`, `npm test -- --run`, and `npm run build` in `frontend/`
