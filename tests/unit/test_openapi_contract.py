"""The checked-in openapi.yaml is the contract of record; keep it honest."""

from pathlib import Path

import pytest
import yaml

from app.main import app

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi.yaml"

EXPECTED_OPERATIONS = {
    ("/issues", "post"),
    ("/issues", "get"),
    ("/issues/{number}", "get"),
    ("/issues/{number}", "patch"),
    ("/issues/{number}/comments", "post"),
    ("/webhook", "post"),
    ("/events", "get"),
    ("/healthz", "get"),
}

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text())


def documented_operations(spec: dict) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, item in spec["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def implemented_operations() -> set[tuple[str, str]]:
    """What the running application actually serves, per FastAPI's own schema."""
    generated = app.openapi()
    return {
        (path, method)
        for path, item in generated["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def test_the_file_exists_and_is_openapi_31(spec):
    assert spec["openapi"].startswith("3.1")


def test_every_expected_operation_is_documented(spec):
    assert EXPECTED_OPERATIONS <= documented_operations(spec)


def test_nothing_extra_is_documented(spec):
    assert documented_operations(spec) == EXPECTED_OPERATIONS


def test_the_spec_matches_the_implemented_routes():
    """Guards against the file drifting away from the application."""
    assert implemented_operations() == EXPECTED_OPERATIONS


def test_there_is_no_delete_issue_operation(spec):
    assert "delete" not in spec["paths"]["/issues/{number}"]


def test_there_is_no_public_get_comments_operation(spec):
    assert "get" not in spec["paths"]["/issues/{number}/comments"]


@pytest.mark.parametrize("name", ["Issue", "Comment", "Error"])
def test_the_required_reusable_schemas_exist(spec, name):
    assert name in spec["components"]["schemas"]


@pytest.mark.parametrize(
    "name",
    [
        "CreateIssueRequest",
        "UpdateIssueRequest",
        "CreateCommentRequest",
        "WebhookEvent",
        "HealthResponse",
        "EventRecord",
    ],
)
def test_the_supporting_schemas_exist(spec, name):
    assert name in spec["components"]["schemas"]


class TestSecurityScheme:
    def test_the_scheme_is_declared_as_http_bearer(self, spec):
        scheme = spec["components"]["securitySchemes"]["githubServerAuth"]
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"
        assert "bearerFormat" in scheme

    def test_callers_are_not_asked_to_authenticate(self, spec):
        """Our API needs no caller auth, and must never ask for the GitHub PAT."""
        assert spec["security"] == []
        for path, item in spec["paths"].items():
            for method, operation in item.items():
                if method in HTTP_METHODS:
                    assert "security" not in operation, f"{method.upper()} {path}"

    def test_github_backed_operations_are_marked(self, spec):
        github_backed = {
            ("/issues", "post"),
            ("/issues", "get"),
            ("/issues/{number}", "get"),
            ("/issues/{number}", "patch"),
            ("/issues/{number}/comments", "post"),
        }
        for path, method in github_backed:
            operation = spec["paths"][path][method]
            assert operation["x-upstream-security"] == ["githubServerAuth"]

    def test_local_only_operations_are_not_marked(self, spec):
        for path, method in [("/healthz", "get"), ("/events", "get"), ("/webhook", "post")]:
            assert "x-upstream-security" not in spec["paths"][path][method]


class TestDocumentedResponses:
    def test_create_issue_documents_201_and_the_location_header(self, spec):
        responses = spec["paths"]["/issues"]["post"]["responses"]
        assert "Location" in responses["201"]["headers"]
        assert "examples" in responses["201"]["content"]["application/json"]

    def test_list_issues_documents_the_link_header(self, spec):
        assert "Link" in spec["paths"]["/issues"]["get"]["responses"]["200"]["headers"]

    def test_the_webhook_documents_an_empty_204(self, spec):
        response = spec["paths"]["/webhook"]["post"]["responses"]["204"]
        assert "content" not in response

    def test_the_webhook_documents_its_required_headers(self, spec):
        names = {p["name"] for p in spec["paths"]["/webhook"]["post"]["parameters"]}
        assert names == {"X-Hub-Signature-256", "X-GitHub-Event", "X-GitHub-Delivery"}

    @pytest.mark.parametrize(
        ("path", "method", "status"),
        [
            ("/issues", "post", "400"),
            ("/issues", "post", "401"),
            ("/issues", "post", "429"),
            ("/issues", "post", "503"),
            ("/issues/{number}", "get", "404"),
            ("/webhook", "post", "401"),
            ("/webhook", "post", "400"),
            ("/events", "get", "400"),
        ],
    )
    def test_error_responses_are_documented(self, spec, path, method, status):
        assert status in spec["paths"][path][method]["responses"]


def test_no_secret_values_appear_in_the_spec(spec):
    from tests.conftest import SECRET, TOKEN

    text = SPEC_PATH.read_text()
    assert TOKEN not in text
    assert SECRET not in text
