import pytest

from pm_pal.platform import LocalArtifactStore, LocalJobQueue


@pytest.mark.asyncio
async def test_local_ports_are_idempotent(tmp_path):
    store = LocalArtifactStore(tmp_path)
    assert await store.put_json("a", {"ok": True})
    assert await store.get_json("a") == {"ok": True}
    queue = LocalJobQueue()

    async def handler(payload):
        return {"value": payload["value"]}

    assert (
        await queue.enqueue(
            key="one", kind="test", payload={"value": 1}, handler=handler
        )
    )["status"] == "completed"
    assert (
        await queue.enqueue(
            key="one", kind="test", payload={"value": 2}, handler=handler
        )
    )["result"] == {"value": 1}
