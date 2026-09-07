"""Unit tests for HMAC-SHA256 signature verification."""

import pytest

from app.webhook_security import compute_signature, verify_signature

SECRET = "unit-test-secret"
BODY = b'{"action": "opened", "issue": {"number": 1}}'


def test_valid_signature_is_accepted():
    assert verify_signature(BODY, compute_signature(BODY, SECRET), SECRET) is True


def test_invalid_signature_is_rejected():
    assert verify_signature(BODY, "sha256=" + "0" * 64, SECRET) is False


def test_payload_changed_after_signing_is_rejected():
    signature = compute_signature(BODY, SECRET)
    tampered = BODY.replace(b'"number": 1', b'"number": 2')
    assert tampered != BODY
    assert verify_signature(tampered, signature, SECRET) is False


def test_signature_from_a_different_secret_is_rejected():
    assert verify_signature(BODY, compute_signature(BODY, "other-secret"), SECRET) is False


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha1=" + "a" * 40,  # wrong algorithm prefix
        "a" * 64,  # no prefix at all
        "sha256=",  # prefix with no digest
        "sha256=not-hex-at-all",
        "sha256=é" * 10,  # non-ASCII
    ],
)
def test_missing_or_malformed_headers_are_rejected(header):
    assert verify_signature(BODY, header, SECRET) is False


def test_empty_body_with_a_valid_signature_is_accepted():
    assert verify_signature(b"", compute_signature(b"", SECRET), SECRET) is True


def test_unicode_body_signs_and_verifies():
    body = '{"title": "café ☕"}'.encode()
    assert verify_signature(body, compute_signature(body, SECRET), SECRET) is True


def test_signature_has_the_github_prefix_and_length():
    signature = compute_signature(BODY, SECRET)
    assert signature.startswith("sha256=")
    assert len(signature) == len("sha256=") + 64
