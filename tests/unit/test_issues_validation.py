"""Request validation must produce HTTP 400 in our envelope, never FastAPI's 422."""

import pytest


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["message"], str) and payload["error"]["message"]
    return payload["error"]


class TestCreateIssueValidation:
    async def test_missing_title_is_400(self, client):
        assert_error(await client.post("/issues", json={}), 400, "invalid_request")

    async def test_empty_title_is_400(self, client):
        assert_error(await client.post("/issues", json={"title": ""}), 400, "invalid_request")

    async def test_whitespace_only_title_is_400(self, client):
        assert_error(
            await client.post("/issues", json={"title": "   \t\n "}), 400, "invalid_request"
        )

    async def test_null_title_is_400(self, client):
        assert_error(await client.post("/issues", json={"title": None}), 400, "invalid_request")

    async def test_unknown_field_is_400(self, client):
        response = await client.post("/issues", json={"title": "ok", "assignee": "someone"})
        assert_error(response, 400, "invalid_request")

    async def test_wrong_label_type_is_400(self, client):
        response = await client.post("/issues", json={"title": "ok", "labels": "bug"})
        assert_error(response, 400, "invalid_request")

    async def test_error_details_name_the_offending_field(self, client):
        error = assert_error(await client.post("/issues", json={}), 400, "invalid_request")
        locations = [field["loc"] for field in error["details"]["fields"]]
        assert "body.title" in locations


class TestListIssuesValidation:
    @pytest.mark.parametrize("per_page", [0, -1, 101, 1000])
    async def test_per_page_out_of_range_is_400(self, client, per_page):
        response = await client.get("/issues", params={"per_page": per_page})
        assert_error(response, 400, "invalid_request")

    async def test_invalid_state_is_400(self, client):
        assert_error(
            await client.get("/issues", params={"state": "sideways"}), 400, "invalid_request"
        )

    @pytest.mark.parametrize("page", [0, -3])
    async def test_page_below_one_is_400(self, client, page):
        assert_error(await client.get("/issues", params={"page": page}), 400, "invalid_request")


class TestIssueNumberValidation:
    @pytest.mark.parametrize("number", [0, -1])
    async def test_non_positive_number_is_400(self, client, number):
        assert_error(await client.get(f"/issues/{number}"), 400, "invalid_request")

    async def test_non_numeric_number_is_400(self, client):
        assert_error(await client.get("/issues/abc"), 400, "invalid_request")


class TestUpdateIssueValidation:
    async def test_invalid_state_is_400(self, client):
        response = await client.patch("/issues/1", json={"state": "deleted"})
        assert_error(response, 400, "invalid_request")

    async def test_empty_payload_is_400(self, client):
        assert_error(await client.patch("/issues/1", json={}), 400, "invalid_request")

    async def test_unknown_field_is_400(self, client):
        response = await client.patch("/issues/1", json={"milestone": 3})
        assert_error(response, 400, "invalid_request")

    async def test_whitespace_only_title_is_400(self, client):
        response = await client.patch("/issues/1", json={"title": "   "})
        assert_error(response, 400, "invalid_request")

    async def test_null_title_is_400(self, client):
        assert_error(await client.patch("/issues/1", json={"title": None}), 400, "invalid_request")

    async def test_null_state_is_400(self, client):
        assert_error(await client.patch("/issues/1", json={"state": None}), 400, "invalid_request")


class TestCreateCommentValidation:
    async def test_missing_body_is_400(self, client):
        assert_error(await client.post("/issues/1/comments", json={}), 400, "invalid_request")

    async def test_empty_body_is_400(self, client):
        response = await client.post("/issues/1/comments", json={"body": ""})
        assert_error(response, 400, "invalid_request")

    async def test_whitespace_only_body_is_400(self, client):
        response = await client.post("/issues/1/comments", json={"body": "  \n  "})
        assert_error(response, 400, "invalid_request")


async def test_malformed_json_body_is_400(client):
    response = await client.post(
        "/issues", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert_error(response, 400, "invalid_request")


async def test_unknown_route_uses_the_same_envelope(client):
    assert_error(await client.get("/does-not-exist"), 404, "not_found")


async def test_wrong_method_uses_the_same_envelope(client):
    assert_error(await client.delete("/issues/1"), 405, "method_not_allowed")
