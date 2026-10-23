"""Thin GitHub REST client for the calls the reviewer needs."""

from dataclasses import dataclass
from typing import Self

import httpx

API_VERSION = "2022-11-28"
MAX_ARCHIVE_BYTES = 50_000_000


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    message: str  # first line only
    author: str
    date: str  # ISO 8601


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

    def get_repo_archive(self, repo: str, ref: str, max_bytes: int = MAX_ARCHIVE_BYTES) -> bytes:
        """Download the repo tarball at `ref` in one request (follows the codeload redirect)."""
        chunks: list[bytes] = []
        total = 0
        with self._client.stream(
            "GET", f"/repos/{repo}/tarball/{ref}", follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"repository archive exceeds {max_bytes} bytes")
                chunks.append(chunk)
        return b"".join(chunks)

    def list_commits(self, repo: str, path: str, sha: str, limit: int = 5) -> list[CommitInfo]:
        """Most recent commits that touched `path`, reachable from `sha`."""
        resp = self._client.get(
            f"/repos/{repo}/commits",
            params={"path": path, "sha": sha, "per_page": limit},
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        commits = []
        for item in resp.json():
            commit = item.get("commit", {})
            author = commit.get("author") or {}
            message = (commit.get("message") or "").strip().splitlines()
            commits.append(
                CommitInfo(
                    sha=item.get("sha", ""),
                    message=message[0] if message else "",
                    author=author.get("name", "unknown"),
                    date=author.get("date", ""),
                )
            )
        return commits

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

    # ---- pull request state and comments -------------------------------------------------

    def get_pull_request(self, repo: str, number: int) -> dict:
        resp = self._client.get(
            f"/repos/{repo}/pulls/{number}", headers={"Accept": "application/vnd.github+json"}
        )
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, max_pages: int = 5) -> list[dict]:
        items: list[dict] = []
        for page in range(1, max_pages + 1):
            resp = self._client.get(
                path,
                params={"per_page": 100, "page": page},
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            batch = resp.json()
            items += batch
            if len(batch) < 100:
                break
        return items

    def list_review_comments(self, repo: str, number: int) -> list[dict]:
        return self._get_all(f"/repos/{repo}/pulls/{number}/comments")

    def list_issue_comments(self, repo: str, number: int) -> list[dict]:
        return self._get_all(f"/repos/{repo}/issues/{number}/comments")

    def create_review(
        self,
        repo: str,
        number: int,
        commit_id: str,
        comments: list[dict],
        body: str = "",
        event: str = "COMMENT",
    ) -> dict:
        """Post one review containing inline comments. Raises HTTPStatusError (422) when a
        comment points at a line that is not part of the diff."""
        resp = self._client.post(
            f"/repos/{repo}/pulls/{number}/reviews",
            json={"commit_id": commit_id, "body": body, "event": event, "comments": comments},
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()

    def create_issue_comment(self, repo: str, number: int, body: str) -> dict:
        resp = self._client.post(
            f"/repos/{repo}/issues/{number}/comments",
            json={"body": body},
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()

    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict:
        resp = self._client.patch(
            f"/repos/{repo}/issues/comments/{comment_id}",
            json={"body": body},
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()
