"""Conversation-first product manager agent API backed by Command Gateway."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from prd_pal.server.command_gateway import CommandError, CommandGateway, ReviewStarter, policy_for


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ConversationCreateRequest(BaseModel):
    product_id: str = ""
    project_id: str = ""
    title: str = ""


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12_000)
    actor: str = ""


class TaskConfirmRequest(BaseModel):
    confirmed: bool = True
    actor: str = ""


class CommandStore:
    """SQLite command ledger. Agent tasks remain the compatibility-facing view."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_conversations (id TEXT PRIMARY KEY, product_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id));
                CREATE TABLE IF NOT EXISTS agent_tasks (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, requires_confirmation INTEGER NOT NULL, source_url TEXT NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, confirmed_at TEXT, command_id TEXT, FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id));
                CREATE TABLE IF NOT EXISTS agent_commands (command_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE, action TEXT NOT NULL, actor TEXT NOT NULL, conversation_id TEXT NOT NULL, product_id TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, policy_json TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, executed_at TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS idx_agent_commands_conversation ON agent_commands(conversation_id, updated_at DESC);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_tasks)").fetchall()}
            if "command_id" not in columns:
                conn.execute("ALTER TABLE agent_tasks ADD COLUMN command_id TEXT")
            conn.commit()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_command(self, conn: sqlite3.Connection, command_id: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM agent_commands WHERE command_id=?", (command_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        for field in ("policy_json", "payload_json", "result_json"):
            payload[field[:-5]] = json.loads(payload.pop(field) or "{}")
        return payload

    def insert_command(self, conn: sqlite3.Connection, command: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO agent_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (command["command_id"], command["task_id"], command["idempotency_key"], command["action"], command["actor"], command["conversation_id"], command["product_id"], command["project_id"], command["status"], json.dumps(command["policy"], ensure_ascii=False), json.dumps(command["payload"], ensure_ascii=False), "{}", "", "", command["created_at"], command["updated_at"], ""),
        )

    def update_command(self, conn: sqlite3.Connection, command_id: str, *, status: str, result: dict[str, Any] | None = None, error: CommandError | Exception | None = None) -> None:
        now = _now()
        error_code = error.code if isinstance(error, CommandError) else ("execution_failed" if error else "")
        error_message = str(error) if error else ""
        conn.execute(
            "UPDATE agent_commands SET status=?, result_json=?, error_code=?, error_message=?, updated_at=?, executed_at=? WHERE command_id=?",
            (status, json.dumps(result or {}, ensure_ascii=False), error_code, error_message, now, now if status in {"completed", "failed", "denied"} else "", command_id),
        )


def create_agent_router(*, db_path: str | Path, decision_db_path: str | Path, project_db_path: str | Path, start_review: ReviewStarter | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"])
    store = CommandStore(Path(db_path))
    gateway = CommandGateway(decision_db_path=decision_db_path, project_db_path=project_db_path, start_review=start_review)

    def fail(code: str, message: str, status: int = 422) -> HTTPException:
        return HTTPException(status_code=status, detail={"code": code, "message": message})

    def classify(content: str) -> tuple[str, str, str, str]:
        lowered = content.lower()
        urls = re.findall(r"https?://[^\s]+", content)
        feishu_url = next((url for url in urls if "feishu" in url or "larksuite" in url), "")
        if feishu_url:
            return "connect_feishu", "连接飞书文档", "已识别飞书文档；确认后会写入当前上下文。", feishu_url
        if any(word in lowered for word in ("feedback", "insight", "反馈", "机会")):
            return "generate_insight", "汇总反馈并生成机会草案", "将基于已确认的证据生成可编辑草案。", ""
        if any(word in lowered for word in ("review", "评审", "检查")):
            return "start_review", "发起 PRD 评审", "已准备评审命令，确认后会发起评审。", ""
        if any(word in lowered for word in ("handoff", "roadmap", "交付", "导出")):
            return "prepare_delivery", "准备交付包", "将检查可交付 PRD 并返回交付入口。", ""
        return "generate_prd", "生成正式 PRD 草案", "将基于已批准机会创建正式 PRD 草案。", ""

    def task_payload(row: sqlite3.Row, conn: sqlite3.Connection) -> dict[str, Any]:
        details = json.loads(row["details_json"] or "{}")
        command = store.get_command(conn, str(row["command_id"] or "")) if row["command_id"] else None
        if command:
            details["command"] = {key: command[key] for key in ("command_id", "idempotency_key", "action", "actor", "status", "policy", "result", "error_code", "error_message")}
        return {"id": row["id"], "kind": row["kind"], "title": row["title"], "status": row["status"], "requires_confirmation": bool(row["requires_confirmation"]), "source_url": row["source_url"], "details": details, "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def conversation_payload(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
        conversation = conn.execute("SELECT * FROM agent_conversations WHERE id=?", (conversation_id,)).fetchone()
        if conversation is None:
            raise fail("conversation_not_found", "Conversation was not found", 404)
        messages = conn.execute("SELECT * FROM agent_messages WHERE conversation_id=? ORDER BY created_at", (conversation_id,)).fetchall()
        tasks = conn.execute("SELECT * FROM agent_tasks WHERE conversation_id=? ORDER BY created_at DESC", (conversation_id,)).fetchall()
        return {"conversation": dict(conversation), "messages": [{"id": item["id"], "role": item["role"], "content": item["content"], "payload": json.loads(item["payload_json"]), "created_at": item["created_at"]} for item in messages], "tasks": [task_payload(item, conn) for item in tasks]}

    async def run_command(task_id: str, command_id: str) -> dict[str, Any]:
        with store._connection() as conn:
            command = store.get_command(conn, command_id)
            if command is None:
                raise fail("command_not_found", "Command was not found", 404)
            if command["status"] == "completed":
                return dict(command["result"])
            if command["status"] not in {"executable", "failed"}:
                raise fail("invalid_command_state", f"Cannot execute command from {command['status']}", 409)
            store.update_command(conn, command_id, status="running")
            conn.execute("UPDATE agent_tasks SET status=?, updated_at=? WHERE id=?", ("running", _now(), task_id))
            conn.commit()
        try:
            result = await gateway.execute(command)
        except CommandError as exc:
            status = "denied" if exc.code in {"actor_required", "unknown_action", "precondition_failed", "permission_denied"} else "failed"
            with store._connection() as conn:
                store.update_command(conn, command_id, status=status, error=exc)
                conn.execute("UPDATE agent_tasks SET status=?, details_json=?, updated_at=? WHERE id=?", (status, json.dumps({"error": str(exc), "error_code": exc.code, "retryable": exc.retryable}, ensure_ascii=False), _now(), task_id))
                conn.commit()
            return {"error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            with store._connection() as conn:
                store.update_command(conn, command_id, status="failed", error=exc)
                conn.execute("UPDATE agent_tasks SET status=?, details_json=?, updated_at=? WHERE id=?", ("failed", json.dumps({"error": "command execution failed", "error_code": "execution_failed", "retryable": True}, ensure_ascii=False), _now(), task_id))
                conn.commit()
            return {"error": {"code": "execution_failed", "message": "command execution failed"}}
        with store._connection() as conn:
            store.update_command(conn, command_id, status="completed", result=result)
            details = {"result": result, "next_step": result.get("next_action", "已完成")}
            conn.execute("UPDATE agent_tasks SET status=?, details_json=?, updated_at=?, confirmed_at=COALESCE(confirmed_at, ?) WHERE id=?", ("completed", json.dumps(details, ensure_ascii=False), _now(), _now(), task_id))
            conversation = conn.execute("SELECT conversation_id FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if conversation and (result.get("product_id") or result.get("project_id")):
                conn.execute("UPDATE agent_conversations SET product_id=COALESCE(NULLIF(?, ''), product_id), project_id=COALESCE(NULLIF(?, ''), project_id), updated_at=? WHERE id=?", (result.get("product_id", ""), result.get("project_id", ""), _now(), conversation["conversation_id"]))
            conn.commit()
        return result

    @router.post("/conversations")
    async def create_conversation(payload: ConversationCreateRequest) -> dict[str, Any]:
        timestamp = _now(); conversation_id = _id("conv")
        with store._connection() as conn:
            conn.execute("INSERT INTO agent_conversations VALUES (?,?,?,?,?,?)", (conversation_id, payload.product_id, payload.project_id, payload.title or "产品 Agent 对话", timestamp, timestamp)); conn.commit()
            return conversation_payload(conn, conversation_id)

    @router.get("/conversations")
    async def list_conversations(limit: int = 24) -> dict[str, Any]:
        with store._connection() as conn:
            rows = conn.execute("SELECT c.*, (SELECT t.status FROM agent_tasks t WHERE t.conversation_id=c.id ORDER BY t.updated_at DESC LIMIT 1) AS latest_task_status FROM agent_conversations c ORDER BY c.updated_at DESC, (SELECT MAX(t.rowid) FROM agent_tasks t WHERE t.conversation_id=c.id) DESC, c.created_at DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
            return {"conversations": [{"id": row["id"], "title": row["title"] or "未命名任务", "product_id": row["product_id"], "project_id": row["project_id"], "updated_at": row["updated_at"], "latest_task_status": row["latest_task_status"] or "idle"} for row in rows]}

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        with store._connection() as conn:
            return conversation_payload(conn, conversation_id)

    @router.post("/conversations/{conversation_id}/messages")
    async def add_message(conversation_id: str, payload: MessageCreateRequest) -> dict[str, Any]:
        actor = payload.actor.strip()
        action, title, reply, source_url = classify(payload.content)
        policy = policy_for(action)
        if policy.writes and not actor:
            raise fail("actor_required", "This command requires an actor identity")
        timestamp = _now(); task_id = _id("task"); command_id = _id("cmd")
        with store._connection() as conn:
            conversation = conn.execute("SELECT * FROM agent_conversations WHERE id=?", (conversation_id,)).fetchone()
            if conversation is None:
                raise fail("conversation_not_found", "Conversation was not found", 404)
            source_payload: dict[str, Any] = {"source_url": source_url} if source_url else {}
            if action == "start_review":
                source = conn.execute("SELECT id, checksum FROM project_sources WHERE project_id=? AND is_prd=1 ORDER BY created_at DESC LIMIT 1", (conversation["project_id"],)).fetchone()
                if source:
                    source_payload.update({"source_id": source["id"], "source_checksum": source["checksum"]})
            command = {"command_id": command_id, "task_id": task_id, "idempotency_key": command_id, "action": action, "actor": actor, "conversation_id": conversation_id, "product_id": conversation["product_id"], "project_id": conversation["project_id"], "status": "awaiting_confirmation" if policy.requires_confirmation else "executable", "policy": {"requires_confirmation": policy.requires_confirmation, "writes": policy.writes}, "payload": source_payload, "created_at": timestamp, "updated_at": timestamp}
            status = "awaiting_confirmation" if policy.requires_confirmation else "executable"
            conn.execute("INSERT INTO agent_messages VALUES (?,?,?,?,?,?)", (_id("msg"), conversation_id, "user", payload.content, "{}", timestamp))
            conn.execute("INSERT INTO agent_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, conversation_id, action, title, status, 1 if policy.requires_confirmation else 0, source_url, json.dumps({"intent": action, "next_step": "请确认后执行" if policy.requires_confirmation else "正在执行"}, ensure_ascii=False), timestamp, timestamp, None, command_id))
            store.insert_command(conn, command)
            conn.execute("INSERT INTO agent_messages VALUES (?,?,?,?,?,?)", (_id("msg"), conversation_id, "assistant", reply, json.dumps({"task_id": task_id, "command_id": command_id}, ensure_ascii=False), timestamp))
            conn.execute("UPDATE agent_conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id)); conn.commit()
        if not policy.requires_confirmation:
            await run_command(task_id, command_id)
        with store._connection() as conn:
            result = conversation_payload(conn, conversation_id)
            result["task"] = next(item for item in result["tasks"] if item["id"] == task_id)
            return result

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        with store._connection() as conn:
            task = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise fail("task_not_found", "Task was not found", 404)
            return {"task": task_payload(task, conn)}

    @router.post("/tasks/{task_id}/confirm")
    async def confirm_task(task_id: str, payload: TaskConfirmRequest) -> dict[str, Any]:
        actor = payload.actor.strip()
        if not actor:
            raise fail("actor_required", "Confirmation requires an actor identity")
        with store._connection() as conn:
            task = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise fail("task_not_found", "Task was not found", 404)
            command = store.get_command(conn, str(task["command_id"] or ""))
            if command is None:
                return {"task": task_payload(task, conn)}
            if command["actor"] != actor:
                raise fail("actor_mismatch", "Only the command requester may confirm it", 403)
            if not payload.confirmed:
                store.update_command(conn, command["command_id"], status="dismissed")
                conn.execute("UPDATE agent_tasks SET status=?, updated_at=? WHERE id=?", ("dismissed", _now(), task_id)); conn.commit()
                return {"task": task_payload(conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone(), conn)}
            if command["status"] == "awaiting_confirmation":
                store.update_command(conn, command["command_id"], status="executable")
                conn.execute("UPDATE agent_tasks SET status=?, confirmed_at=?, updated_at=? WHERE id=?", ("executable", _now(), _now(), task_id)); conn.commit()
            elif command["status"] not in {"failed", "completed"}:
                return {"task": task_payload(task, conn)}
        if command["status"] != "completed":
            await run_command(task_id, command["command_id"])
        with store._connection() as conn:
            return {"task": task_payload(conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone(), conn)}

    @router.post("/tasks/{task_id}/retry")
    async def retry_task(task_id: str, payload: TaskConfirmRequest) -> dict[str, Any]:
        return await confirm_task(task_id, payload.model_copy(update={"confirmed": True}))

    @router.get("/tasks/{task_id}/progress/stream")
    async def task_progress_stream(task_id: str):
        async def events():
            with store._connection() as conn:
                task = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
                if task is None:
                    yield 'event: error\ndata: {"code":"task_not_found"}\n\n'; return
                yield f"event: progress\ndata: {json.dumps(task_payload(task, conn), ensure_ascii=False)}\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    return router
