"""POST /webhook: signature, allow-lists, deduplication, and failure handling."""

import json

import pytest

from app.db import recent_deliveries
from app.webhook_security import DELIVERY_HEADER, compute_signature
from tests.conftest import SECRET, load_fixture, signed_headers

ISSUES_OPENED = load_fixture("webhook_issues_opened")
COMMENT_CREATED = load_fixture("webhook_issue_comment_created")
PING = load_fixture("webhook_ping")


def body_of(payload: dict) -> bytes:
    return json.dumps(payload).encode()


async def post_webhook(client, payload, *, event="issues", delivery_id="delivery-1", **kwargs):
    raw = body_of(payload)
    return await client.post(
        "/webhook",
        content=raw,
        headers=signed_headers(raw, event=event, delivery_id=delivery_id, **kwargs),
    )


class TestAcceptedDeliveries:
    async def test_issues_opened_returns_an_empty_204(self, client):
        response = await post_webhook(client, ISSUES_OPENED)

        assert response.status_code == 204
        assert response.content == b""

    async def test_issue_comment_created_returns_204(self, client):
        response = await post_webhook(client, COMMENT_CREATED, event="issue_comment")

        assert response.status_code == 204

    async def test_ping_returns_204(self, client):
        response = await post_webhook(client, PING, event="ping")

        assert response.status_code == 204

    @pytest.mark.parametrize(
        "action",
        ["opened", "closed", "reopened", "edited", "labeled", "typed", "field_added"],
    )
    async def test_documented_issue_actions_are_accepted(self, client, action):
        payload = {**ISSUES_OPENED, "action": action}

        response = await post_webhook(client, payload, delivery_id=f"d-{action}")

        assert response.status_code == 204

    async def test_a_delivery_without_an_issue_object_does_not_crash(self, client):
        response = await post_webhook(client, {"action": "opened"})

        assert response.status_code == 204


class TestRejectedDeliveries:
    async def test_invalid_signature_is_401(self, client):
        raw = body_of(ISSUES_OPENED)
        headers = signed_headers(raw)
        headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64

        response = await client.post("/webhook", content=raw, headers=headers)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_signature"

    async def test_missing_signature_is_401(self, client):
        response = await client.post(
            "/webhook", content=body_of(ISSUES_OPENED), headers={"X-GitHub-Event": "issues"}
        )

        assert response.status_code == 401

    async def test_body_tampered_after_signing_is_401(self, client):
        raw = body_of(ISSUES_OPENED)
        headers = signed_headers(raw)
        tampered = body_of({**ISSUES_OPENED, "action": "closed"})

        response = await client.post("/webhook", content=tampered, headers=headers)

        assert response.status_code == 401

    async def test_signature_is_checked_before_the_event_allow_list(self, client):
        """An unsigned unsupported event is a 401, not a 400."""
        raw = body_of({"action": "created"})

        response = await client.post(
            "/webhook",
            content=raw,
            headers={"X-GitHub-Event": "star", "X-GitHub-Delivery": "d"},
        )

        assert response.status_code == 401

    async def test_unknown_event_is_400(self, client):
        response = await post_webhook(client, {"action": "created"}, event="star")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_event"

    async def test_unknown_action_is_400(self, client):
        response = await post_webhook(client, {**ISSUES_OPENED, "action": "exploded"})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_action"

    async def test_an_issue_comment_action_is_not_valid_for_issues(self, client):
        response = await post_webhook(client, {"action": "created"}, event="issues")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_action"

    async def test_missing_action_is_400(self, client):
        response = await post_webhook(client, {"issue": {"number": 1}})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_action"

    async def test_non_string_action_is_400(self, client):
        response = await post_webhook(client, {"action": 7})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_action"

    async def test_malformed_json_is_400(self, client):
        raw = b"{not json"

        response = await client.post("/webhook", content=raw, headers=signed_headers(raw))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_payload"

    async def test_a_json_array_payload_is_400(self, client):
        raw = b"[1, 2, 3]"

        response = await client.post("/webhook", content=raw, headers=signed_headers(raw))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_payload"


class TestDeliveryIdIsRequired:
    async def test_missing_delivery_header_is_400(self, client):
        raw = body_of(ISSUES_OPENED)
        headers = signed_headers(raw)
        del headers[DELIVERY_HEADER]

        response = await client.post("/webhook", content=raw, headers=headers)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "missing_delivery_id"

    async def test_blank_delivery_header_is_400(self, client):
        raw = body_of(ISSUES_OPENED)
        headers = signed_headers(raw)
        headers[DELIVERY_HEADER] = "   "

        response = await client.post("/webhook", content=raw, headers=headers)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "missing_delivery_id"

    async def test_nothing_is_stored_for_a_rejected_delivery(self, client, settings):
        raw = body_of(ISSUES_OPENED)
        headers = signed_headers(raw)
        del headers[DELIVERY_HEADER]

        await client.post("/webhook", content=raw, headers=headers)

        assert await recent_deliveries(settings.db_path) == []


class TestPersistenceAndIdempotency:
    async def test_a_delivery_is_stored_in_the_public_shape(self, client, settings):
        await post_webhook(client, ISSUES_OPENED, delivery_id="abc-123")

        rows = await recent_deliveries(settings.db_path)

        assert rows == [
            {
                "id": "abc-123",
                "event": "issues",
                "action": "opened",
                "issue_number": 42,
                "timestamp": rows[0]["timestamp"],
            }
        ]

    async def test_a_redelivery_is_a_safe_no_op(self, client, settings):
        first = await post_webhook(client, ISSUES_OPENED, delivery_id="dup-1")
        second = await post_webhook(client, ISSUES_OPENED, delivery_id="dup-1")

        assert (first.status_code, second.status_code) == (204, 204)
        assert len(await recent_deliveries(settings.db_path)) == 1

    async def test_the_same_delivery_id_with_a_different_action_is_a_new_row(
        self, client, settings
    ):
        await post_webhook(client, ISSUES_OPENED, delivery_id="same-id")
        await post_webhook(client, {**ISSUES_OPENED, "action": "closed"}, delivery_id="same-id")

        rows = await recent_deliveries(settings.db_path)

        assert sorted(row["action"] for row in rows) == ["closed", "opened"]

    async def test_ping_is_stored_with_a_non_null_action(self, client, settings):
        await post_webhook(client, PING, event="ping", delivery_id="ping-1")
        await post_webhook(client, PING, event="ping", delivery_id="ping-1")

        rows = await recent_deliveries(settings.db_path)

        assert len(rows) == 1
        assert rows[0]["action"] == "ping"
        assert rows[0]["issue_number"] is None

    async def test_a_persistence_failure_is_not_acknowledged(self, client, monkeypatch):
        """A storage error must return 5xx so GitHub retries, never a false 204."""

        async def boom(*_args, **_kwargs):
            raise OSError("disk is gone")

        monkeypatch.setattr("app.routes.webhooks.record_delivery", boom)

        response = await post_webhook(client, ISSUES_OPENED)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"


async def test_signature_over_raw_bytes_not_reserialised_json(client):
    """Whitespace inside the body is part of what GitHub signed."""
    raw = b'{"action":   "opened",  "issue": {"number": 42}}'

    response = await client.post(
        "/webhook",
        content=raw,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "raw-bytes",
            "X-Hub-Signature-256": compute_signature(raw, SECRET),
        },
    )

    assert response.status_code == 204
