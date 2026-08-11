# pm-pal

[中文](./README.md) | [English](./README.en.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=0A0A0A)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)
![Feishu](https://img.shields.io/badge/Feishu-Integrated-3370FF)
![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)

**An agent for product managers’ daily work** — connect feedback, evidence, insights, PRDs, reviews, and delivery into one confirmable loop so PMs spend less time on busywork and more on decisions.

Self-hosted and private-deployable. Data stays in your project space by default. Use the Web workspace, Feishu, CLI, or MCP.

---

## Why pm-pal

Product managers often burn time on:

- Pulling evidence from Feishu docs, notes, and meeting minutes
- Turning scattered feedback into insights and opportunities
- Drafting and revising PRDs
- Running reviews, clarifying questions, and preparing engineering handoff

`pm-pal` uses an **Agent + project space** to carry that workflow: you describe the job, the agent drafts the work, and **important actions only run after you confirm** — never silently overwrite source documents.

## Features

- **Agent workspace** — Ask in natural language (analyze feedback, generate opportunities, prepare a review); approve actions under Confirmations
- **Project space** — Materials, decisions, and deliveries stay scoped to a project with traceable context
- **Evidence → insight → opportunity → PRD** — End-to-end path from raw inputs to shippable docs
- **PRD / requirement review** — Multi-role review, risks, open questions, and exportable reports
- **Connectors** — Feishu, Notion, GitHub, URL, and local files, with webhook sync
- **Multiple entry points** — Web, Feishu H5, CLI (`pm-pal`), and MCP for IDE / automation agents
- **Self-hosted** — Keys and artifacts stay in your environment; bring your own model API

## Workflow

```text
Collect materials / attach docs
        ↓
Ask Agent → Confirm
        ↓
Insights / opportunities / PRD draft
        ↓
Review · clarify · revise
        ↓
Deliverables / handoff / roadmap
```

| Page | Purpose |
|------|---------|
| `/workspace` | Home: chats, Ask Agent, project picker |
| `/materials` | Evidence and PRD sources |
| `/decisions` | Insights and opportunities |
| `/deliveries` | PRD versions and delivery records |
| `/confirmations` | Approve or dismiss agent actions |
| `/settings` | Model connection status |

## Quick start

### Requirements

- Python 3.11+
- Node.js 22+
- A model API key (e.g. OpenAI)

### Install and run

```bash
git clone <your-repo-url>
cd pm-pal

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .

cp .env.example .env   # Windows: copy .env.example .env
# Edit .env: set OPENAI_API_KEY and PM_PAL_LLM (or LLM)

cd frontend && npm install && cd ..
```

Windows helper:

```bash
start-dev.cmd
```

Or start separately:

```bash
python main.py                 # API → http://127.0.0.1:8000
cd frontend && npm run dev     # UI  → http://127.0.0.1:5173
```

Open [http://127.0.0.1:5173/workspace](http://127.0.0.1:5173/workspace):

1. Create a project  
2. Add a PRD under Materials (try `docs/sample_prd.md`)  
3. Use Ask Agent, then approve under Confirmations  

Details: [docs/quick-start.md](./docs/quick-start.md).

### Docker

```bash
docker-compose up --build
# Optional Vite frontend: docker-compose --profile dev up dev
```

## Usage

### Web (local / trial)

Workspace: `http://127.0.0.1:5173/workspace`  
Health: `http://127.0.0.1:8000/health`

### Feishu (optional team entry)

View results and answer clarifications inside Feishu. Webhook and signature setup: [docs/callback-config.md](./docs/callback-config.md).

### CLI

```bash
pm-pal review --input docs/sample_prd.md
pm-pal doctor
```

### MCP

```bash
python -m pm_pal.mcp_server.server
```

See [docs/mcp.md](./docs/mcp.md).

## Configuration

Copy `.env.example` to `.env`. Common variables:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / other provider keys | Model credentials |
| `PM_PAL_LLM` (or `LLM`) | Single model selection, e.g. `openai:gpt-5-nano`; legacy `SMART_LLM` etc. still work as fallbacks |
| `PM_PAL_SECRETS_MASTER_KEY` | Required to save provider keys in Settings |
| `PM_PAL_DATA_DIR` | Data root (default `data/`, SQLite + outputs) |
| `PM_PAL_API_AUTH_DISABLED` | Auth off by default locally; enable with `PM_PAL_API_KEY` for shared hosts |
| `MARRDP_FEISHU_*` etc. | Feishu / Notion / GitHub connectors |

Full reference: [.env.example](./.env.example), [docs/callback-config.md](./docs/callback-config.md).

## Documentation

| Doc | Topic |
|-----|-------|
| [docs/quick-start.md](./docs/quick-start.md) | First successful run |
| [docs/project-space.md](./docs/project-space.md) | Project space, workspace UI & Agent |
| [docs/v2-api.md](./docs/v2-api.md) | HTTP API |
| [docs/deployment-guide.md](./docs/deployment-guide.md) | Deployment |
| [docs/callback-config.md](./docs/callback-config.md) | Feishu / Notion / GitHub callbacks |
| [docs/mcp.md](./docs/mcp.md) | MCP integration |

## Development

```bash
pytest -q
cd frontend && npm test -- --run && npm run build
```

Issues and PRs are welcome. Keep changes focused and include repro steps or screenshots when helpful.

## Security & deployment notes

- Best suited for local or private networks; enable API auth and TLS for shared deployments
- Agent defaults to draft-then-confirm; it does not overwrite source documents unprompted
- Single-instance: in-process SSE does not span replicas; persist `PM_PAL_DATA_DIR`
- Docker Compose mounts `./data:/app/data` and sets `PM_PAL_DATA_DIR=/app/data`

## License

Apache License 2.0 — see [LICENSE](./LICENSE).
