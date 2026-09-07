"""Shared test fixtures.

The five required environment variables are set here, with obvious placeholder
values, *before* the application is imported. Setting real environment variables
also guarantees a developer's local ``.env`` can never leak into a test run,
since environment variables outrank the dotenv file.
"""

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

TEST_ENV = {
    "GITHUB_TOKEN": "placeholder-token-not-a-real-credential",
    "GITHUB_OWNER": "test-owner",
    "GITHUB_REPO": "test-repo",
    "WEBHOOK_SECRET": "placeholder-webhook-secret",
    "PORT": "8000",
    "LOG_LEVEL": "INFO",
    "DB_PATH": "./data/placeholder-tests.db",
}
os.environ.update(TEST_ENV)

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.webhook_security import (  # noqa: E402
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    compute_signature,
)

OWNER = TEST_ENV["GITHUB_OWNER"]
REPO = TEST_ENV["GITHUB_REPO"]
SECRET = TEST_ENV["WEBHOOK_SECRET"]
TOKEN = TEST_ENV["GITHUB_TOKEN"]

GITHUB_HOST = "api.github.com"
ISSUES_URL = f"https://{GITHUB_HOST}/repos/{OWNER}/{REPO}/issues"

FIXTURES = Path(__file__).parent / "fixtures"

#: Let httpx_mock intercept GitHub only, so the in-process ASGI transport that
#: serves our own app keeps working.
mock_github_only = pytest.mark.httpx_mock(
    should_mock=lambda request: request.url.host == GITHUB_HOST
)


def load_fixture(name: str) -> Any:
    """Load a JSON fixture by file name (without the extension)."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def signed_headers(
    body: bytes,
    *,
    event: str = "issues",
    delivery_id: str = "11111111-2222-3333-4444-555555555555",
    secret: str = SECRET,
) -> dict[str, str]:
    """Headers GitHub would send for ``body``."""
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        SIGNATURE_HEADER: compute_signature(body, secret),
    }
    if delivery_id is not None:
        headers[DELIVERY_HEADER] = delivery_id
    return headers


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a throwaway SQLite file."""
    return Settings(db_path=str(tmp_path / "webhooks.db"))  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired to our app, with the lifespan actually running."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http
