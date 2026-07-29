# prd-pal

[中文](./README.md) | [English](./README.en.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=0A0A0A)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)
![Feishu](https://img.shields.io/badge/Feishu-Integrated-3370FF)
![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)

`prd-pal` is a PRD and requirement review service organized around **project space**: create projects, attach sources, start reviews, and inspect run history. Feishu remains the primary end-user entry; Web and CLI are retained for trial, integration, and development.

## What Project Space Delivers

1. Create projects and configure model connections
2. Add PRD sources (text, files, Feishu/Notion/GitHub/URL links)
3. Start reviews from project sources (`POST /api/projects/{project_id}/reviews`)
4. View run status, results, reports, and follow-up actions inside the project
5. Sync external document changes through connector webhooks

## 30-Second Start

1. Follow [docs/quick-start.md](./docs/quick-start.md) to start locally
2. Open the web home page, create a project, and add a sample PRD source
3. Start a review from the project page and open the result view
4. Optionally wire Feishu using [docs/feishu-setup.md](./docs/feishu-setup.md)

## Requirements

- Python `3.11+`
- Node.js `22+`
- A valid model API key

## Connectors Overview

| Connector | Purpose | Realtime callback |
|-----------|---------|-------------------|
| Feishu | Feishu docs, events, submit | `POST /api/feishu/events`, `/api/feishu/submit` |
| Notion | Notion page ingestion | `POST /api/notion/events` |
| GitHub | README, issues, PRs | `POST /api/github/events` |
| URL | Public web pages | — |
| Local file | Local file paths | — |

See [docs/callback-config.md](./docs/callback-config.md) for webhook and signature setup.

## Recommended Reading

- Project space and review APIs:
  - [docs/project-space.md](./docs/project-space.md)
  - [docs/v2-api.md](./docs/v2-api.md)
- Quick start:
  - [docs/quick-start.md](./docs/quick-start.md)
- Feishu rollout:
  - [docs/feishu-setup.md](./docs/feishu-setup.md)
  - [docs/feishu-user-guide.md](./docs/feishu-user-guide.md)
- Deployment and callbacks:
  - [docs/deployment-guide.md](./docs/deployment-guide.md)
  - [docs/callback-config.md](./docs/callback-config.md)

## Local Quick Start

### 1. Clone

```bash
git clone <your-repo-url>
cd prd-pal
```

### 2. Configure `.env`

```bash
copy .env.example .env
```

Minimum local setup:

```dotenv
OPENAI_API_KEY=your-key
SMART_LLM=openai:gpt-5-nano
FAST_LLM=openai:gpt-5-nano
STRATEGIC_LLM=openai:gpt-5-nano
```

### 3. Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
cd frontend && npm install && cd ..
```

### 4. Start services

```bash
start-dev.cmd
```

Default addresses:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- Health: `http://127.0.0.1:8000/health`
- Ready: `http://127.0.0.1:8000/ready`

### 5. Validate the local flow

1. Create a project on the home page
2. Add a sample PRD source and start a review
3. Confirm the result page shows progress, summary, and report downloads

CLI alternative:

```bash
prd-pal review --input docs/sample_prd.md
```

## Docker

```bash
docker-compose up --build
```

Dev-mode frontend:

```bash
docker-compose --profile dev up dev
```

## Common Entry Points

### Web (project space)

- Home: `http://127.0.0.1:5173/`
- Review API prefix: `/api/projects/{project_id}/reviews`

### Feishu (primary user entry)

- Work entry: `https://<your-domain>/feishu`
- H5 result URL: `/run/<run_id>?embed=feishu&open_id=<open_id>&tenant_key=<tenant_key>`

### CLI / MCP

```bash
prd-pal review --input docs/sample_prd.md
python -m prd_pal.mcp_server.server
```

### Primary HTTP APIs (project-scoped)

- `POST /api/projects/{project_id}/reviews` — start a review
- `GET /api/projects/{project_id}/reviews/{run_id}` — poll status
- `GET /api/projects/{project_id}/reviews/{run_id}/result` — structured result
- `GET /api/projects/{project_id}/reviews/{run_id}/report?format=md|json|html|csv` — download report

Global `/api/review` routes were removed in Phase 2. See [docs/v2-api.md](./docs/v2-api.md).

## Outputs

Each run writes artifacts under `outputs/<run_id>/`:

- `report.md`, `report.json`, `run_trace.json`
- parallel review path may also include `review_report.json`, `risk_items.json`, `open_questions.json`, `review_summary.md`

## Validation

```bash
pytest -q
cd frontend && npm test -- --run && npm run build
```


## Decision Workbench Demo

Run prd-pal demo seed (no API key required), then open /workbench?product_id=demo-mobile-commerce. The workbench demonstrates the evidence -> agent draft -> human approval -> PRD quality gate -> delivery trace. See [docs/decision-workbench-demo.md](docs/decision-workbench-demo.md).
