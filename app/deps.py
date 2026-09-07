"""Tiny FastAPI dependencies.

Everything the routes need is created once during the application lifespan and
parked on ``app.state``; these two functions are the whole dependency-injection
story.
"""

from fastapi import Request

from app.github_client import GitHubClient


def get_github_client(request: Request) -> GitHubClient:
    """The shared, authenticated GitHub client."""
    return request.app.state.github


def get_db_path(request: Request) -> str:
    """Path to the SQLite database holding webhook deliveries."""
    return request.app.state.db_path
