from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeishuChallengeEvent(BaseModel):
    challenge: str
    type: str | None = None


class FeishuEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    challenge: str | None = None
    header: dict[str, Any] | None = None
    event: dict[str, Any] | None = None

    def is_challenge(self) -> bool:
        return (
            bool(self.challenge) or str(self.type or "").strip() == "url_verification"
        )


class FeishuSubmitRequest(BaseModel):
    source: str | None = None
    prd_text: str | None = None
    mode: Literal["auto", "quick", "full"] | None = None
    llm: str | None = None
    # Legacy aliases still accepted; all collapse to llm :-)
    fast_llm: str | None = None
    smart_llm: str | None = None
    strategic_llm: str | None = None
    temperature: float | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    llm_kwargs: dict[str, Any] | None = None
    open_id: str | None = None
    user_id: str | None = None
    tenant_key: str | None = None
    trigger_source: str | None = Field(default="feishu")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> FeishuSubmitRequest:
        has_source = bool(self.source and self.source.strip())
        if has_source:
            return self

        has_text = bool(self.prd_text and self.prd_text.strip())
        if not has_text:
            raise ValueError("Provide source, or prd_text.")
        return self

    def to_review_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prd_text": None,
            "prd_path": None,
            "source": self.source.strip() if self.source else None,
            "mode": self.mode,
            "llm_options": {},
        }
        if not payload["source"]:
            payload["prd_text"] = self.prd_text

        for key in ("llm", "smart_llm", "fast_llm", "strategic_llm"):
            value = getattr(self, key)
            if value is not None:
                payload["llm_options"]["llm"] = value
                break
        for key in ("temperature", "reasoning_effort", "llm_kwargs"):
            value = getattr(self, key)
            if value is not None:
                payload["llm_options"][key] = value
        return payload

    def build_audit_context(self) -> dict[str, Any]:
        return _build_feishu_audit_context(
            open_id=self.open_id,
            user_id=self.user_id,
            tenant_key=self.tenant_key,
            trigger_source=self.trigger_source,
            metadata=self.metadata,
            tool_name="feishu.submit",
        )


class FeishuClarificationRequest(BaseModel):
    run_id: str
    question_id: str
    answer: str
    open_id: str | None = None
    user_id: str | None = None
    tenant_key: str | None = None
    trigger_source: str | None = Field(default="feishu")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> FeishuClarificationRequest:
        if not str(self.run_id or "").strip():
            raise ValueError("run_id is required.")
        if not str(self.question_id or "").strip():
            raise ValueError("question_id is required.")
        if not str(self.answer or "").strip():
            raise ValueError("answer is required.")
        return self

    def to_answers_payload(self) -> list[dict[str, str]]:
        return [
            {
                "question_id": str(self.question_id).strip(),
                "answer": str(self.answer).strip(),
            }
        ]

    def build_audit_context(self) -> dict[str, Any]:
        return _build_feishu_audit_context(
            open_id=self.open_id,
            user_id=self.user_id,
            tenant_key=self.tenant_key,
            trigger_source=self.trigger_source,
            metadata=self.metadata,
            tool_name="feishu.clarification",
        )


class FeishuWorkspaceClarificationRequest(FeishuClarificationRequest):
    workspace_id: str | None = None


class FeishuWorkspaceDeriveRequest(BaseModel):
    open_id: str | None = None
    user_id: str | None = None
    tenant_key: str | None = None
    trigger_source: str | None = Field(default="feishu")
    metadata: dict[str, Any] | None = None

    def build_audit_context(self) -> dict[str, Any]:
        return _build_feishu_audit_context(
            open_id=self.open_id,
            user_id=self.user_id,
            tenant_key=self.tenant_key,
            trigger_source=self.trigger_source,
            metadata=self.metadata,
            tool_name="feishu.workspace.derive",
        )


class FeishuWorkspaceRoadmapUpdateRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] | None = None
    risk_items: list[dict[str, Any]] | None = None
    acceptance_criteria_coverage: dict[str, Any] | list[dict[str, Any]] | None = None
    business_priority_hints: dict[str, Any] | None = None
    milestones: list[dict[str, Any]] | None = None
    open_id: str | None = None
    user_id: str | None = None
    tenant_key: str | None = None
    trigger_source: str | None = Field(default="feishu")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> FeishuWorkspaceRoadmapUpdateRequest:
        if not self.tasks:
            raise ValueError("tasks is required.")
        return self

    def build_audit_context(self) -> dict[str, Any]:
        return _build_feishu_audit_context(
            open_id=self.open_id,
            user_id=self.user_id,
            tenant_key=self.tenant_key,
            trigger_source=self.trigger_source,
            metadata=self.metadata,
            tool_name="feishu.workspace.roadmap.update",
        )


def _build_feishu_audit_context(
    *,
    open_id: str | None,
    user_id: str | None,
    tenant_key: str | None,
    trigger_source: str | None,
    metadata: dict[str, Any] | None,
    tool_name: str,
) -> dict[str, Any]:
    client_metadata = {
        "trigger_source": str(trigger_source or "feishu").strip() or "feishu",
    }
    for key in ("open_id", "user_id", "tenant_key"):
        value = {"open_id": open_id, "user_id": user_id, "tenant_key": tenant_key}[key]
        if isinstance(value, str) and value.strip():
            client_metadata[key] = value.strip()
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            client_metadata.setdefault(str(key), value)

    return {
        "source": "feishu",
        "tool_name": tool_name,
        "actor": str(open_id or user_id or "feishu").strip() or "feishu",
        "client_metadata": client_metadata,
    }
