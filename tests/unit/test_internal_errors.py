"""An unexpected bug in our own code becomes a 500 in the standard envelope."""

import logging

from tests.conftest import mock_github_only

pytestmark = mock_github_only


async def test_an_unexpected_exception_becomes_a_500(client, monkeypatch, caplog, httpx_mock):
    httpx_mock.add_response(json={"number": 42})

    def explode(_payload):
        raise RuntimeError("a bug in our mapping code")

    monkeypatch.setattr("app.routes.issues.issue_from_github", explode)

    with caplog.at_level(logging.ERROR):
        response = await client.get("/issues/42", headers={"X-Request-ID": "trace-1"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected internal error occurred."}
    }
    # The caller learns nothing about the internals...
    assert "a bug in our mapping code" not in response.text
    # ...but we logged the whole thing, correlated to the request.
    assert any("a bug in our mapping code" in (r.exc_text or "") for r in caplog.records)
    assert response.headers["X-Request-ID"] == "trace-1"


async def test_the_response_is_still_json_for_a_500(client, monkeypatch, httpx_mock):
    httpx_mock.add_response(json={"number": 1})

    def explode(_payload):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.routes.issues.issue_from_github", explode)

    response = await client.get("/issues/1")

    assert response.headers["content-type"].startswith("application/json")
