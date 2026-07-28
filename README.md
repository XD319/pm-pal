# prd-pal

[中文](./README.md) | [English](./README.en.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=nodedotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=0A0A0A)
![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?logo=docker&logoColor=white)
![Feishu](https://img.shields.io/badge/Feishu-Integrated-3370FF)
![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)

`prd-pal` 是一个面向 PRD/需求文档的评审服务。默认以 **项目空间（Project Space）** 组织工作：创建项目、添加来源、发起评审、查看历史运行记录。飞书仍是面向普通用户的主入口；Web 与 CLI 保留为试用、联调和开发入口。

## 项目空间能做什么

1. 创建项目并配置模型连接
2. 添加 PRD 来源（正文、文件、Feishu/Notion/GitHub/URL 链接）
3. 从项目来源发起评审（`POST /api/projects/{project_id}/reviews`）
4. 在项目内查看运行状态、结果、报告与后续澄清/修订/交付动作
5. 通过连接器 webhook 同步外部文档变更

## 飞书主流程（公开叙事）

面向普通用户的默认流程：

发起评审 → 查看结果 → 回答澄清 → 选择是否修订 PRD → （可选）上传会议纪要和额外要求 → 生成并确认修订版 → 生成 handoff / roadmap

关键约定：

- 修订版是草稿/派生版本，不会自动覆盖原文
- handoff / roadmap 默认基于“已确认的修订版”生成
- 若你选择不修订，也可以直接继续后续交付（兼容旧流程）

## 30 秒上手

1. 按 [docs/quick-start.md](./docs/quick-start.md) 启动本地服务
2. 打开 Web 首页，创建项目并添加 sample PRD 来源
3. 从项目页发起评审并打开结果页
4. （可选）按 [docs/feishu-setup.md](./docs/feishu-setup.md) 接入飞书主入口

## 环境要求

- Python `3.11+`
- Node.js `22+`
- 一个可用的模型 API Key
- Windows 本地开发可直接使用仓库内脚本；macOS/Linux 可用 `python` + `npm` 或 Docker

## 连接器概览

| 连接器 | 用途 | 实时同步回调 |
|--------|------|--------------|
| Feishu | 飞书文档读取、事件与提审 | `POST /api/feishu/events`, `/api/feishu/submit` |
| Notion | Notion 页面读取 | `POST /api/notion/events` |
| GitHub | README/Issues/PR 等仓库内容 | `POST /api/github/events` |
| URL | 抓取公开网页 | — |
| Local file | 本地文件路径 | — |

Webhook 与签名校验配置见 [docs/callback-config.md](./docs/callback-config.md)。

## 一、推荐阅读顺序

- 项目空间与评审 API：
  - [docs/project-space.md](./docs/project-space.md)
  - [docs/v2-api.md](./docs/v2-api.md)
- 快速启动：
  - [docs/quick-start.md](./docs/quick-start.md)
- 飞书接入（主入口）：
  - [docs/feishu-setup.md](./docs/feishu-setup.md)
  - [docs/feishu-user-guide.md](./docs/feishu-user-guide.md)
- 部署与回调：
  - [docs/deployment-guide.md](./docs/deployment-guide.md)
  - [docs/callback-config.md](./docs/callback-config.md)

## 二、本地快速跑通

### 1. 下载仓库

```bash
git clone <your-repo-url>
cd prd-pal
```

### 2. 配置环境变量

```bash
copy .env.example .env
```

本地最小可用配置：

```dotenv
OPENAI_API_KEY=your-key
SMART_LLM=openai:gpt-5-nano
FAST_LLM=openai:gpt-5-nano
STRATEGIC_LLM=openai:gpt-5-nano
```

### 3. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
cd frontend && npm install && cd ..
```

### 4. 启动服务

Windows 推荐：

```bash
start-dev.cmd
```

默认地址：

- 前端: `http://127.0.0.1:5173`
- 后端: `http://127.0.0.1:8000`
- 健康检查: `http://127.0.0.1:8000/health`
- 就绪检查: `http://127.0.0.1:8000/ready`

### 5. 验证本地链路

1. 打开首页，创建项目
2. 添加 sample PRD 来源并发起评审
3. 确认结果页显示进度、总结和报告下载

CLI 替代路径：

```bash
prd-pal review --input docs/sample_prd.md
```

## 三、Docker 跑通

```bash
docker-compose up --build
```

开发模式前端：

```bash
docker-compose --profile dev up dev
```

## 四、常用入口

### Web（项目空间）

- 首页: `http://127.0.0.1:5173/`
- 项目评审 API 前缀: `/api/projects/{project_id}/reviews`

### Feishu（主入口）

- 飞书工作入口: `https://<your-domain>/feishu`
- H5 结果页: `/run/<run_id>?embed=feishu&open_id=<open_id>&tenant_key=<tenant_key>`

### CLI / MCP

```bash
prd-pal review --input docs/sample_prd.md
python -m prd_pal.mcp_server.server
```

### 主要 HTTP API（项目域）

- `POST /api/projects/{project_id}/reviews` — 发起评审
- `GET /api/projects/{project_id}/reviews/{run_id}` — 轮询状态
- `GET /api/projects/{project_id}/reviews/{run_id}/result` — 结构化结果
- `GET /api/projects/{project_id}/reviews/{run_id}/report?format=md|json|html|csv` — 下载报告

全局 `/api/review` 路由已在 Phase 2 移除；详见 [docs/v2-api.md](./docs/v2-api.md)。

## 五、输出物

每次运行默认写到 `outputs/<run_id>/`：

- `report.md`, `report.json`, `run_trace.json`
- 并行评审路径下可能还有 `review_report.json`, `risk_items.json`, `open_questions.json`, `review_summary.md`

## 六、验证

```bash
pytest -q
cd frontend && npm test -- --run && npm run build
```
