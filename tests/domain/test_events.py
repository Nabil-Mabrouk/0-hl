"""T-M1.1-05/06 — event journal sequences, idempotence, checksum, consumer cursors."""

import uuid

import pytest

from app.domain.trading.errors import StateConflict
from app.domain.trading.events import (
    advance_cursor,
    append_event,
    get_cursor,
    read_events_after,
    verify_checksum,
)

pytestmark = pytest.mark.anyio


async def _append(db, agg="a1", **kw):
    return await append_event(
        db, event_type=kw.pop("event_type", "TEST_EVENT"), aggregate_type="test",
        aggregate_id=agg, payload=kw.pop("payload", {"n": "1"}), source="test", **kw,
    )


async def test_sequences_positions_and_checksum(db):
    e1 = await _append(db, "a1")
    e2 = await _append(db, "a2")
    e3 = await _append(db, "a1")
    await db.commit()
    assert (e1.aggregate_sequence, e2.aggregate_sequence, e3.aggregate_sequence) == (1, 1, 2)
    assert e1.position < e2.position < e3.position
    assert all(verify_checksum(e) for e in (e1, e2, e3))
    e1.payload = {"n": "tampered"}
    assert not verify_checksum(e1)


async def test_duplicate_event_id_is_ignored(db):
    eid = uuid.uuid4()
    first = await _append(db, event_id=eid)
    again = await _append(db, event_id=eid, payload={"n": "different"})
    await db.commit()
    assert again.position == first.position
    assert len(await read_events_after(db, 0)) == 1


async def test_correlation_defaults_to_event_id_and_is_kept(db):
    e = await _append(db)
    corr = uuid.uuid4()
    child = await _append(db, correlation_id=corr, causation_id=e.event_id)
    assert e.correlation_id == e.event_id
    assert child.correlation_id == corr and child.causation_id == e.event_id


async def test_cursor_resume_without_loss_or_duplicate(session_factory):
    async with session_factory() as db:
        for i in range(5):
            await _append(db, f"agg{i}")
        await db.commit()

    seen: list[int] = []
    # first consumer pass processes 2 events, then "crashes" before committing the 3rd
    async with session_factory() as db:
        batch = await read_events_after(db, await get_cursor(db, "agent:test"), limit=2)
        seen += [e.position for e in batch]
        await advance_cursor(db, "agent:test", batch[-1].position)
        await db.commit()
    async with session_factory() as db:
        batch = await read_events_after(db, await get_cursor(db, "agent:test"), limit=1)
        await advance_cursor(db, "agent:test", batch[-1].position)
        await db.rollback()  # crash: cursor move lost, event will be re-read
    async with session_factory() as db:
        batch = await read_events_after(db, await get_cursor(db, "agent:test"))
        seen += [e.position for e in batch]
        await advance_cursor(db, "agent:test", batch[-1].position)
        await db.commit()

    async with session_factory() as db:
        all_positions = [e.position for e in await read_events_after(db, 0)]
    assert seen == all_positions  # every event exactly once, in order


async def test_cursor_never_moves_backwards(db):
    await advance_cursor(db, "agent:x", 10)
    with pytest.raises(StateConflict):
        await advance_cursor(db, "agent:x", 5)


async def test_filter_by_bot_and_type(db):
    bot = uuid.uuid4()
    await _append(db, bot_id=bot, event_type="A")
    await _append(db, event_type="A")
    await _append(db, bot_id=bot, event_type="B")
    assert [e.event_type for e in await read_events_after(db, 0, bot_id=bot)] == ["A", "B"]
    assert len(await read_events_after(db, 0, event_types=["A"])) == 2
