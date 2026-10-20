"""Review pipeline. covers: fetch diff -> parse -> map changes to code units."""

import logging

from app.changes import ChangeSet, map_changes
from app.diff import parse_unified_diff
from app.events import PullRequestEvent
from app.github_client import GitHubClient
from app.parsing import language_for

logger = logging.getLogger("code_review_agent")

MAX_FILES = 40
MAX_FILE_BYTES = 300_000


def build_changeset(client: GitHubClient, event: PullRequestEvent) -> ChangeSet:
    diff_text = client.get_pull_request_diff(event.repo_full_name, event.pr_number)
    diffs = parse_unified_diff(diff_text)

    new_sources: dict[str, str] = {}
    old_sources: dict[str, str] = {}

    candidates = [d for d in diffs if not d.is_binary and not d.is_deleted and language_for(d.path)]
    for fd in candidates[:MAX_FILES]:
        new_text = client.get_file_text(event.repo_full_name, fd.path, event.head_sha)
        if new_text is None or len(new_text) > MAX_FILE_BYTES:
            continue
        new_sources[fd.path] = new_text
        if not fd.is_new and fd.removed_lines:
            old_text = client.get_file_text(
                event.repo_full_name, fd.old_path or fd.path, event.base_sha
            )
            if old_text is not None:
                old_sources[fd.path] = old_text

    changeset = map_changes(diffs, new_sources, old_sources)
    for path, reason in changeset.skipped.items():
        logger.info("skipped %s: %s", path, reason)
    return changeset
