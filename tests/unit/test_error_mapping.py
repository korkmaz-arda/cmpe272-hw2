"""GitHub failures must become our own errors, never a raw upstream body."""

import httpx
import pytest

from tests.conftest import ISSUES_URL, load_fixture, mock_github_only

pytestmark = mock_github_only

GITHUB_422 = load_fixture("github_422")


def assert_envelope(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == code
    return payload["error"]


async def test_github_401_becomes_our_401(client, httpx_mock):
    httpx_mock.add_response(method="GET", status_code=401, json={"message": "Bad credentials"})

    error = assert_envelope(await client.get("/issues/42"), 401, "github_unauthorized")

    assert "GITHUB_TOKEN" in error["message"]


async def test_github_403_becomes_our_403(client, httpx_mock):
    httpx_mock.add_response(
        method="GET", status_code=403, json={"message": "Resource not accessible"}
    )

    error = assert_envelope(await client.get("/issues/42"), 403, "github_forbidden")

    assert "permission" in error["message"].lower()


async def test_github_404_becomes_our_404(client, httpx_mock):
    httpx_mock.add_response(method="GET", status_code=404, json={"message": "Not Found"})

    assert_envelope(await client.get("/issues/999"), 404, "not_found")


async def test_github_422_becomes_a_400_with_summarised_details(client, httpx_mock):
    httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=422, json=GITHUB_422)

    error = assert_envelope(
        await client.post("/issues", json={"title": "x"}), 400, "github_validation_failed"
    )

    assert "Validation Failed" in error["message"]
    assert error["details"]["github_errors"] == [
        {"resource": "Issue", "field": "title", "code": "missing_field"}
    ]
    # The rest of GitHub's body is not forwarded.
    assert "documentation_url" not in str(error)


async def test_github_422_without_an_errors_array_still_maps_cleanly(client, httpx_mock):
    httpx_mock.add_response(
        method="POST", url=ISSUES_URL, status_code=422, json={"message": "Validation Failed"}
    )

    error = assert_envelope(
        await client.post("/issues", json={"title": "x"}), 400, "github_validation_failed"
    )

    assert "details" not in error


async def test_other_github_4xx_becomes_a_400(client, httpx_mock):
    httpx_mock.add_response(method="GET", status_code=410, json={"message": "Gone"})

    assert_envelope(await client.get("/issues/42"), 400, "github_bad_request")


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_github_5xx_becomes_a_503(client, httpx_mock, status):
    httpx_mock.add_response(method="GET", status_code=status, text="upstream exploded")

    error = assert_envelope(await client.get("/issues/42"), 503, "github_unavailable")

    assert "upstream exploded" not in str(error)


async def test_github_timeout_becomes_a_503(client, httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("too slow"))

    error = assert_envelope(await client.get("/issues/42"), 503, "github_unavailable")

    assert "timed out" in error["message"].lower()


async def test_github_connection_error_becomes_a_503(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("no route to host"))

    assert_envelope(await client.get("/issues/42"), 503, "github_unavailable")


async def test_non_json_github_error_does_not_crash_the_mapping(client, httpx_mock):
    httpx_mock.add_response(method="GET", status_code=404, text="<html>nope</html>")

    assert_envelope(await client.get("/issues/42"), 404, "not_found")


async def test_error_mapping_applies_to_every_route(client, httpx_mock):
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})

    for response in (
        await client.get("/issues"),
        await client.post("/issues", json={"title": "x"}),
        await client.patch("/issues/1", json={"state": "closed"}),
        await client.post("/issues/1/comments", json={"body": "hi"}),
    ):
        assert_envelope(response, 401, "github_unauthorized")
