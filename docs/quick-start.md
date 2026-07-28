# Quick Start

这份文档只关注一件事：让你第一次就把 `prd-pal` 的项目空间流程跑起来。

## 目标

完成后，你应该可以：

- 启动前后端
- 创建项目并添加 sample PRD 来源
- 从项目页发起评审
- 打开结果页并下载报告

## 1. 前置要求

- Python `3.11+`
- Node.js `22+`
- 一个可用的模型 API Key

## 2. 下载与安装

```bash
git clone <your-repo-url>
cd prd-pal
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
SMART_LLM=openai:gpt-5-nano
FAST_LLM=openai:gpt-5-nano
STRATEGIC_LLM=openai:gpt-5-nano
```

如需在 Web 设置页保存 provider API key，还需生成并设置 `MARRDP_SECRETS_MASTER_KEY`（见 [project-space.md](./project-space.md)）。

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

- 前端: `http://127.0.0.1:5173`
- 后端: `http://127.0.0.1:8000`
- 健康检查: `http://127.0.0.1:8000/health`
- 就绪检查: `http://127.0.0.1:8000/ready`

## 5. 验证第一次提交

1. 打开首页 `http://127.0.0.1:5173`
2. 创建项目（或使用默认引导流程）
3. 添加 PRD 来源（粘贴 sample 文本或上传文件）
4. 从项目页点击发起评审
5. 等待跳转到结果页
6. 确认页面里能看到运行进度、findings/risks/questions 和报告下载区

也可直接用 CLI 验证评审内核：

```bash
prd-pal review --input docs/sample_prd.md
```

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

- [project-space.md](./project-space.md) — 项目空间与模型配置
- [v2-api.md](./v2-api.md) — 项目域评审 API
- [feishu-setup.md](./feishu-setup.md) — 飞书主入口
- [callback-config.md](./callback-config.md) — 连接器 webhook 配置
