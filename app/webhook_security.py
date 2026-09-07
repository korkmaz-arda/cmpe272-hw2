"""HMAC-SHA256 verification of GitHub webhook deliveries.

The digest must be computed over the exact bytes GitHub sent, so callers pass the
raw request body here before any JSON parsing. Nothing in this module logs.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

_PREFIX = "sha256="


def compute_signature(raw_body: bytes, secret: str) -> str:
    """The ``sha256=...`` signature GitHub would send for this body."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify_signature(raw_body: bytes, header_value: str | None, secret: str) -> bool:
    """Constant-time check of a delivery's signature header."""
    if not header_value or not isinstance(header_value, str):
        return False
    if not header_value.startswith(_PREFIX):
        return False
    try:
        return hmac.compare_digest(header_value, compute_signature(raw_body, secret))
    except TypeError:
        # Non-ASCII in the header: not a signature we could have produced.
        return False
