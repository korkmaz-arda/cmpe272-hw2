"""Happy-path behaviour of the issue routes, with GitHub mocked."""

import json

from tests.conftest import ISSUES_URL, TOKEN, load_fixture, mock_github_only

pytestmark = mock_github_only

ISSUE = load_fixture("issue_open")
CLOSED_ISSUE = load_fixture("issue_closed")
PULL_REQUEST = load_fixture("pull_request_entry")
COMMENT = load_fixture("comment")

LINK_HEADER = f'<{ISSUES_URL}?page=2>; rel="next", <{ISSUES_URL}?page=5>; rel="last"'


class TestCreateIssue:
    async def test_returns_201_with_location_header(self, client, httpx_mock):
        httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=201, json=ISSUE)

        response = await client.post("/issues", json={"title": "Something is broken"})

        assert response.status_code == 201
        assert response.headers["Location"] == "/issues/42"

    async def test_maps_github_fields_onto_our_shape(self, client, httpx_mock):
        httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=201, json=ISSUE)

        body = (await client.post("/issues", json={"title": "Something is broken"})).json()

        assert body == {
            "number": 42,
            "html_url": ISSUE["html_url"],
            "state": "open",
            "title": "Something is broken",
            "body": "Steps to reproduce...",
            "labels": ["bug", "help wanted"],
            "created_at": "2026-09-01T10:00:00Z",
            "updated_at": "2026-09-01T10:00:00Z",
        }
        # GitHub-only fields are not leaked into our contract.
        assert "user" not in body

    async def test_forwards_title_body_and_labels(self, client, httpx_mock):
        httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=201, json=ISSUE)

        await client.post(
            "/issues",
            json={"title": "Something is broken", "body": "details", "labels": ["bug"]},
        )

        sent = json.loads(httpx_mock.get_requests()[0].content)
        assert sent == {"title": "Something is broken", "body": "details", "labels": ["bug"]}

    async def test_omits_body_and_labels_when_not_given(self, client, httpx_mock):
        httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=201, json=ISSUE)

        await client.post("/issues", json={"title": "Something is broken"})

        assert json.loads(httpx_mock.get_requests()[0].content) == {"title": "Something is broken"}

    async def test_sends_the_expected_github_headers(self, client, httpx_mock):
        httpx_mock.add_response(method="POST", url=ISSUES_URL, status_code=201, json=ISSUE)

        await client.post("/issues", json={"title": "Something is broken"})

        headers = httpx_mock.get_requests()[0].headers
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"


class TestListIssues:
    async def test_returns_an_array_of_issues(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", json=[ISSUE, CLOSED_ISSUE])

        response = await client.get("/issues")

        assert response.status_code == 200
        body = response.json()
        assert [item["number"] for item in body] == [42, 42]

    async def test_defaults_to_open_state_and_forwards_paging(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", json=[])

        await client.get("/issues")

        params = httpx_mock.get_requests()[0].url.params
        assert params["state"] == "open"
        assert params["page"] == "1"
        assert params["per_page"] == "30"
        assert "labels" not in params

    async def test_forwards_state_labels_and_paging(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", json=[])

        await client.get(
            "/issues", params={"state": "all", "labels": "bug,urgent", "page": 3, "per_page": 100}
        )

        params = httpx_mock.get_requests()[0].url.params
        assert params["state"] == "all"
        assert params["labels"] == "bug,urgent"
        assert params["page"] == "3"
        assert params["per_page"] == "100"

    async def test_propagates_the_link_header_verbatim(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", json=[], headers={"Link": LINK_HEADER})

        response = await client.get("/issues")

        assert response.headers["Link"] == LINK_HEADER
        assert response.headers["X-Next-Page"] == "2"
        assert response.headers["X-Last-Page"] == "5"
        assert response.headers["X-Page"] == "1"
        assert response.headers["X-Per-Page"] == "30"

    async def test_omits_link_when_github_sends_none(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", json=[ISSUE])

        response = await client.get("/issues")

        assert "Link" not in response.headers
        assert "X-Next-Page" not in response.headers

    async def test_pull_requests_are_not_filtered_out(self, client, httpx_mock):
        """GitHub's issues endpoint also returns PRs; dropping them here would
        make page sizes disagree with the Link header we forward."""
        httpx_mock.add_response(
            method="GET", json=[ISSUE, PULL_REQUEST], headers={"Link": LINK_HEADER}
        )

        body = (await client.get("/issues")).json()

        assert len(body) == 2
        assert [item["number"] for item in body] == [42, 43]


class TestGetIssue:
    async def test_returns_one_issue(self, client, httpx_mock):
        httpx_mock.add_response(method="GET", url=f"{ISSUES_URL}/42", json=ISSUE)

        response = await client.get("/issues/42")

        assert response.status_code == 200
        assert response.json()["number"] == 42


class TestUpdateIssue:
    async def test_renames_an_issue(self, client, httpx_mock):
        httpx_mock.add_response(method="PATCH", url=f"{ISSUES_URL}/42", json=ISSUE)

        response = await client.patch("/issues/42", json={"title": "New title"})

        assert response.status_code == 200
        assert json.loads(httpx_mock.get_requests()[0].content) == {"title": "New title"}

    async def test_sends_only_the_fields_the_caller_set(self, client, httpx_mock):
        httpx_mock.add_response(method="PATCH", url=f"{ISSUES_URL}/42", json=ISSUE)

        await client.patch("/issues/42", json={"body": "rewritten"})

        assert json.loads(httpx_mock.get_requests()[0].content) == {"body": "rewritten"}

    async def test_an_explicit_null_body_is_forwarded_to_clear_it(self, client, httpx_mock):
        httpx_mock.add_response(method="PATCH", url=f"{ISSUES_URL}/42", json=ISSUE)

        await client.patch("/issues/42", json={"body": None})

        assert json.loads(httpx_mock.get_requests()[0].content) == {"body": None}

    async def test_closing_an_issue_is_the_delete_operation(self, client, httpx_mock):
        httpx_mock.add_response(method="PATCH", url=f"{ISSUES_URL}/42", json=CLOSED_ISSUE)

        response = await client.patch("/issues/42", json={"state": "closed"})

        assert response.status_code == 200
        assert response.json()["state"] == "closed"
        assert json.loads(httpx_mock.get_requests()[0].content) == {"state": "closed"}

    async def test_reopening_an_issue(self, client, httpx_mock):
        httpx_mock.add_response(method="PATCH", url=f"{ISSUES_URL}/42", json=ISSUE)

        response = await client.patch("/issues/42", json={"state": "open"})

        assert response.json()["state"] == "open"


class TestCreateComment:
    async def test_returns_201_with_our_comment_shape(self, client, httpx_mock):
        httpx_mock.add_response(
            method="POST", url=f"{ISSUES_URL}/42/comments", status_code=201, json=COMMENT
        )

        response = await client.post("/issues/42/comments", json={"body": "Thanks for the report."})

        assert response.status_code == 201
        assert response.json() == {
            "id": 900001,
            "body": "Thanks for the report.",
            "user": "test-owner",
            "created_at": "2026-09-01T11:00:00Z",
            "html_url": COMMENT["html_url"],
        }

    async def test_forwards_the_comment_body(self, client, httpx_mock):
        httpx_mock.add_response(
            method="POST", url=f"{ISSUES_URL}/42/comments", status_code=201, json=COMMENT
        )

        await client.post("/issues/42/comments", json={"body": "Thanks for the report."})

        assert json.loads(httpx_mock.get_requests()[0].content) == {
            "body": "Thanks for the report."
        }
