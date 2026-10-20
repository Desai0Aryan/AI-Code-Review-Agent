"""Webhook signature verification (GitHub's X-Hub-Signature-256)."""

import hashlib
import hmac


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of the signature GitHub sent with the payload.

    Returns False if the secret is unset, the header is missing, or it doesn't match.
    """
    if not secret or not signature_header:
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature_header)
