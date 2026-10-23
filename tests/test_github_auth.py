import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from app.github_auth import GitHubAuth, create_app_jwt


def test_app_jwt_claims_and_signature(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    token = create_app_jwt("42", private_pem, now=1_700_000_000)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "42"
    assert claims["exp"] - claims["iat"] <= 10 * 60


def _expiry(minutes: int) -> str:
    dt = datetime.now(UTC) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_installation_token_is_minted_and_cached(rsa_keypair):
    private_pem, _ = rsa_keypair
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/app/installations/7/access_tokens"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(201, json={"token": "ghs_abc", "expires_at": _expiry(60)})

    auth = GitHubAuth("42", private_pem, transport=httpx.MockTransport(handler))
    assert auth.get_installation_token(7) == "ghs_abc"
    assert auth.get_installation_token(7) == "ghs_abc"
    assert len(calls) == 1  # second call served from cache


def test_expired_token_is_refreshed(rsa_keypair):
    private_pem, _ = rsa_keypair
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(201, json={"token": f"tok{counter['n']}", "expires_at": _expiry(0)})

    auth = GitHubAuth("42", private_pem, transport=httpx.MockTransport(handler))
    assert auth.get_installation_token(1) == "tok1"
    time.sleep(0.01)
    assert auth.get_installation_token(1) == "tok2"
