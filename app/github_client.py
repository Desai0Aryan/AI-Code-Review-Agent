"""Thin GitHub REST client for the calls the reviewer needs."""

from typing import Self

import httpx

API_VERSION = "2022-11-28"


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.api_url,
            transport=transport,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_pull_request_diff(self, repo: str, number: int) -> str:
        resp = self._client.get(
            f"/repos/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        resp.raise_for_status()
        return resp.text

    def get_file_text(self, repo: str, path: str, ref: str) -> str | None:
        """Raw file contents at a ref, or None if it doesn't exist there."""
        resp = self._client.get(
            f"/repos/{repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
