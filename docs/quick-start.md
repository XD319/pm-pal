# Quick Start

这份文档只关注一件事：让你第一次就把 `pm-pal` 的项目空间（workspace-v5）流程跑起来。

## 目标

完成后，你应该可以：

- 启动前后端
- 创建项目并在「资料」中添加 sample PRD
- 通过 Agent / 待确认推进工作
- 在工作台与成果页看到结果（或用 CLI 验证评审内核）

## 1. 前置要求

- Python `3.11+`
- Node.js `22+`
- 一个可用的模型 API Key

## 2. 下载与安装

```bash
git clone <your-repo-url>
cd pm-pal
python -m venv .venv
.venv\Scripts\activate
pip install -e .
cd frontend
npm install
cd ..
```

## 3. 配置 `.env`

```bash
copy .env.example .env
```

最小必填：

```dotenv
OPENAI_API_KEY=your-key
PM_PAL_LLM=openai:gpt-5-nano
```

如需在 Web 设置里保存 provider API key，再生成并设置 `PM_PAL_SECRETS_MASTER_KEY`（见 [project-space.md](./project-space.md)）。未配置时仍可用 `.env` 中的模型 Key 本地运行。

## 4. 启动

Windows 推荐：

```bash
start-dev.cmd
```

手动启动：

```bash
python main.py
cd frontend
npm run dev
```

默认地址：

- 前端: `http://127.0.0.1:5173`（进入后会到 `/workspace`）
- 后端: `http://127.0.0.1:8000`
- 健康检查: `http://127.0.0.1:8000/health`
- 就绪检查: `http://127.0.0.1:8000/ready`

## 5. 验证第一次提交

1. 打开 `http://127.0.0.1:5173/workspace`
2. 新建项目
3. 进入 **资料**，用粘贴正文或上传文件添加 sample PRD（内容可用 `docs/sample_prd.md`）
4. 用 **询问 Agent** 提出一项任务，或在项目域 API 上发起评审
5. 在 **待确认** 中确认任务；在 **决策 / 成果** 查看产出

也可直接用 CLI 验证评审内核：

```bash
pm-pal review --input docs/sample_prd.md
```

HTTP 评审入口：`POST /api/projects/{project_id}/reviews`（见 [v2-api.md](./v2-api.md)）。

## 6. 常见问题

### 前端打开了，但提交失败

优先检查：

- `OPENAI_API_KEY` 是否已填写
- 后端 `http://127.0.0.1:8000/health` 是否返回 `ok: true`
- 后端日志里是否出现模型鉴权错误

### 前端起不来

优先检查：

- 是否已运行 `npm install`
- Node.js 是否为 `22+`
- `5173` 端口是否被占用

### 后端起不来

优先检查：

- Python 是否为 `3.11+`
- 是否已激活虚拟环境
- 是否已执行 `pip install -e .`

## 7. 下一步

本地链路跑通后，继续看：

- [project-space.md](./project-space.md) — 项目空间、工作台与 Agent
- [v2-api.md](./v2-api.md) — HTTP API
- [callback-config.md](./callback-config.md) — 飞书 / Notion / GitHub webhook 配置
