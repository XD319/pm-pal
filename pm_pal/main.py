"""CLI entry point for the requirement-review workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from dotenv import load_dotenv

from .doctor import render_doctor_report, run_doctor
from .service.report_service import get_report_for_mcp
from .service.review_service import (
    prepare_agent_handoff_for_mcp_async,
    review_prd_text_async,
)
from .utils.logging import setup_logging


def _add_review_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input", dest="prd_path", type=str, help="Path to the requirement document"
    )
    parser.add_argument(
        "--text", dest="prd_text", type=str, help="Inline requirement text"
    )
    parser.add_argument("--source", type=str, help="Connector-backed source reference")
    parser.add_argument("--run-id", type=str, help="Optional run_id override")
    parser.add_argument(
        "--outputs-root", type=str, default="outputs", help="Outputs directory"
    )
    parser.add_argument(
        "--mode", choices=["auto", "quick", "full"], help="Review mode preference"
    )
    parser.add_argument(
        "--review-mode-override",
        type=str,
        help="Explicit workflow review mode override",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")


def _build_modern_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Requirement Review CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser("review", help="Run one review")
    _add_review_input_arguments(review_parser)

    report_parser = subparsers.add_parser("report", help="Fetch a generated report")
    report_parser.add_argument("--run-id", required=True, help="Review run_id")
    report_parser.add_argument(
        "--format", choices=["md", "json"], default="md", help="Report format"
    )
    report_parser.add_argument(
        "--outputs-root", type=str, default="outputs", help="Outputs directory"
    )
    report_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    handoff_parser = subparsers.add_parser(
        "prepare-handoff", help="Prepare agent-specific request payloads"
    )
    _add_review_input_arguments(handoff_parser)
    handoff_parser.add_argument(
        "--agent",
        choices=["all", "codex", "claude_code", "openclaw"],
        default="all",
        help="Target agent selection",
    )
    handoff_parser.add_argument(
        "--execution-mode",
        choices=["agent_auto", "agent_assisted", "human_only"],
        default="agent_assisted",
        help="Execution mode for prepared handoff tasks",
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Validate local setup and optional runtime health"
    )
    doctor_parser.add_argument(
        "--outputs-root", type=str, default="outputs", help="Outputs directory"
    )
    doctor_parser.add_argument(
        "--backend-url",
        type=str,
        default="http://127.0.0.1:8000",
        help="Backend base URL",
    )
    doctor_parser.add_argument(
        "--frontend-url",
        type=str,
        default="http://127.0.0.1:5173",
        help="Frontend base URL",
    )
    doctor_parser.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Skip HTTP checks for backend and frontend runtime endpoints",
    )
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    return parser


def _parse_legacy_review_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Requirement Review CLI")
    _add_review_input_arguments(parser)
    args = parser.parse_args(argv)
    args.command = "review"
    return args


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if not effective_argv or effective_argv[0].startswith("-"):
        return _parse_legacy_review_args(effective_argv)
    return _build_modern_parser().parse_args(effective_argv)


def _resolve_review_inputs(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None]:
    prd_text = str(getattr(args, "prd_text", "") or "").strip() or None
    prd_path = str(getattr(args, "prd_path", "") or "").strip() or None
    source = str(getattr(args, "source", "") or "").strip() or None

    selected = [value for value in (prd_text, prd_path, source) if value]
    if not selected and not str(getattr(args, "run_id", "") or "").strip():
        raise ValueError("Provide one of --input, --text, --source, or --run-id")
    if len([value for value in (prd_text, prd_path, source) if value]) > 1:
        raise ValueError("Provide only one of --input, --text, or --source")
    return prd_text, prd_path, source


def _review_options_from_args(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {
        "outputs_root": str(getattr(args, "outputs_root", "outputs") or "outputs"),
        "audit_context": {
            "source": "cli",
            "tool_name": f"cli.{getattr(args, 'command', 'review') or 'review'!s}",
            "actor": "cli",
            "client_metadata": {},
        },
    }
    mode = str(getattr(args, "mode", "") or "").strip()
    review_mode_override = str(getattr(args, "review_mode_override", "") or "").strip()
    run_id = str(getattr(args, "run_id", "") or "").strip()
    execution_mode = str(getattr(args, "execution_mode", "") or "").strip()
    if mode:
        options["mode"] = mode
    if review_mode_override:
        options["review_mode_override"] = review_mode_override
    if run_id:
        options["run_id"] = run_id
    if execution_mode:
        options["execution_mode"] = execution_mode
    return options


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run_review_command(args: argparse.Namespace) -> int:
    prd_text, prd_path, source = _resolve_review_inputs(args)
    summary = await review_prd_text_async(
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        run_id=str(getattr(args, "run_id", "") or "").strip() or None,
        config_overrides=_review_options_from_args(args),
    )
    payload = {
        "run_id": summary.run_id,
        "status": summary.status,
        "metrics": {
            "coverage_ratio": summary.coverage_ratio,
            "high_risk_ratio": summary.high_risk_ratio,
            "revision_round": summary.revision_round,
        },
        "artifacts": {
            "report_md_path": summary.report_md_path,
            "report_json_path": summary.report_json_path,
            "trace_path": summary.run_trace_path,
            "implementation_pack_path": summary.implementation_pack_path,
            "test_pack_path": summary.test_pack_path,
            "execution_pack_path": summary.execution_pack_path,
            "prd_v1_path": summary.prd_v1_path,
            "task_bundle_v1_path": summary.task_bundle_v1_path,
            "delivery_bundle_path": summary.delivery_bundle_path,
        },
    }
    if args.json:
        _emit_json(payload)
    else:
        print(f"Run ID : {summary.run_id}")
        print(f"Status : {summary.status}")
        print(f"Report : {summary.report_md_path}")
        print(f"State  : {summary.report_json_path}")
        print(f"Trace  : {summary.run_trace_path}")
        print(f"PRD V1 : {summary.prd_v1_path}")
        print(f"Tasks  : {summary.task_bundle_v1_path}")
        print(f"Bundle : {summary.delivery_bundle_path}")
    return 0


def _run_report_command(args: argparse.Namespace) -> int:
    payload = get_report_for_mcp(
        run_id=str(args.run_id),
        format=str(args.format),
        outputs_root=str(args.outputs_root),
    )
    if args.json or args.format == "json":
        _emit_json(payload)
    else:
        print(str(payload.get("content", "")))
    return 1 if "error" in payload else 0


def _run_doctor_command(args: argparse.Namespace) -> int:
    payload = run_doctor(
        outputs_root=str(getattr(args, "outputs_root", "outputs") or "outputs"),
        backend_url=str(
            getattr(args, "backend_url", "http://127.0.0.1:8000")
            or "http://127.0.0.1:8000"
        ),
        frontend_url=str(
            getattr(args, "frontend_url", "http://127.0.0.1:5173")
            or "http://127.0.0.1:5173"
        ),
        check_runtime=not bool(getattr(args, "skip_runtime", False)),
    )
    if args.json:
        _emit_json(payload)
    else:
        print(render_doctor_report(payload))
    return 1 if payload.get("status") == "fail" else 0


async def _run_prepare_handoff_command(args: argparse.Namespace) -> int:
    if not str(getattr(args, "run_id", "") or "").strip():
        _resolve_review_inputs(args)

    payload = await prepare_agent_handoff_for_mcp_async(
        agent=str(args.agent),
        run_id=str(getattr(args, "run_id", "") or "").strip() or None,
        prd_text=str(getattr(args, "prd_text", "") or "").strip() or None,
        prd_path=str(getattr(args, "prd_path", "") or "").strip() or None,
        source=str(getattr(args, "source", "") or "").strip() or None,
        options=_review_options_from_args(args),
    )
    if args.json:
        _emit_json(payload)
    else:
        print(f"Run ID   : {payload.get('run_id', '')}")
        print(f"Bundle   : {payload.get('bundle_id', '')}")
        print(f"Status   : {payload.get('status', '')}")
        print(f"Requests : {payload.get('request_count', 0)}")
        for item in payload.get("requests", []):
            print(f"- {item.get('agent', '')}: {item.get('request_path', '')}")
    return 1 if "error" in payload else 0


async def _run_async_command(args: argparse.Namespace) -> int:
    if args.command == "review":
        return await _run_review_command(args)
    if args.command == "prepare-handoff":
        return await _run_prepare_handoff_command(args)
    raise ValueError(f"unsupported command: {args.command}")


def run_cli(argv: list[str] | None = None) -> int:
    load_dotenv()
    setup_logging()
    try:
        args = _parse_args(argv)
        if args.command == "report":
            return _run_report_command(args)
        if args.command == "doctor":
            return _run_doctor_command(args)
        return asyncio.run(_run_async_command(args))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
