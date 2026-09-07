"""GitHub payload -> our shape, including defensive handling of odd payloads."""

from app.errors import _safe_json
from app.models import comment_from_github, issue_from_github
from tests.conftest import ISSUES_URL, load_fixture, mock_github_only

pytestmark = mock_github_only


def test_labels_may_arrive_as_plain_strings():
    issue = issue_from_github({"number": 1, "labels": ["bug", "urgent"]})

    assert issue.labels == ["bug", "urgent"]


def test_labels_may_mix_objects_strings_and_junk():
    issue = issue_from_github(
        {"number": 1, "labels": [{"name": "bug"}, "urgent", {"no_name": True}, 7]}
    )

    assert issue.labels == ["bug", "urgent"]


def test_a_sparse_issue_payload_does_not_raise():
    issue = issue_from_github({})

    assert issue.number == 0
    assert issue.labels == []
    assert issue.body is None


def test_a_comment_without_a_user_object():
    comment = comment_from_github({"id": 1, "body": "hi"})

    assert comment.user is None


def test_a_comment_with_an_empty_user_login():
    comment = comment_from_github({"id": 1, "body": "hi", "user": {"login": ""}})

    assert comment.user is None


def test_safe_json_tolerates_a_non_json_body():
    import httpx

    assert _safe_json(httpx.Response(500, text="<html>oops</html>")) == {}


def test_safe_json_tolerates_a_json_array():
    import httpx

    assert _safe_json(httpx.Response(500, json=[1, 2])) == {}


async def test_list_comments_is_available_to_the_integration_test(client, httpx_mock):
    """Exposed on the client, deliberately not routed."""
    httpx_mock.add_response(
        method="GET", url=f"{ISSUES_URL}/42/comments?per_page=100", json=[load_fixture("comment")]
    )
    github = client._transport.app.state.github

    comments = await github.list_comments(42)

    assert [comment["id"] for comment in comments] == [900001]


async def test_there_is_no_public_route_for_listing_comments(client):
    assert (await client.get("/issues/42/comments")).status_code == 405
