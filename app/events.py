"""Typed view of the GitHub webhook payloads we care about."""

from dataclasses import dataclass

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
