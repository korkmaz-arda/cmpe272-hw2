"""Unit tests for environment configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from tests.conftest import TEST_ENV

REQUIRED = ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "WEBHOOK_SECRET", "PORT")


def test_loads_from_the_environment():
    settings = Settings()
    assert settings.github_owner == TEST_ENV["GITHUB_OWNER"]
    assert settings.port == int(TEST_ENV["PORT"])
    assert settings.repo_full_name == f"{TEST_ENV['GITHUB_OWNER']}/{TEST_ENV['GITHUB_REPO']}"


@pytest.mark.parametrize("name", REQUIRED)
def test_every_required_variable_is_required(name, monkeypatch, tmp_path):
    """Notably PORT, which deliberately has no application-level default."""
    monkeypatch.delenv(name, raising=False)
    # Ignore any developer .env so the variable is genuinely absent.
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert name.lower() in str(excinfo.value).lower()


def test_optional_variables_have_defaults(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.db_path == "./data/webhooks.db"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("port", ["0", "70000", "not-a-number"])
def test_invalid_port_is_rejected(port, monkeypatch):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("name", ["GITHUB_OWNER", "GITHUB_REPO"])
def test_blank_owner_or_repo_is_rejected(name, monkeypatch):
    monkeypatch.setenv(name, "   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_log_level_is_rejected(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_are_not_exposed_by_repr_or_str():
    settings = Settings()
    for rendered in (repr(settings), str(settings)):
        assert TEST_ENV["GITHUB_TOKEN"] not in rendered
        assert TEST_ENV["WEBHOOK_SECRET"] not in rendered
    # ...but are still available where the code needs them.
    assert settings.github_token.get_secret_value() == TEST_ENV["GITHUB_TOKEN"]


def test_get_settings_is_cached():
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
