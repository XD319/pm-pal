"""Quick debug script for pm_pal agents.

Usage
-----
# Test parser only
python -m pm_pal.debug --agent parser

# Test reviewer only (uses parser output from a previous run, or built-in mock)
python -m pm_pal.debug --agent reviewer

# Test full pipeline: parser → reviewer
python -m pm_pal.debug --agent all

# Use your own requirement document
python -m pm_pal.debug --agent all --input docs/prd.md

# Enable verbose logging
python -m pm_pal.debug --agent all -v
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from .state import create_initial_state

SAMPLE_REQUIREMENT_DOC = """\
## User Management Module

1. The system shall allow users to register with email and password.
   - Passwords must be at least 8 characters with one uppercase and one digit.
   - A confirmation email must be sent within 30 seconds.

2. The system should provide a fast and user-friendly login experience.

3. Admin users shall be able to deactivate any user account, and the change \
must take effect immediately.
"""

MOCK_PARSED_ITEMS = [
    {
        "id": "REQ-001",
        "description": "User registration with email and password",
        "acceptance_criteria": [
            "Password minimum 8 characters with one uppercase and one digit",
            "Confirmation email sent within 30 seconds",
        ],
    },
    {
        "id": "REQ-002",
        "description": "Provide a fast and user-friendly login experience",
        "acceptance_criteria": [
            "Login should be fast",
            "Login should be user-friendly",
        ],
    },
    {
        "id": "REQ-003",
        "description": "Admin can deactivate any user account immediately",
        "acceptance_criteria": [
            "Admin role can deactivate accounts",
            "Deactivation takes effect immediately",
        ],
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug pm_pal agents")
    p.add_argument(
        "--agent",
        choices=["parser", "reviewer", "all"],
        default="all",
        help="Which agent(s) to run (default: all)",
    )
    p.add_argument(
        "--input", type=str, default=None, help="Path to requirement doc (optional)"
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Print raw LLM responses"
    )
    return p.parse_args()


def pretty(label: str, data: object) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()


async def debug_parser(requirement_doc: str, verbose: bool) -> dict:
    from .agents import parser_agent

    state = create_initial_state(requirement_doc)
    print("[parser] Running parser_agent.run() ...")
    result = await parser_agent.run(state)

    pretty("Parser — parsed_items", result.get("parsed_items", []))
    pretty("Parser — trace", result.get("trace", {}))

    if not result.get("parsed_items"):
        print("[parser] WARNING: parsed_items is empty — check trace for errors.")
    return result


async def debug_reviewer(parsed_items: list[dict], verbose: bool) -> dict:
    from .agents import reviewer_agent

    state = {
        "requirement_doc": "",
        "parsed_items": parsed_items,
        "review_results": [],
        "final_report": "",
        "trace": {},
    }
    print("[reviewer] Running reviewer_agent.run() ...")
    result = await reviewer_agent.run(state)

    pretty("Reviewer — review_results", result.get("review_results", []))
    pretty("Reviewer — trace", result.get("trace", {}))

    if not result.get("review_results"):
        print("[reviewer] WARNING: review_results is empty — check trace for errors.")
    return result


async def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.input:
        requirement_doc = Path(args.input).read_text(encoding="utf-8")
    else:
        requirement_doc = SAMPLE_REQUIREMENT_DOC
        print("[debug] Using built-in sample requirement document.\n")

    if args.agent in ("parser", "all"):
        parser_result = await debug_parser(requirement_doc, args.verbose)
        parsed_items = parser_result.get("parsed_items", [])
    else:
        parsed_items = []

    if args.agent in ("reviewer", "all"):
        items = parsed_items if parsed_items else MOCK_PARSED_ITEMS
        if not parsed_items:
            print(
                "[debug] No parser output available — using built-in mock parsed_items.\n"
            )
        await debug_reviewer(items, args.verbose)


if __name__ == "__main__":
    asyncio.run(main())
