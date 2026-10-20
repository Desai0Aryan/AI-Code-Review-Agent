"""Review pipeline. Part 2 covers: fetch diff -> parse -> map changes to code units."""

import logging

import httpx

from app.changes import ChangeSet, map_changes
from app.context import UnitContext, build_unit_context
from app.diff import parse_unified_diff
from app.events import PullRequestEvent
from app.github_client import CommitInfo, GitHubClient
from app.index import RepoIndex, extract_python_files
from app.parsing import language_for

logger = logging.getLogger("code_review_agent")

MAX_FILES = 40
MAX_FILE_BYTES = 300_000
MAX_CONTEXT_UNITS = 25


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


def build_context(
    client: GitHubClient, event: PullRequestEvent, changeset: ChangeSet
) -> dict[str, UnitContext]:
    """Gather cross-file context for each changed unit (keyed by ChangedUnit.key)."""
    if not changeset.changed_units:
        return {}

    archive = client.get_repo_archive(event.repo_full_name, event.head_sha)
    index = RepoIndex(extract_python_files(archive))

    history: dict[str, list[CommitInfo]] = {}
    contexts: dict[str, UnitContext] = {}
    for cu in changeset.changed_units[:MAX_CONTEXT_UNITS]:
        if cu.path not in history:
            try:
                history[cu.path] = client.list_commits(event.repo_full_name, cu.path, event.head_sha)
            except httpx.HTTPError:
                history[cu.path] = []  # history is best-effort; never fail the review over it
        contexts[cu.key] = build_unit_context(index, cu, history[cu.path])

    skipped = len(changeset.changed_units) - len(contexts)
    if skipped > 0:
        logger.info("context skipped for %d units (limit %d)", skipped, MAX_CONTEXT_UNITS)
    return contexts
