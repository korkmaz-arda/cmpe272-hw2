"""The full CRUD + comment flow against the real configured GitHub repository.

Marked ``integration`` and skipped without credentials. The steps run in order
within one session, mirroring the assignment's required flow:

1. create an issue through our API              -> 201
2. read it back through our API                 -> 200
3. update the title                             -> 200
4. update the body                              -> 200
5. close it (our Delete operation)              -> 200
6. reopen it                                    -> 200
7. create a comment through our API             -> 201
8. fetch the comments list **from GitHub** and verify the comment exists

Step 8 goes straight to GitHub on purpose: the required public API defines only
``POST`` for comments, so the verification does not need a route we invented.
"""

import uuid
from typing import Any

import pytest
import pytest_asyncio

from tests.integration.conftest import requires_credentials

# The session-scoped fixtures (`live_client`, `github`) build their httpx clients
# on the session event loop, and httpx keeps pooled TCP/TLS connections bound to
# the loop that opened them. pytest-asyncio's default test loop scope is
# "function", so without this marker every test would run on a *new* loop and
# reuse connections belonging to an already-closed one
# (RuntimeError: Event loop is closed). Running the tests on the same session
# loop as the fixtures keeps one client, one loop, one connection pool.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    requires_credentials,
]

RUN_ID = uuid.uuid4().hex[:8]
ORIGINAL_TITLE = f"[hw2-itest {RUN_ID}] created by the integration test"
RENAMED_TITLE = f"[hw2-itest {RUN_ID}] renamed by the integration test"
ORIGINAL_BODY = "Created by the CMPE272 HW2 integration test. Safe to close."
UPDATED_BODY = "Body updated by the CMPE272 HW2 integration test."
COMMENT_BODY = f"Comment from the CMPE272 HW2 integration test ({RUN_ID})."

#: Carries state between the ordered steps below.
state: dict[str, Any] = {}


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _cleanup(github, live_settings):
    """Close the test issue afterwards so the repository stays tidy."""
    yield
    number = state.get("number")
    if number is None:
        return
    await github.patch(
        f"/repos/{live_settings.repo_full_name}/issues/{number}", json={"state": "closed"}
    )


async def test_1_create_issue_returns_201_with_location(live_client):
    response = await live_client.post(
        "/issues", json={"title": ORIGINAL_TITLE, "body": ORIGINAL_BODY, "labels": []}
    )

    assert response.status_code == 201, response.text
    issue = response.json()
    state["number"] = issue["number"]

    assert response.headers["Location"] == f"/issues/{issue['number']}"
    assert issue["title"] == ORIGINAL_TITLE
    assert issue["state"] == "open"
    assert issue["html_url"].startswith("https://github.com/")


async def test_2_get_issue_returns_200(live_client):
    number = state["number"]

    response = await live_client.get(f"/issues/{number}")

    assert response.status_code == 200, response.text
    assert response.json()["number"] == number
    assert response.json()["title"] == ORIGINAL_TITLE


async def test_3_update_title(live_client):
    response = await live_client.patch(f"/issues/{state['number']}", json={"title": RENAMED_TITLE})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == RENAMED_TITLE


async def test_4_update_body(live_client):
    response = await live_client.patch(f"/issues/{state['number']}", json={"body": UPDATED_BODY})

    assert response.status_code == 200, response.text
    assert response.json()["body"] == UPDATED_BODY


async def test_5_close_issue(live_client):
    """Closing is this API's Delete operation."""
    response = await live_client.patch(f"/issues/{state['number']}", json={"state": "closed"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "closed"


async def test_6_reopen_issue(live_client):
    response = await live_client.patch(f"/issues/{state['number']}", json={"state": "open"})

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "open"


async def test_7_create_comment_returns_201(live_client):
    response = await live_client.post(
        f"/issues/{state['number']}/comments", json={"body": COMMENT_BODY}
    )

    assert response.status_code == 201, response.text
    comment = response.json()
    state["comment_id"] = comment["id"]

    assert comment["body"] == COMMENT_BODY
    assert comment["user"]
    assert comment["html_url"].startswith("https://github.com/")


async def test_8_comment_exists_when_fetched_directly_from_github(github, live_settings):
    """Verification against GitHub itself, not through a route we invented."""
    response = await github.get(
        f"/repos/{live_settings.repo_full_name}/issues/{state['number']}/comments",
        params={"per_page": 100},
    )

    assert response.status_code == 200, response.text
    comments = response.json()

    assert state["comment_id"] in [comment["id"] for comment in comments]
    assert COMMENT_BODY in [comment["body"] for comment in comments]


async def test_9_the_issue_appears_in_our_list_endpoint(live_client):
    response = await live_client.get("/issues", params={"state": "open", "per_page": 100})

    assert response.status_code == 200, response.text
    assert state["number"] in [issue["number"] for issue in response.json()]


async def test_10_a_missing_issue_returns_404(live_client):
    response = await live_client.get("/issues/999999999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
