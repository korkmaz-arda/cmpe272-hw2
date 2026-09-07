"""Environment-based configuration.

The five variables named by the assignment are all required and deliberately have
no application-level defaults: a missing one fails fast at startup rather than
letting the service run against a surprising target. Two optional, non-secret
knobs (``DB_PATH``, ``LOG_LEVEL``) carry sensible defaults.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the environment (or a local ``.env``)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- required -----------------------------------------------------------
    github_token: SecretStr
    github_owner: str
    github_repo: str
    webhook_secret: SecretStr
    port: int = Field(ge=1, le=65535)

    # --- optional -----------------------------------------------------------
    db_path: str = "./data/webhooks.db"
    log_level: str = "INFO"

    @field_validator("github_owner", "github_repo", "db_path")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError("must be a valid logging level")
        return level

    @property
    def repo_full_name(self) -> str:
        """``owner/repo``, as GitHub spells it."""
        return f"{self.github_owner}/{self.github_repo}"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment once."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
