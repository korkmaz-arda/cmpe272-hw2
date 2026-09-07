"""SQLite persistence for webhook deliveries.

The table is deliberately tiny and its primary key *is* the assignment's
deduplication key, ``(delivery_id, action)``. There is no ORM and no queue:
inserts are idempotent by construction, so a GitHub redelivery is a no-op.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_id  TEXT    NOT NULL,
    event        TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    issue_number INTEGER,
    status       TEXT    NOT NULL DEFAULT 'processed',
    error        TEXT,
    received_at  TEXT    NOT NULL,
    PRIMARY KEY (delivery_id, action)
);

CREATE INDEX IF NOT EXISTS ix_deliveries_received_at
    ON webhook_deliveries (received_at DESC);
"""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


async def init_db(db_path: str) -> None:
    """Create the database file and schema if they do not exist yet."""
    parent = Path(db_path).expanduser().parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await db.commit()


async def record_delivery(
    db_path: str,
    *,
    delivery_id: str,
    event: str,
    action: str,
    issue_number: int | None = None,
    received_at: str | None = None,
    status: str = "processed",
    error: str | None = None,
) -> bool:
    """Store a delivery, ignoring one we have already stored.

    Returns ``True`` when a new row was written and ``False`` when this
    ``(delivery_id, action)`` was already present. Both outcomes mean the
    delivery is safely persisted; only an exception means it is not.
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO webhook_deliveries
                (delivery_id, event, action, issue_number, status, error, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (delivery_id, action) DO NOTHING
            """,
            (
                delivery_id,
                event,
                action,
                issue_number,
                status,
                error,
                received_at or utc_now_iso(),
            ),
        )
        await db.commit()
        return cursor.rowcount == 1


async def recent_deliveries(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent stored deliveries, newest first, in the public shape."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT delivery_id, event, action, issue_number, received_at
              FROM webhook_deliveries
             ORDER BY received_at DESC, rowid DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()

    return [
        {
            "id": row["delivery_id"],
            "event": row["event"],
            "action": row["action"],
            "issue_number": row["issue_number"],
            "timestamp": row["received_at"],
        }
        for row in rows
    ]
