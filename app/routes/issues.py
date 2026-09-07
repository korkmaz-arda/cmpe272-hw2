"""Our CRUD surface over GitHub issues.

Closing an issue (``PATCH`` with ``state: "closed"``) is the Delete half of CRUD;
there is intentionally no ``DELETE`` route.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.deps import get_github_client
from app.github_client import GitHubClient
from app.models import (
    CommentOut,
    CreateCommentRequest,
    CreateIssueRequest,
    ErrorResponse,
    IssueOut,
    IssueStateFilter,
    UpdateIssueRequest,
    comment_from_github,
    issue_from_github,
)
from app.pagination import pagination_headers

router = APIRouter(tags=["issues"])

IssueNumber = Annotated[int, Path(ge=1, description="The GitHub issue number.")]
GitHub = Annotated[GitHubClient, Depends(get_github_client)]

_COMMON_ERRORS: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    401: {"model": ErrorResponse, "description": "GitHub authentication failed"},
    403: {"model": ErrorResponse, "description": "GitHub denied access"},
    429: {"model": ErrorResponse, "description": "GitHub rate limit exceeded"},
    503: {"model": ErrorResponse, "description": "GitHub temporarily unavailable"},
}
_NOT_FOUND: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "Issue not found"}
}


@router.post(
    "/issues",
    status_code=201,
    response_model=IssueOut,
    summary="Create an issue",
    description="Creates an issue in the configured repository. Requires server-side "
    "GitHub authentication.",
    responses=_COMMON_ERRORS,
)
async def create_issue(
    payload: CreateIssueRequest,
    response: Response,
    github: GitHub,
) -> IssueOut:
    issue = issue_from_github(
        await github.create_issue(payload.title, payload.body, payload.labels)
    )
    response.headers["Location"] = f"/issues/{issue.number}"
    return issue


@router.get(
    "/issues",
    response_model=list[IssueOut],
    summary="List issues",
    description=(
        "Lists issues from the configured repository. GitHub's `Link` header is "
        "forwarded unchanged and its entries are not filtered, so pagination "
        "semantics match GitHub's exactly. Requires server-side GitHub authentication."
    ),
    responses=_COMMON_ERRORS,
)
async def list_issues(
    response: Response,
    github: GitHub,
    state: IssueStateFilter = "open",
    labels: Annotated[str | None, Query(description="Comma-separated label names.")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[IssueOut]:
    items, headers = await github.list_issues(
        state=state, labels=labels, page=page, per_page=per_page
    )
    for name, value in pagination_headers(headers.get("link"), page, per_page).items():
        response.headers[name] = value
    return [issue_from_github(item) for item in items]


@router.get(
    "/issues/{number}",
    response_model=IssueOut,
    summary="Get one issue",
    description="Requires server-side GitHub authentication.",
    responses={**_COMMON_ERRORS, **_NOT_FOUND},
)
async def get_issue(number: IssueNumber, github: GitHub) -> IssueOut:
    return issue_from_github(await github.get_issue(number))


@router.patch(
    "/issues/{number}",
    response_model=IssueOut,
    summary="Update, close, or reopen an issue",
    description=(
        "Renames the issue, edits its body, closes it, or reopens it. Closing is the "
        "Delete half of CRUD. Requires server-side GitHub authentication."
    ),
    responses={**_COMMON_ERRORS, **_NOT_FOUND},
)
async def update_issue(
    number: IssueNumber,
    payload: UpdateIssueRequest,
    github: GitHub,
) -> IssueOut:
    # Only fields the caller actually sent are forwarded, so an omitted field is
    # left untouched while an explicit `"body": null` clears the body.
    fields = payload.model_dump(exclude_unset=True)
    return issue_from_github(await github.update_issue(number, fields))


@router.post(
    "/issues/{number}/comments",
    status_code=201,
    response_model=CommentOut,
    summary="Comment on an issue",
    description="Requires server-side GitHub authentication.",
    responses={**_COMMON_ERRORS, **_NOT_FOUND},
)
async def create_comment(
    number: IssueNumber,
    payload: CreateCommentRequest,
    github: GitHub,
) -> CommentOut:
    return comment_from_github(await github.create_comment(number, payload.body))
