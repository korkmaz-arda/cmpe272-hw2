"""Request and response models, plus mappers from GitHub's shapes to ours.

The mappers exist so that our API contract is our own: GitHub payloads are never
echoed back to callers verbatim.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

IssueState = Literal["open", "closed"]
IssueStateFilter = Literal["open", "closed", "all"]


# --- requests ---------------------------------------------------------------


class CreateIssueRequest(BaseModel):
    """Body of ``POST /issues``."""

    model_config = ConfigDict(extra="forbid")

    title: NonBlankStr
    body: str | None = None
    labels: list[str] | None = None


class UpdateIssueRequest(BaseModel):
    """Body of ``PATCH /issues/{number}``. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    title: NonBlankStr | None = None
    body: str | None = None
    state: IssueState | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> "UpdateIssueRequest":
        if not self.model_fields_set:
            raise ValueError("Provide at least one of: title, body, state.")
        for field in ("title", "state"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"'{field}' may not be null.")
        return self


class CreateCommentRequest(BaseModel):
    """Body of ``POST /issues/{number}/comments``."""

    model_config = ConfigDict(extra="forbid")

    body: NonBlankStr


# --- responses --------------------------------------------------------------


class IssueOut(BaseModel):
    """An issue, in our shape."""

    number: int = Field(examples=[42])
    html_url: str
    state: str = Field(examples=["open"])
    title: str
    body: str | None = None
    labels: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class CommentOut(BaseModel):
    """A comment, in our shape."""

    id: int
    body: str
    user: str | None = None
    created_at: str
    html_url: str


class EventOut(BaseModel):
    """One stored webhook delivery, as exposed by ``GET /events``."""

    id: str = Field(description="The GitHub delivery id (X-GitHub-Delivery).")
    event: str
    action: str
    issue_number: int | None = None
    timestamp: str


class HealthOut(BaseModel):
    """Body of ``GET /healthz``."""

    status: str = Field(examples=["ok"])


class ErrorDetail(BaseModel):
    code: str = Field(examples=["invalid_request"])
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The single error envelope used by every failure response."""

    error: ErrorDetail


# --- GitHub -> our shape ----------------------------------------------------


def _label_names(raw: Any) -> list[str]:
    names: list[str] = []
    for label in raw or []:
        if isinstance(label, dict):
            name = label.get("name")
            if name:
                names.append(str(name))
        elif isinstance(label, str):
            names.append(label)
    return names


def issue_from_github(payload: dict[str, Any]) -> IssueOut:
    """Map a GitHub issue object onto :class:`IssueOut`."""
    return IssueOut(
        number=int(payload.get("number") or 0),
        html_url=str(payload.get("html_url") or ""),
        state=str(payload.get("state") or ""),
        title=str(payload.get("title") or ""),
        body=payload.get("body"),
        labels=_label_names(payload.get("labels")),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def comment_from_github(payload: dict[str, Any]) -> CommentOut:
    """Map a GitHub issue-comment object onto :class:`CommentOut`."""
    user = payload.get("user")
    login = str(user.get("login")) if isinstance(user, dict) and user.get("login") else None
    return CommentOut(
        id=int(payload.get("id") or 0),
        body=str(payload.get("body") or ""),
        user=login,
        created_at=str(payload.get("created_at") or ""),
        html_url=str(payload.get("html_url") or ""),
    )
