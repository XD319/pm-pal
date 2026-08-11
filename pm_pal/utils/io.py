"""I/O helpers for persisting agent artefacts."""

from __future__ import annotations

import os


def save_raw_agent_output(run_dir: str, agent_name: str, content: str) -> str:
    """Write *content* to ``<run_dir>/raw_agent_outputs/<agent_name>.txt``.

    Creates intermediate directories when they do not exist.
    Returns the absolute path of the written file.
    """
    raw_dir = os.path.join(run_dir, "raw_agent_outputs")
    os.makedirs(raw_dir, exist_ok=True)

    path = os.path.join(raw_dir, f"{agent_name}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    return os.path.abspath(path)
