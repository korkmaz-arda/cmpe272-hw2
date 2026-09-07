"""The one place that talks to the GitHub REST API.

Route handlers never touch ``httpx`` directly: they call methods here, which
either return parsed JSON or raise an :class:`~app.errors.AppError` that is
already mapped to the status code we intend to return.
"""

import logging
from typing import Any

import httpx

from app.config import Settings
from app.errors import GITHUB_UNAVAILABLE_MESSAGE, AppError, map_github_response

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "cmpe272-hw2"
REQUEST_TIMEOUT_SECONDS = 10.0


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """Create the shared, authenticated client used for the process lifetime."""
    return httpx.AsyncClient(
        base_url=GITHUB_API_BASE,
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
        },
    )


class GitHubClient:
    """Typed access to the Issues endpoints of one configured repository."""

    def __init__(self, client: httpx.AsyncClient, owner: str, repo: str) -> None:
        self._client = client
        self.owner = owner
        self.repo = repo
        self._base = f"/repos/{owner}/{repo}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform one GitHub call, raising ``AppError`` for anything but 2xx."""
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            logger.warning("github request timed out", extra={"method": method, "path": path})
            raise AppError(503, "github_unavailable", "Timed out talking to GitHub.") from exc
        except httpx.HTTPError as exc:
            logger.warning("github request failed", extra={"method": method, "path": path})
            raise AppError(503, "github_unavailable", GITHUB_UNAVAILABLE_MESSAGE) from exc

        logger.debug(
            "github request",
            extra={
                "method": method,
                "path": path,
                "status": response.status_code,
                "rate_remaining": response.headers.get("x-ratelimit-remaining"),
            },
        )

        if response.status_code >= 400:
            # No automatic retry: we surface 429/503 and let the caller decide.
            raise map_github_response(response)
        return response

    @staticmethod
    def _as_dict(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def create_issue(
        self,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if body is not None:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        response = await self._request("POST", f"{self._base}/issues", json=payload)
        return self._as_dict(response)

    async def list_issues(
        self,
        *,
        state: str = "open",
        labels: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> tuple[list[dict[str, Any]], httpx.Headers]:
        """Return the page of issues plus GitHub's response headers.

        The headers are returned so the route can forward the ``Link`` header
        untouched; entries are not filtered, so page sizes stay consistent with
        the pagination GitHub described.
        """
        params: dict[str, Any] = {"state": state, "page": page, "per_page": per_page}
        if labels:
            params["labels"] = labels
        response = await self._request("GET", f"{self._base}/issues", params=params)
        payload = response.json()
        items = payload if isinstance(payload, list) else []
        return items, response.headers

    async def get_issue(self, number: int) -> dict[str, Any]:
        response = await self._request("GET", f"{self._base}/issues/{number}")
        return self._as_dict(response)

    async def update_issue(self, number: int, fields: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("PATCH", f"{self._base}/issues/{number}", json=fields)
        return self._as_dict(response)

    async def create_comment(self, number: int, body: str) -> dict[str, Any]:
        response = await self._request(
            "POST", f"{self._base}/issues/{number}/comments", json={"body": body}
        )
        return self._as_dict(response)

    async def list_comments(self, number: int, per_page: int = 100) -> list[dict[str, Any]]:
        """Fetch an issue's comments.

        Used only by the integration test to verify a created comment against
        GitHub itself; it is deliberately not exposed as a public route.
        """
        response = await self._request(
            "GET", f"{self._base}/issues/{number}/comments", params={"per_page": per_page}
        )
        payload = response.json()
        return payload if isinstance(payload, list) else []
