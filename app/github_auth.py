"""GitHub App authentication: app JWT -> installation access token."""

import time
from dataclasses import dataclass

import httpx
import jwt


def create_app_jwt(app_id: str, private_key_pem: str, now: int | None = None) -> str:
    """Create the short-lived JWT that identifies the GitHub App itself."""
    issued = int(now if now is not None else time.time())
    payload = {
        "iat": issued - 60,  # allow for clock drift
        "exp": issued + 9 * 60,  # GitHub max is 10 minutes
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


@dataclass
class InstallationToken:
    token: str
    expires_at: float  # epoch seconds

    def is_fresh(self, skew: int = 60) -> bool:
        return time.time() < self.expires_at - skew


class GitHubAuth:
    """Mints and caches installation tokens, one per installation id."""

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ):
        self.app_id = app_id
        self.private_key_pem = private_key_pem
        self.api_url = api_url.rstrip("/")
        self._transport = transport
        self._cache: dict[int, InstallationToken] = {}

    def get_installation_token(self, installation_id: int) -> str:
        cached = self._cache.get(installation_id)
        if cached and cached.is_fresh():
            return cached.token

        app_jwt = create_app_jwt(self.app_id, self.private_key_pem)
        with httpx.Client(transport=self._transport, timeout=15) as client:
            resp = client.post(
                f"{self.api_url}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        resp.raise_for_status()
        data = resp.json()
        expires_at = _parse_iso8601(data["expires_at"])
        self._cache[installation_id] = InstallationToken(data["token"], expires_at)
        return data["token"]


def _parse_iso8601(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()
