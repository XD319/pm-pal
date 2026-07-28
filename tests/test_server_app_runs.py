from __future__ import annotations

import pytest

from prd_pal.server import app as app_module


@pytest.mark.asyncio
async def test_allocate_unique_run_dir_waits_out_same_second_collision(
    tmp_path, monkeypatch
):
    generated = iter(
        [
            "20260309T040506Z",
            "20260309T040506Z",
            "20260309T040507Z",
        ]
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    monkeypatch.setattr(app_module, "make_run_id", lambda: next(generated))
    app_module._jobs.clear()

    first_run_id, first_run_dir = await app_module._allocate_unique_run_dir()
    second_run_id, second_run_dir = await app_module._allocate_unique_run_dir()

    assert first_run_id == "20260309T040506Z"
    assert second_run_id == "20260309T040507Z"
    assert first_run_dir.exists()
    assert second_run_dir.exists()
    assert first_run_dir != second_run_dir
    app_module._jobs.clear()
