"""SQLite persistence: schema, dedupe key, ordering, and durability."""

import aiosqlite
import pytest

from app.db import init_db, recent_deliveries, record_delivery, utc_now_iso


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "nested" / "webhooks.db")


async def test_init_creates_the_file_and_parent_directory(db_path):
    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_deliveries'"
        )
        assert await cursor.fetchone() is not None


async def test_init_is_idempotent(db_path):
    await init_db(db_path)
    await init_db(db_path)

    assert await recent_deliveries(db_path) == []


async def test_insert_then_duplicate(db_path):
    await init_db(db_path)

    first = await record_delivery(
        db_path, delivery_id="d1", event="issues", action="opened", issue_number=1
    )
    second = await record_delivery(
        db_path, delivery_id="d1", event="issues", action="opened", issue_number=1
    )

    assert (first, second) == (True, False)
    assert len(await recent_deliveries(db_path)) == 1


async def test_the_dedupe_key_is_delivery_id_plus_action(db_path):
    await init_db(db_path)

    await record_delivery(db_path, delivery_id="d1", event="issues", action="opened")
    created = await record_delivery(db_path, delivery_id="d1", event="issues", action="closed")

    assert created is True
    assert len(await recent_deliveries(db_path)) == 2


async def test_recent_deliveries_are_newest_first_and_limited(db_path):
    await init_db(db_path)
    for index in range(5):
        await record_delivery(
            db_path,
            delivery_id=f"d{index}",
            event="issues",
            action="opened",
            received_at=f"2026-09-0{index + 1}T00:00:00.000+00:00",
        )

    rows = await recent_deliveries(db_path, limit=3)

    assert [row["id"] for row in rows] == ["d4", "d3", "d2"]


async def test_rows_survive_reopening_the_same_file(db_path):
    await init_db(db_path)
    await record_delivery(db_path, delivery_id="persisted", event="ping", action="ping")

    # A fresh connection, as a restarted process would make.
    await init_db(db_path)
    rows = await recent_deliveries(db_path)

    assert [row["id"] for row in rows] == ["persisted"]


async def test_only_the_public_fields_are_returned(db_path):
    await init_db(db_path)
    await record_delivery(
        db_path,
        delivery_id="d1",
        event="issues",
        action="opened",
        issue_number=7,
        status="failed",
        error="internal detail that must not be exposed",
    )

    rows = await recent_deliveries(db_path)

    assert set(rows[0]) == {"id", "event", "action", "issue_number", "timestamp"}


async def test_status_and_error_are_stored_for_internal_diagnostics(db_path):
    await init_db(db_path)
    await record_delivery(
        db_path, delivery_id="d1", event="issues", action="opened", status="failed", error="boom"
    )

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT status, error FROM webhook_deliveries")
        assert await cursor.fetchone() == ("failed", "boom")


async def test_issue_number_may_be_null(db_path):
    await init_db(db_path)
    await record_delivery(db_path, delivery_id="ping-1", event="ping", action="ping")

    assert (await recent_deliveries(db_path))[0]["issue_number"] is None


def test_timestamps_are_iso_utc():
    stamp = utc_now_iso()

    assert stamp.endswith("+00:00")
    assert "T" in stamp
