# 飞书产品决策工作台：单团队试点运行手册

## 范围

- 单产品、单租户、预配置来源表
- 产品负责人制：owner 批准机会、放行/豁免 PRD、导出交付包；管理员可兜底
- 飞书多维表格为默认交付目标；飞书项目按产品配置启用

## 配置清单

1. 复制 `config/pilot.example.yaml` 为部署配置，填写：
   - `product.id` / `owner_open_id` / `admin_open_ids`
   - 飞书文档与多维表格来源 URL、`external_id`、字段映射
   - 交付 bitable `app_token` / `table_id` / 字段映射
2. 环境变量：
   - `MARRDP_FEISHU_APP_ID`
   - `MARRDP_FEISHU_APP_SECRET`
   - `MARRDP_FEISHU_OPEN_BASE_URL`（可选）
3. 通过 `POST /api/decision/owners` 写入产品 owner
4. 通过 `POST /api/decision/sources` 登记来源

## 飞书权限

- 文档只读：同步会议纪要/PRD 原文
- 多维表格读写：证据拉取与交付导出
- 机器人：提醒、提交、查询、H5 深链接（不做复杂审批）

## 日常节奏

- 02:00 `Asia/Shanghai` 自动增量同步
- H5 手动刷新与日任务共用幂等键
- 机器人指令：`submit` / `summary` / `pending` / `query` / `link`

## 验收闭环

来源同步 → 证据审阅 → owner 批准机会 → PRD 质量评估/豁免 → 多维表格导出 → 飞书链接回跳

## 首期明确不做

- 实时消息
- Jira/Linear 双向同步
- 分析闭环
- 多团队权限
- 托管 SaaS
