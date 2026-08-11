# pm-pal

[中文](./README.md) | [English](./README.en.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=0A0A0A)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)
![Feishu](https://img.shields.io/badge/Feishu-Integrated-3370FF)
![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)

**面向产品经理的日常工作 Agent** —— 把反馈、证据、洞察、PRD、评审与交付串成一条可确认的闭环，帮 PM 少做重复整理，多做关键决策。

自托管、可私有化部署；数据默认落在本机项目空间。支持 Web 工作台、飞书入口、CLI 与 MCP。

---

## 为什么需要 pm-pal

产品经理日常大量时间花在：

- 从飞书/文档/会议纪要里捞证据
- 把零散反馈整理成洞察与机会
- 起草与修订 PRD
- 组织评审、澄清问题、准备研发 handoff

`pm-pal` 用 **Agent + 项目空间** 承接这些流程：你提任务，Agent 生成草案；**关键动作需你确认后才执行**，不会静默覆盖原文。

## 功能特性

- **Agent 工作台** — 用自然语言下达任务（分析反馈、生成机会、准备评审等），在「待确认」中审批执行
- **项目空间** — 按项目沉淀资料、决策与成果，上下文可追溯
- **证据 → 洞察 → 机会 → PRD** — 从收集材料到可交付文档的完整链路
- **PRD / 需求评审** — 多角色评审、风险与开放问题、报告导出
- **连接器** — Feishu / Notion / GitHub / URL / 本地文件，支持 webhook 同步
- **多种入口** — Web、飞书 H5、CLI（`pm-pal`）、MCP（给 IDE / 自动化 Agent 用）
- **自托管** — 密钥与产物留在你的环境，按需接入自己的模型 API

## 工作流一览

```text
收集资料 / 接入文档
        ↓
询问 Agent → 待确认
        ↓
洞察 / 机会 / PRD 草案
        ↓
评审 · 澄清 · 修订
        ↓
交付物 / handoff / roadmap
```

| 页面 | 做什么 |
|------|--------|
| `/workspace` | 工作台：对话、询问 Agent、切换项目 |
| `/materials` | 资料：证据与 PRD 来源 |
| `/decisions` | 决策：洞察与机会 |
| `/deliveries` | 成果：PRD 版本与交付记录 |
| `/confirmations` | 待确认：批准或忽略 Agent 动作 |
| `/settings` | 设置：模型连接状态 |

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 22+
- 可用的模型 API Key（如 OpenAI）

### 安装与启动

```bash
git clone <your-repo-url>
cd pm-pal

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e .

cp .env.example .env   # Windows 可用: copy .env.example .env
# 编辑 .env，至少填入 OPENAI_API_KEY 与 PM_PAL_LLM（或 LLM）

cd frontend && npm install && cd ..
```

Windows 一键启动：

```bash
start-dev.cmd
```

或分别启动：

```bash
python main.py                 # API → http://127.0.0.1:8000
cd frontend && npm run dev     # UI  → http://127.0.0.1:5173
```

打开 [http://127.0.0.1:5173/workspace](http://127.0.0.1:5173/workspace)：

1. 新建项目  
2. 在「资料」中粘贴或上传 PRD（可用 `docs/sample_prd.md`）  
3. 用「询问 Agent」描述任务，并在「待确认」中批准  

更细的步骤见 [docs/quick-start.md](./docs/quick-start.md)。

### Docker

```bash
docker-compose up --build
# 可选开发前端: docker-compose --profile dev up dev
```

## 使用方式

### Web（推荐本地 / 试用）

工作台地址：`http://127.0.0.1:5173/workspace`  
健康检查：`http://127.0.0.1:8000/health`

### 飞书（可选团队入口）

可在飞书内查看结果与回答澄清；事件回调与签名配置见 [docs/callback-config.md](./docs/callback-config.md)。

### CLI

```bash
pm-pal review --input docs/sample_prd.md
pm-pal doctor
```

### MCP

```bash
python -m pm_pal.mcp_server.server
```

工具说明见 [docs/mcp.md](./docs/mcp.md)。

## 配置说明

复制 `.env.example` 为 `.env`。常见项：

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` / 其他厂商 Key | 模型调用凭证 |
| `PM_PAL_LLM`（或 `LLM`） | 单模型选型，如 `openai:gpt-5-nano`；旧 `SMART_LLM` 等仍可作为回退 |
| `PM_PAL_SECRETS_MASTER_KEY` | 在设置页保存 provider Key 时需要 |
| `PM_PAL_DATA_DIR` | 数据目录（默认 `data/`，含 SQLite 与 outputs） |
| `PM_PAL_API_AUTH_DISABLED` | 本地默认关闭鉴权；共享部署请开启并配置 `PM_PAL_API_KEY` |
| `MARRDP_FEISHU_*` 等 | 飞书 / Notion / GitHub 连接器（见 callback 文档） |

完整说明：[.env.example](./.env.example)、[docs/callback-config.md](./docs/callback-config.md)。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/quick-start.md](./docs/quick-start.md) | 第一次跑通 |
| [docs/project-space.md](./docs/project-space.md) | 项目空间、工作台与 Agent |
| [docs/v2-api.md](./docs/v2-api.md) | HTTP API |
| [docs/deployment-guide.md](./docs/deployment-guide.md) | 部署与边界 |
| [docs/callback-config.md](./docs/callback-config.md) | 飞书 / Notion / GitHub 回调 |
| [docs/mcp.md](./docs/mcp.md) | MCP 集成 |

## 开发

```bash
pytest -q
cd frontend && npm test -- --run && npm run build
```

欢迎 Issue 与 PR。改动请保持聚焦，并尽量附上复现步骤或截图。

## 安全与部署提示

- 默认适合本机 / 私有网络；共享环境请开启 API 鉴权与 TLS
- Agent 默认「先草案、再确认」，不自动覆盖原始文档
- 单实例部署：进程内 SSE 不跨副本；数据以 `PM_PAL_DATA_DIR` 为准
- Docker Compose 挂载 `./data:/app/data` 并设置 `PM_PAL_DATA_DIR=/app/data`

## License

Apache License 2.0 — 见 [LICENSE](./LICENSE)。
