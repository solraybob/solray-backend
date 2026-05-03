"""
Memory layer regression tests.

These tests cover the five scenarios called out in Codex's memory
review, plus a few related guards:

  1. A new memory written with surface_next=True is visible on the
     next call to get_user_memories. This is the precondition for
     surface_next surfacing the memory in the next chat session.
  2. After reset_surface_next_flags is called, the surface_next
     attribute is False on every row. This is the post-condition that
     "consumes" the flag once per session.
  3. delete_all_user_memories removes every row for the user and
     returns the count. The /memory DELETE endpoint depends on this.
  4. update_user_memories merges, never replaces. An old memory is
     preserved when a new synthesis returns a non-overlapping list.
  5. The 50-memory cap drops oldest entries first but always keeps
     surface_next=True rows.

Guard tests:

  6. update_user_memories with the same (category, fingerprint) updates
     the existing row in place rather than creating a duplicate.
  7. delete_all_user_memories on a user with zero memories returns 0
     and does not raise.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from db.database import (
    UserMemory,
    add_user_memory,
    delete_all_user_memories,
    get_user_memories,
    reset_surface_next_flags,
    update_user_memories,
)


@pytest.mark.asyncio
async def test_surface_next_visible_to_next_load(db, user):
    """A surface_next=True memory must be present when memories are
    loaded for the next chat turn. This is the top-level promise of
    the surface_next mechanism."""
    await add_user_memory(
        db,
        user.id,
        category="life_event",
        content="Just got engaged. Wants to talk about commitment patterns.",
        surface_next=True,
    )
    memories = await get_user_memories(db, user.id)
    assert len(memories) == 1
    assert memories[0].surface_next is True
    assert "engaged" in memories[0].content


@pytest.mark.asyncio
async def test_surface_next_cleared_after_reset(db, user):
    """After reset_surface_next_flags fires (post-response on a new
    session), every memory has surface_next=False."""
    await add_user_memory(db, user.id, "life_event", "engaged", surface_next=True)
    await add_user_memory(db, user.id, "theme", "self-worth", surface_next=True)
    await add_user_memory(db, user.id, "insight", "saturn 7th",  surface_next=False)

    before = await get_user_memories(db, user.id)
    assert sum(1 for m in before if m.surface_next) == 2

    await reset_surface_next_flags(db, user.id)

    after = await get_user_memories(db, user.id)
    assert sum(1 for m in after if m.surface_next) == 0
    # Content is unchanged; only the flag flipped.
    contents = {m.content for m in after}
    assert contents == {"engaged", "self-worth", "saturn 7th"}


@pytest.mark.asyncio
async def test_clear_memory_deletes_all_rows(db, user):
    """delete_all_user_memories hard-deletes every row for the user
    and returns the count. /memory DELETE depends on this."""
    await add_user_memory(db, user.id, "life_event", "first session")
    await add_user_memory(db, user.id, "theme",      "perfectionism")
    await add_user_memory(db, user.id, "insight",    "saturn return")

    count = await delete_all_user_memories(db, user.id)
    assert count == 3

    after = await get_user_memories(db, user.id)
    assert after == []


@pytest.mark.asyncio
async def test_clear_memory_handles_zero_rows(db, user):
    """Calling delete on a user with no memories returns 0 cleanly,
    never raises."""
    count = await delete_all_user_memories(db, user.id)
    assert count == 0


@pytest.mark.asyncio
async def test_synthesis_merge_preserves_old_memories(db, user):
    """update_user_memories is a merge, not a replace. A new synthesis
    returning unrelated memories must not erase prior continuity."""
    await add_user_memory(
        db, user.id, "life_event",
        "Started therapy in March 2026, working through grief",
        surface_next=False,
    )
    await add_user_memory(
        db, user.id, "communication_style",
        "Processes through writing, prefers concrete language",
        surface_next=False,
    )

    # Simulate a synthesis turn that surfaces something new but does
    # not mention the existing memories.
    new_synthesis = [
        {
            "category": "insight",
            "content": "Realized her Saturn 7th explains commitment fear",
            "surface_next": True,
        },
    ]
    await update_user_memories(db, user.id, new_synthesis)

    after = await get_user_memories(db, user.id)
    contents = {m.content for m in after}
    assert "Started therapy in March 2026, working through grief" in contents
    assert "Processes through writing, prefers concrete language" in contents
    assert "Realized her Saturn 7th explains commitment fear" in contents
    assert len(after) == 3


@pytest.mark.asyncio
async def test_synthesis_merge_updates_existing_in_place(db, user):
    """When a new memory shares (category, fingerprint) with an
    existing one, the existing row is updated in place rather than
    duplicated. Keeps the table tidy across many synthesis turns."""
    await add_user_memory(
        db, user.id, "theme",
        "Struggles with self-worth, particularly around career",
    )

    # Slightly refined version with the same fingerprint (lowercase /
    # punctuation collapse maps both to the same key).
    refined = [
        {
            "category": "theme",
            "content": "Struggles with self-worth, particularly around career.",
            "surface_next": True,
        },
    ]
    await update_user_memories(db, user.id, refined)

    after = await get_user_memories(db, user.id)
    assert len(after) == 1
    assert after[0].surface_next is True
    # Content reflects the refinement (period at the end).
    assert after[0].content.endswith(".")


@pytest.mark.asyncio
async def test_cap_at_50_keeps_surface_next_and_recent(db, user):
    """When the merge would push the row count over 50, the oldest
    entries are pruned first but surface_next=True rows in the
    incoming synthesis are always preserved.

    Note on semantics: surface_next is a PER-TURN flag. update_user_memories
    resets all existing entries' surface_next to False at the start of the
    merge, then applies fresh flags from the new memories. The cap-at-50
    prune runs AFTER that, so "surface_next=True at prune time" means
    "in the incoming synthesis." This is the correct design: the LLM
    re-flags whatever is still relevant on every synthesis turn; old
    flags don't persist across cycles unintentionally.
    """
    # 50 ordinary entries already in the DB
    for i in range(50):
        await add_user_memory(
            db, user.id, "theme",
            f"recent ordinary memory {i:02d}",
            surface_next=False,
        )

    # New synthesis: 5 ordinary + 1 critical surface_next entry. The
    # critical one MUST survive the cap.
    new_synthesis = [
        {"category": "insight", "content": f"new insight {i}", "surface_next": False}
        for i in range(5)
    ]
    new_synthesis.append({
        "category": "life_event",
        "content": "Just got engaged: must surface in next session",
        "surface_next": True,
    })
    await update_user_memories(db, user.id, new_synthesis)

    # Direct count of rows (bypasses the 50-row helper)
    from sqlalchemy import select, func
    from db.database import UserMemory as UM
    total = (await db.execute(
        select(func.count()).select_from(UM).where(UM.user_id == user.id)
    )).scalar()
    assert total == 50, f"expected 50 rows after cap, got {total}"

    # The flagged memory must be present and still flagged
    found = (await db.execute(
        select(UM).where(
            UM.user_id == user.id,
            UM.content == "Just got engaged: must surface in next session",
        )
    )).scalar_one_or_none()
    assert found is not None, "surface_next memory was evicted by the cap"
    assert found.surface_next is True, "surface_next flag was cleared by the cap"


@pytest.mark.asyncio
async def test_update_memories_resets_existing_surface_next_flags(db, user):
    """When update_user_memories runs, any existing memory not in the
    new set has its surface_next flag reset to False. The flag is
    per-turn; fresh flags come from the new synthesis. This is the
    db-level companion to the api-level reset_surface_next_flags
    call."""
    await add_user_memory(db, user.id, "theme", "old flagged theme", surface_next=True)
    new_synthesis = [
        {"category": "insight", "content": "new", "surface_next": True},
    ]
    await update_user_memories(db, user.id, new_synthesis)

    after = await get_user_memories(db, user.id)
    flagged = {m.content for m in after if m.surface_next}
    assert flagged == {"new"}, "old flagged memory should have been reset"
