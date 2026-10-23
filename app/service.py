"""The whole review, end to end: fetch -> parse -> context -> analyze -> review -> fix -> post."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from app.analysis import AnalysisReport, run_analysis
from app.baseline import mark_pre_existing
from app.config import Settings
from app.events import PullRequestEvent
from app.fixer import FixBatch, fix_findings
from app.github_auth import GitHubAuth
from app.github_client import GitHubClient
from app.index import RepoIndex, extract_python_files
from app.llm import get_llm
from app.pipeline import build_changeset, build_context
from app.publisher import PublishOutcome, publish_review
from app.render import render_markdown
from app.review_models import ReviewResult
from app.reviewer import review_changes
from app.sandbox import Sandbox, get_sandbox
from app.store import Store

logger = logging.getLogger("code_review_agent")

_DEFAULT = object()


@dataclass
class ReviewOutcome:
    status: str  # reviewed | nothing-to-review | stale | error
    review: ReviewResult = field(default_factory=ReviewResult)
    fixes: FixBatch = field(default_factory=FixBatch)
    report: AnalysisReport = field(default_factory=AnalysisReport)
    published: PublishOutcome | None = None
    markdown: str = ""
    error: str | None = None


class ReviewService:
    def __init__(
        self,
        settings: Settings,
        *,
        github_factory: Callable[[PullRequestEvent], AbstractContextManager[GitHubClient]]
        | None = None,
        llm=_DEFAULT,
        sandbox=_DEFAULT,
        store: Store | None = None,
    ):
        self.settings = settings
        self._github_factory = github_factory or self._default_github
        self._llm = get_llm(settings) if llm is _DEFAULT else llm
        self._sandbox: Sandbox | None | object = sandbox
        self.store = store

    def _default_github(self, event: PullRequestEvent) -> AbstractContextManager[GitHubClient]:
        s = self.settings
        token = GitHubAuth(
            s.github_app_id, s.private_key_pem, s.github_api_url
        ).get_installation_token(event.installation_id)
        return GitHubClient(token, s.github_api_url)

    def run(self, event: PullRequestEvent, *, publish: bool = True) -> ReviewOutcome:
        try:
            outcome = self._run(event, publish and not self.settings.dry_run)
        except Exception as exc:
            logger.exception("review failed repo=%s pr=%s", event.repo_full_name, event.pr_number)
            outcome = ReviewOutcome(status="error", error=f"{type(exc).__name__}: {exc}")
        self._record(event, outcome)
        return outcome

    def _record(self, event: PullRequestEvent, o: ReviewOutcome) -> None:
        if self.store is None:
            return
        try:
            self.store.record(
                event.repo_full_name, event.pr_number, event.head_sha, o.status,
                {
                    "confirmed": len(o.review.with_status("confirmed")),
                    "likely": len(o.review.with_status("likely")),
                    "uncertain": len(o.review.with_status("uncertain")),
                    "rejected_claims": len(o.review.rejected),
                    "fixes": {k: v.status for k, v in o.fixes.fixes.items()},
                    "tokens_in": o.review.input_tokens + o.fixes.input_tokens,
                    "tokens_out": o.review.output_tokens + o.fixes.output_tokens,
                    "error": o.error,
                },
            )  # fmt: skip
        except Exception:
            logger.exception("could not record review")

    def _get_sandbox(self) -> Sandbox | None:
        if self._sandbox is _DEFAULT:
            self._sandbox = get_sandbox(self.settings)
        return self._sandbox  # type: ignore[return-value]

    def _run(self, event: PullRequestEvent, publish: bool) -> ReviewOutcome:
        s = self.settings
        repo, number = event.repo_full_name, event.pr_number
        logger.info("review start %s#%d head=%s", repo, number, event.head_sha[:7])
        with self._github_factory(event) as client:
            if client.get_pull_request(repo, number)["head"]["sha"] != event.head_sha:
                logger.info("skipping %s#%d: a newer commit exists", repo, number)
                return ReviewOutcome(status="stale")

            changeset = build_changeset(client, event)
            if not changeset.changed_units:
                logger.info("no changed Python functions in %s#%d", repo, number)
                return ReviewOutcome(status="nothing-to-review")

            archive = client.get_repo_archive(repo, event.head_sha)
            index = RepoIndex(extract_python_files(archive))
            contexts = build_context(client, event, changeset, archive=archive, index=index)

            report = AnalysisReport()
            sandbox = self._get_sandbox()
            if sandbox is None:
                report.notes.append("sandbox unavailable: static analysis and tests were skipped")
            else:
                try:
                    report = run_analysis(
                        archive, changeset, contexts, sandbox,
                        analysis_timeout=s.analysis_timeout, test_timeout=s.test_timeout,
                    )  # fmt: skip
                    if any(f.tool == "pytest" for f in report.findings):
                        base = client.get_repo_archive(repo, event.base_sha)
                        n = mark_pre_existing(report, base, sandbox, s.test_timeout)
                        if n:
                            logger.info("%d failing test(s) also fail on the base commit", n)
                except Exception:
                    logger.exception("analysis failed")
                    report.notes.append("analysis failed unexpectedly; see server logs")

            review = review_changes(
                changeset, contexts, report, index.files, self._llm,
                min_confidence=s.min_confidence, max_units=s.max_review_units,
                max_tokens=s.llm_max_tokens,
            )  # fmt: skip
            for note in report.notes:
                if note not in review.notes:
                    review.notes.append(note)

            fixes = FixBatch()
            if s.enable_fixes and sandbox is not None and review.findings:
                try:
                    fixes = fix_findings(
                        review.findings, archive=archive, files=index.files, report=report,
                        llm=self._llm, sandbox=sandbox, max_fixes=s.max_fixes,
                        test_timeout=s.test_timeout, analysis_timeout=s.analysis_timeout,
                    )  # fmt: skip
                except Exception:
                    logger.exception("fix validation failed")
                    review.notes.append("fix validation failed unexpectedly")

            markdown = render_markdown(review, fixes.fixes)
            outcome = ReviewOutcome(
                status="reviewed", review=review, fixes=fixes, report=report, markdown=markdown
            )
            logger.info(
                "review %s#%d confirmed=%d likely=%d uncertain=%d rejected_claims=%d",
                repo, number, len(review.with_status("confirmed")), len(review.with_status("likely")),
                len(review.with_status("uncertain")), len(review.rejected),
            )  # fmt: skip
            if publish:
                outcome.published = publish_review(
                    client,
                    repo,
                    number,
                    event.head_sha,
                    review,
                    fixes.fixes,
                    changeset.diffs,
                    post_clean_summary=s.post_clean_summary,
                    max_inline=s.max_inline_comments,
                )
                if outcome.published.stale:
                    outcome.status = "stale"
            else:
                logger.info("not publishing (dry run). Markdown:\n%s", markdown)
            return outcome
