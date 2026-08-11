from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


@dataclass
class DoctorCheck:
    name: str
    status: str
    summary: str
    detail: str = ""


def _has_value(name: str) -> bool:
    return bool(str(os.getenv(name, "") or "").strip())


def _env_value(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _env_value_dual(*names: str, default: str = "") -> str:
    for name in names:
        value = _env_value(name)
        if value:
            return value
    return default


def _env_flag(name: str, default: bool) -> bool:
    raw = _env_value(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _model_provider(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    if ":" not in normalized:
        return ""
    return normalized.split(":", 1)[0].strip().lower()


def _runtime_model_env_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    provider_to_key = {
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    raw_model = _env_value_dual(
        "PM_PAL_LLM",
        "LLM",
        "PM_PAL_SMART_LLM",
        "SMART_LLM",
        "PM_PAL_FAST_LLM",
        "FAST_LLM",
        "PM_PAL_STRATEGIC_LLM",
        "STRATEGIC_LLM",
    )
    provider = _model_provider(raw_model)
    if not provider:
        checks.append(
            DoctorCheck(
                name="llm",
                status="warn",
                summary="PM_PAL_LLM is not set",
                detail="Expected format is <provider>:<model>, for example openai:gpt-5-nano. Legacy SMART_LLM / FAST_LLM / STRATEGIC_LLM still work.",
            )
        )
    else:
        api_key_name = provider_to_key.get(provider)
        if not api_key_name:
            checks.append(
                DoctorCheck(
                    name="llm",
                    status="warn",
                    summary=f"LLM uses provider '{provider}'",
                    detail="Doctor can only validate credential wiring for openai and deepseek providers.",
                )
            )
        elif _has_value(api_key_name):
            checks.append(
                DoctorCheck(
                    name="llm",
                    status="pass",
                    summary=f"LLM is configured as {raw_model}",
                    detail=f"Credential {api_key_name} is present.",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name="llm",
                    status="fail",
                    summary=f"LLM is configured as {raw_model} but {api_key_name} is missing",
                    detail="Add the matching API key to .env before submitting reviews.",
                )
            )
    return checks


def _check_outputs_root(outputs_root: str) -> DoctorCheck:
    path = Path(outputs_root or "outputs")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_path = path / ".doctor-probe"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
    except OSError as exc:
        return DoctorCheck(
            name="outputs_root",
            status="fail",
            summary=f"Outputs directory is not writable: {path}",
            detail=str(exc),
        )
    return DoctorCheck(
        name="outputs_root",
        status="pass",
        summary=f"Outputs directory is writable: {path}",
    )


def _check_env_file() -> DoctorCheck:
    env_path = Path(".env")
    if env_path.exists():
        return DoctorCheck(
            name="env_file",
            status="pass",
            summary=".env file exists",
            detail=str(env_path.resolve()),
        )
    return DoctorCheck(
        name="env_file",
        status="warn",
        summary=".env file is missing",
        detail="Copy .env.example to .env before local setup.",
    )


def _check_python_version() -> DoctorCheck:
    current = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    if sys.version_info >= (3, 11):
        return DoctorCheck(
            name="python",
            status="pass",
            summary=f"Python version is supported: {current}",
        )
    return DoctorCheck(
        name="python",
        status="fail",
        summary=f"Python 3.11+ is required, current version is {current}",
    )


def _check_frontend_sources() -> DoctorCheck:
    frontend_package = Path("frontend") / "package.json"
    if frontend_package.exists():
        return DoctorCheck(
            name="frontend_sources",
            status="pass",
            summary="Frontend sources are present",
            detail=str(frontend_package.resolve()),
        )
    return DoctorCheck(
        name="frontend_sources",
        status="warn",
        summary="Frontend package.json is missing",
        detail="Frontend dev startup may not work in this checkout.",
    )


def _fetch_json(url: str) -> tuple[int, Any]:
    with urlopen(url, timeout=2.0) as response:
        status = int(getattr(response, "status", 200) or 200)
        charset = response.headers.get_content_charset() or "utf-8"
        content = response.read().decode(charset)
        return status, json.loads(content)


def _http_check(
    name: str, url: str, *, success_field: str | None = "ok"
) -> DoctorCheck:
    try:
        status, payload = _fetch_json(url)
    except HTTPError as exc:
        return DoctorCheck(
            name=name,
            status="warn",
            summary=f"{url} returned HTTP {exc.code}",
            detail="The service responded, but not with a healthy status.",
        )
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return DoctorCheck(
            name=name,
            status="warn",
            summary=f"{url} is not reachable",
            detail=str(exc),
        )

    if status >= 400:
        return DoctorCheck(
            name=name,
            status="warn",
            summary=f"{url} returned HTTP {status}",
            detail=json.dumps(payload, ensure_ascii=False),
        )

    if success_field is None:
        return DoctorCheck(
            name=name,
            status="pass",
            summary=f"{url} is reachable",
        )

    if isinstance(payload, dict) and payload.get(success_field) is True:
        return DoctorCheck(
            name=name,
            status="pass",
            summary=f"{url} is healthy",
        )

    return DoctorCheck(
        name=name,
        status="warn",
        summary=f"{url} responded but did not report {success_field}=true",
        detail=json.dumps(payload, ensure_ascii=False),
    )


def _check_frontend_runtime(frontend_url: str) -> DoctorCheck:
    normalized = str(frontend_url or "").rstrip("/")
    try:
        with urlopen(normalized, timeout=2.0) as response:
            status = int(getattr(response, "status", 200) or 200)
    except (HTTPError, URLError, OSError) as exc:
        return DoctorCheck(
            name="frontend_runtime",
            status="warn",
            summary=f"{normalized} is not reachable",
            detail=str(exc),
        )

    if 200 <= status < 400:
        return DoctorCheck(
            name="frontend_runtime",
            status="pass",
            summary=f"{normalized} is reachable",
        )
    return DoctorCheck(
        name="frontend_runtime",
        status="warn",
        summary=f"{normalized} returned HTTP {status}",
    )


def _feishu_checks() -> list[DoctorCheck]:
    app_id = _has_value("MARRDP_FEISHU_APP_ID")
    app_secret = _has_value("MARRDP_FEISHU_APP_SECRET")
    signature_disabled = _env_flag("MARRDP_FEISHU_SIGNATURE_DISABLED", True)
    webhook_secret = _has_value("MARRDP_FEISHU_WEBHOOK_SECRET")

    if not app_id and not app_secret and not webhook_secret:
        return [
            DoctorCheck(
                name="feishu",
                status="warn",
                summary="Feishu integration is not configured",
                detail="This is optional for local-only usage.",
            )
        ]

    checks: list[DoctorCheck] = []
    if app_id and app_secret:
        checks.append(
            DoctorCheck(
                name="feishu_credentials",
                status="pass",
                summary="Feishu app credentials are present",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="feishu_credentials",
                status="fail",
                summary="Feishu app credentials are incomplete",
                detail="Both MARRDP_FEISHU_APP_ID and MARRDP_FEISHU_APP_SECRET are required.",
            )
        )

    if signature_disabled:
        checks.append(
            DoctorCheck(
                name="feishu_signature",
                status="warn",
                summary="Feishu signature verification is disabled",
                detail="Acceptable for local mock testing, not recommended for production.",
            )
        )
    elif webhook_secret:
        checks.append(
            DoctorCheck(
                name="feishu_signature",
                status="pass",
                summary="Feishu signature verification is enabled and webhook secret is present",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="feishu_signature",
                status="fail",
                summary="Feishu signature verification is enabled but webhook secret is missing",
                detail="Set MARRDP_FEISHU_WEBHOOK_SECRET before enabling production callbacks.",
            )
        )
    return checks


def run_doctor(
    *,
    outputs_root: str = "outputs",
    backend_url: str = "http://127.0.0.1:8000",
    frontend_url: str = "http://127.0.0.1:5173",
    check_runtime: bool = True,
) -> dict[str, Any]:
    checks: list[DoctorCheck] = [
        _check_python_version(),
        _check_env_file(),
        _check_outputs_root(outputs_root),
        _check_frontend_sources(),
    ]
    checks.extend(_runtime_model_env_checks())
    checks.extend(_feishu_checks())

    if check_runtime:
        normalized_backend = str(backend_url or "").rstrip("/")
        checks.append(_http_check("backend_health", f"{normalized_backend}/health"))
        checks.append(_http_check("backend_ready", f"{normalized_backend}/ready"))
        checks.append(_check_frontend_runtime(frontend_url))

    failing = [item for item in checks if item.status == "fail"]
    warnings = [item for item in checks if item.status == "warn"]
    status = "fail" if failing else "warn" if warnings else "pass"
    return {
        "status": status,
        "summary": {
            "pass": sum(1 for item in checks if item.status == "pass"),
            "warn": len(warnings),
            "fail": len(failing),
        },
        "checks": [asdict(item) for item in checks],
    }


def render_doctor_report(payload: dict[str, Any]) -> str:
    lines = [
        f"Doctor status : {payload.get('status', 'unknown')}",
        (
            "Checks        : "
            f"{payload.get('summary', {}).get('pass', 0)} passed, "
            f"{payload.get('summary', {}).get('warn', 0)} warnings, "
            f"{payload.get('summary', {}).get('fail', 0)} failures"
        ),
        "",
    ]
    for item in payload.get("checks", []):
        status = str(item.get("status", "")).upper().ljust(4)
        lines.append(f"[{status}] {item.get('name', '')}: {item.get('summary', '')}")
        detail = str(item.get("detail", "") or "").strip()
        if detail:
            lines.append(f"       {detail}")
    return "\n".join(lines)
