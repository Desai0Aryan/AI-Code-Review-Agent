"""Typed view of the GitHub webhook payloads we care about."""

import re
from dataclasses import dataclass

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@dataclass(frozen=True)
class PullRequestEvent:
    installation_id: int
    repo_full_name: str
    pr_number: int
    head_sha: str
    base_sha: str
    action: str
    draft: bool

    @classmethod
    def from_payload(cls, payload: dict) -> "PullRequestEvent":
        pr = payload["pull_request"]
        repo = payload["repository"]["full_name"]
        if not isinstance(repo, str) or not REPO_NAME_RE.match(repo) or ".." in repo:
            raise ValueError(f"unexpected repository name: {repo!r}")
        return cls(
            installation_id=payload["installation"]["id"],
            repo_full_name=payload["repository"]["full_name"],
            pr_number=pr["number"],
            head_sha=pr["head"]["sha"],
            base_sha=pr["base"]["sha"],
            action=payload["action"],
            draft=bool(pr.get("draft", False)),
        )

    @property
    def should_review(self) -> bool:
        return self.action in REVIEWABLE_ACTIONS and not self.draft
