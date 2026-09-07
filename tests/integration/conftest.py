"""Integration-test fixtures.

These tests talk to the real GitHub API using the credentials in your
environment (or ``.env``). They are marked ``integration`` and excluded from the
default ``pytest`` run, and they skip entirely when credentials are absent, so a
normal unit-test run can never create issues in a real repository.

Run them with::

    make test-integration
"""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

REQUIRED_VARS = ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO")

# tests/conftest.py installs placeholder credentials so the app can be imported;
# a real run must override all of them.
PLACEHOLDERS = {
    "GITHUB_TOKEN": "placeholder-token-not-a-real-credential",
    "GITHUB_OWNER": "test-owner",
    "GITHUB_REPO": "test-repo",
}


def _real_credentials() -> dict[str, str] | None:
    """Return the real credentials, or ``None`` if they are not configured."""
    from dotenv import dotenv_values  # provided by pydantic-settings

    from_file = {key: value for key, value in dotenv_values(".env").items() if value}
    values = {}
    for name in REQUIRED_VARS:
        value = os.environ.get(name)
        if not value or value == PLACEHOLDERS[name]:
            value = from_file.get(name)
        if not value or value == PLACEHOLDERS[name]:
            return None
        values[name] = value
    return values


CREDENTIALS = _real_credentials()

#: Applied by the test module. A conftest-level ``pytestmark`` would be ignored,
#: so the skip lives here and is imported where the tests are defined.
requires_credentials = pytest.mark.skipif(
    CREDENTIALS is None,
    reason=(
        "Live GitHub credentials are not configured. Set GITHUB_TOKEN, GITHUB_OWNER "
        "and GITHUB_REPO (in the environment or .env) to run integration tests."
    ),
)


@pytest.fixture(scope="session")
def credentials() -> dict[str, str]:
    assert CREDENTIALS is not None
    return CREDENTIALS


@pytest.fixture(scope="session")
def live_settings(credentials, tmp_path_factory):
    """Settings pointing at the real repository and a throwaway database."""
    from app.config import Settings

    return Settings(
        github_token=credentials["GITHUB_TOKEN"],
        github_owner=credentials["GITHUB_OWNER"],
        github_repo=credentials["GITHUB_REPO"],
        db_path=str(tmp_path_factory.mktemp("integration") / "webhooks.db"),
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_client(live_settings) -> AsyncIterator[httpx.AsyncClient]:
    """Our own API, running in-process against the real GitHub."""
    from app.main import create_app

    app = create_app(live_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def github(live_settings) -> AsyncIterator[httpx.AsyncClient]:
    """A direct GitHub client, used to verify results independently of our API."""
    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        timeout=30.0,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {live_settings.github_token.get_secret_value()}",
        },
    ) as client:
        yield client
