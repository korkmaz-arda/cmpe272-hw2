"""Correlation ids, and the guarantee that secrets never reach the logs."""

import json
import logging

import pytest

from app.logging_config import REQUEST_ID_HEADER, JsonFormatter, request_id_var
from app.webhook_security import compute_signature
from tests.conftest import SECRET, TOKEN, mock_github_only, signed_headers

pytestmark = mock_github_only


class TestRequestId:
    async def test_a_request_id_is_generated_when_absent(self, client):
        response = await client.get("/healthz")

        request_id = response.headers[REQUEST_ID_HEADER]
        assert len(request_id) == 32

    async def test_a_safe_inbound_request_id_is_reused(self, client):
        response = await client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-abc.123"})

        assert response.headers[REQUEST_ID_HEADER] == "trace-abc.123"

    @pytest.mark.parametrize(
        "hostile",
        ["with space", "line\nbreak", "x" * 65, "semi;colon", ""],
    )
    async def test_a_hostile_inbound_request_id_is_replaced(self, client, hostile):
        response = await client.get("/healthz", headers={REQUEST_ID_HEADER: hostile})

        assert response.headers[REQUEST_ID_HEADER] != hostile
        assert len(response.headers[REQUEST_ID_HEADER]) == 32

    async def test_each_request_gets_its_own_id(self, client):
        first = await client.get("/healthz")
        second = await client.get("/healthz")

        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]

    async def test_errors_also_carry_a_request_id(self, client):
        response = await client.post("/issues", json={})

        assert response.status_code == 400
        assert REQUEST_ID_HEADER in response.headers


class TestSecretsAreNotLogged:
    async def test_a_rejected_webhook_logs_no_secret_or_signature(self, client, caplog):
        raw = b'{"action": "opened"}'
        signature = compute_signature(raw, SECRET)
        with caplog.at_level(logging.DEBUG):
            response = await client.post(
                "/webhook",
                content=raw,
                headers={
                    "X-GitHub-Event": "issues",
                    "X-GitHub-Delivery": "d1",
                    "X-Hub-Signature-256": signature,
                },
            )

        assert response.status_code == 204
        logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
        assert SECRET not in logged
        assert signature not in logged
        assert TOKEN not in logged

    async def test_an_invalid_signature_logs_neither_signature_nor_body(self, client, caplog):
        bad_signature = "sha256=" + "e" * 64
        with caplog.at_level(logging.DEBUG):
            await client.post(
                "/webhook",
                content=b'{"action": "opened", "secret_data": "hunter2"}',
                headers={
                    "X-GitHub-Event": "issues",
                    "X-GitHub-Delivery": "d1",
                    "X-Hub-Signature-256": bad_signature,
                },
            )

        logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
        assert bad_signature not in logged
        assert "hunter2" not in logged
        assert SECRET not in logged

    async def test_a_github_auth_failure_does_not_log_the_token(self, client, caplog, httpx_mock):
        httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})
        with caplog.at_level(logging.DEBUG):
            response = await client.get("/issues")

        assert response.status_code == 401
        logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
        assert TOKEN not in logged
        assert TOKEN not in response.text

    async def test_a_successful_webhook_logs_only_safe_fields(self, client, caplog):
        raw = b'{"action": "opened", "issue": {"number": 42}}'
        with caplog.at_level(logging.INFO):
            await client.post("/webhook", content=raw, headers=signed_headers(raw))

        processed = [r for r in caplog.records if r.getMessage() == "webhook processed"]
        assert processed, "expected a webhook processed log line"
        record = processed[0]
        assert record.delivery_id == "11111111-2222-3333-4444-555555555555"
        assert record.event == "issues"
        assert record.action == "opened"
        assert record.issue_number == 42
        assert record.outcome == "processed"


class TestJsonFormatter:
    def test_renders_one_json_object_with_extras(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        record.delivery_id = "d1"
        token = request_id_var.set("req-123")
        try:
            payload = json.loads(JsonFormatter().format(record))
        finally:
            request_id_var.reset(token)

        assert payload["msg"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["request_id"] == "req-123"
        assert payload["delivery_id"] == "d1"
        assert payload["ts"].endswith("+00:00")

    def test_includes_the_traceback_for_exceptions(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )

        payload = json.loads(JsonFormatter().format(record))

        assert "kaboom" in payload["exc"]

    def test_unserialisable_extras_do_not_break_formatting(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        record.weird = object()

        payload = json.loads(JsonFormatter().format(record))

        assert "weird" in payload
