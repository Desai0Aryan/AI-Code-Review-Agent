"""Post a review to the pull request: inline comments where possible, plus one summary comment.

Safety rules:
  * never post for a commit that is no longer the PR head (the review is stale),
  * inline comments only on lines GitHub will accept (added/context lines in the diff),
  * never post the same finding twice (hidden marker in each comment),
  * the summary is a single comment that is updated in place on later pushes.
"""

import logging
import re
from dataclasses import dataclass, field

import httpx

from app.diff import FileDiff
from app.fixer import FixResult
from app.render import render_inline, render_markdown
from app.review_models import ReviewFinding, ReviewResult

logger = logging.getLogger("code_review_agent")

SUMMARY_MARKER = "<!-- cra:summary -->"
_FINDING_MARKER = re.compile(r"<!-- cra:finding:([0-9a-f]+) -->")


@dataclass
class PublishOutcome:
    posted_inline: list[str] = field(default_factory=list)  # finding ids
    skipped_duplicate: list[str] = field(default_factory=list)
    not_commentable: list[str] = field(default_factory=list)
    summary_action: str = "none"  # created | updated | none
    stale: bool = False


def _marker(finding_id: str) -> str:
    return f"<!-- cra:finding:{finding_id} -->"


def _comment_for(f: ReviewFinding, fix: FixResult | None, commentable: set[int]) -> dict | None:
    if f.line not in commentable:
        return None
    start, end = f.line, f.end_line or f.line
    use_fix = fix is not None and fix.status in ("verified", "no-regression")
    if use_fix:
        # a suggestion replaces exactly the commented range, so the range must match the fix
        start, end = fix.start_line, fix.end_line
        if not all(n in commentable for n in range(start, end + 1)):
            use_fix = False
    if not use_fix:
        start, end = f.line, f.line
    body = render_inline(f, fix, suggestion=use_fix) + "\n\n" + _marker(f.id)
    comment = {"path": f.path, "line": end, "side": "RIGHT", "body": body}
    if end > start:
        comment["start_line"] = start
        comment["start_side"] = "RIGHT"
    return comment


def publish_review(
    client,
    repo: str,
    number: int,
    head_sha: str,
    result: ReviewResult,
    fixes: dict[str, FixResult],
    diffs: list[FileDiff],
    *,
    post_clean_summary: bool = True,
    max_inline: int = 10,
) -> PublishOutcome:
    out = PublishOutcome()
    current = client.get_pull_request(repo, number)["head"]["sha"]
    if current != head_sha:
        logger.info(
            "review for %s#%d is stale (head moved to %s); not posting", repo, number, current[:7]
        )
        out.stale = True
        return out

    commentable = {d.path: d.commentable_lines for d in diffs}
    existing_inline = {
        m for c in client.list_review_comments(repo, number)
        for m in _FINDING_MARKER.findall(c.get("body", ""))
    }  # fmt: skip

    comments: list[tuple[ReviewFinding, dict]] = []
    for f in result.findings:
        if f.status == "uncertain":
            continue  # questions live in the summary, not as inline noise
        if f.id in existing_inline:
            out.skipped_duplicate.append(f.id)
            continue
        c = _comment_for(f, fixes.get(f.id), commentable.get(f.path, set()))
        if c is None:
            out.not_commentable.append(f.id)
        elif len(comments) < max_inline:
            comments.append((f, c))

    if comments:
        try:
            client.create_review(repo, number, head_sha, [c for _, c in comments])
            out.posted_inline = [f.id for f, _ in comments]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 422:
                raise
            # One bad anchor rejects the whole review; retry one by one and keep what works.
            for f, c in comments:
                try:
                    client.create_review(repo, number, head_sha, [c])
                    out.posted_inline.append(f.id)
                except httpx.HTTPStatusError:
                    out.not_commentable.append(f.id)

    has_content = bool(result.findings) or bool(result.rejected)
    if has_content or post_clean_summary:
        body = SUMMARY_MARKER + "\n" + render_markdown(result, fixes)
        existing = next(
            (
                c
                for c in client.list_issue_comments(repo, number)
                if SUMMARY_MARKER in c.get("body", "")
            ),
            None,
        )
        if existing:
            client.update_issue_comment(repo, existing["id"], body)
            out.summary_action = "updated"
        else:
            client.create_issue_comment(repo, number, body)
            out.summary_action = "created"
    return out
