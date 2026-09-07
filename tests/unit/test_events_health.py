"""GET /events and GET /healthz."""

import pytest

from app.db import record_delivery


class TestHealth:
    async def test_returns_ok(self, client):
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestEvents:
    async def test_empty_database_returns_an_empty_array(self, client):
        response = await client.get("/events")

        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_the_assignment_shape_only(self, client, settings):
        await record_delivery(
            settings.db_path,
            delivery_id="abc-123",
            event="issues",
            action="opened",
            issue_number=42,
            status="processed",
            error=None,
        )

        body = (await client.get("/events")).json()

        assert len(body) == 1
        assert set(body[0]) == {"id", "event", "action", "issue_number", "timestamp"}
        assert body[0]["id"] == "abc-123"
        assert body[0]["issue_number"] == 42

    async def test_internal_columns_are_not_exposed(self, client, settings):
        await record_delivery(
            settings.db_path,
            delivery_id="abc-123",
            event="issues",
            action="opened",
            status="failed",
            error="internal detail",
        )

        text = (await client.get("/events")).text

        assert "internal detail" not in text
        assert "failed" not in text

    async def test_newest_first(self, client, settings):
        for index in range(3):
            await record_delivery(
                settings.db_path,
                delivery_id=f"d{index}",
                event="issues",
                action="opened",
                received_at=f"2026-09-0{index + 1}T00:00:00.000+00:00",
            )

        body = (await client.get("/events")).json()

        assert [row["id"] for row in body] == ["d2", "d1", "d0"]

    async def test_limit_is_applied(self, client, settings):
        for index in range(5):
            await record_delivery(
                settings.db_path, delivery_id=f"d{index}", event="issues", action="opened"
            )

        body = (await client.get("/events", params={"limit": 2})).json()

        assert len(body) == 2

    @pytest.mark.parametrize("limit", [0, -1, 201, 1000])
    async def test_limit_out_of_range_is_400(self, client, limit):
        response = await client.get("/events", params={"limit": limit})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"

    async def test_default_limit_is_twenty(self, client, settings):
        for index in range(25):
            await record_delivery(
                settings.db_path, delivery_id=f"d{index}", event="issues", action="opened"
            )

        assert len((await client.get("/events")).json()) == 20
