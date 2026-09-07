"""Rate limiting is detected from headers and body, not from a 429 alone."""

import time

import httpx
import pytest

from app.errors import is_rate_limited, retry_after_seconds
from tests.conftest import mock_github_only

pytestmark = mock_github_only


def _response(status: int, headers: dict[str, str] | None = None, **kwargs) -> httpx.Response:
    return httpx.Response(status_code=status, headers=headers or {}, **kwargs)


class TestDetection:
    def test_403_with_exhausted_primary_limit(self):
        assert is_rate_limited(_response(403, {"x-ratelimit-remaining": "0"})) is True

    def test_403_with_retry_after_is_a_secondary_limit(self):
        assert is_rate_limited(_response(403, {"retry-after": "30"})) is True

    def test_403_whose_body_mentions_a_rate_limit(self):
        response = _response(403, json={"message": "You have exceeded a secondary rate limit"})
        assert is_rate_limited(response) is True

    def test_plain_403_is_not_rate_limiting(self):
        response = _response(
            403,
            {"x-ratelimit-remaining": "4999"},
            json={"message": "Resource not accessible by personal access token"},
        )
        assert is_rate_limited(response) is False

    def test_429_with_headers(self):
        assert is_rate_limited(_response(429, {"x-ratelimit-remaining": "0"})) is True

    @pytest.mark.parametrize("status", [200, 401, 404, 500])
    def test_other_statuses_are_never_rate_limiting(self, status):
        assert is_rate_limited(_response(status, {"x-ratelimit-remaining": "0"})) is False


class TestRetryAfter:
    def test_prefers_the_retry_after_header(self):
        assert retry_after_seconds(_response(403, {"retry-after": "45"})) == 45

    def test_derives_from_the_reset_epoch(self):
        now = time.time()
        response = _response(403, {"x-ratelimit-reset": str(int(now) + 90)})
        assert retry_after_seconds(response, now=now) == 90

    def test_never_returns_a_negative_wait(self):
        now = time.time()
        response = _response(403, {"x-ratelimit-reset": str(int(now) - 500)})
        assert retry_after_seconds(response, now=now) == 0

    def test_falls_back_to_a_default(self):
        assert retry_after_seconds(_response(403, {})) == 60

    @pytest.mark.parametrize(
        "headers",
        [{"retry-after": "soon"}, {"x-ratelimit-reset": "never"}],
    )
    def test_unparseable_values_fall_back(self, headers):
        assert retry_after_seconds(_response(403, headers)) == 60


class TestThroughTheApi:
    async def test_403_rate_limit_surfaces_as_429(self, client, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            status_code=403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 60)},
            json={"message": "API rate limit exceeded"},
        )

        response = await client.get("/issues")

        assert response.status_code == 429
        body = response.json()
        assert body["error"]["code"] == "rate_limited"
        assert 0 < body["error"]["details"]["retry_after"] <= 60
        assert response.headers["Retry-After"] == str(body["error"]["details"]["retry_after"])

    async def test_429_with_retry_after_is_propagated(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", status_code=429, headers={"retry-after": "30"})

        response = await client.get("/issues")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "30"
        assert response.json()["error"]["details"]["retry_after"] == 30

    async def test_secondary_rate_limit_message_surfaces_as_429(self, client, httpx_mock):
        httpx_mock.add_response(
            method="POST",
            status_code=403,
            json={
                "message": "You have exceeded a secondary rate limit. Please wait a few minutes."
            },
        )

        response = await client.post("/issues", json={"title": "x"})

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"

    async def test_a_plain_permission_403_stays_a_403(self, client, httpx_mock):
        httpx_mock.add_response(
            method="GET",
            status_code=403,
            headers={"x-ratelimit-remaining": "4998"},
            json={"message": "Resource not accessible by personal access token"},
        )

        response = await client.get("/issues")

        assert response.status_code == 403
        assert "Retry-After" not in response.headers

    async def test_rate_limited_requests_are_not_retried(self, client, httpx_mock):
        """No aggressive retry loop: exactly one upstream call is made."""
        httpx_mock.add_response(
            method="GET",
            status_code=429,
            headers={"retry-after": "30"},
        )

        await client.get("/issues")

        assert len(httpx_mock.get_requests()) == 1
