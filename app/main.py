"""FastAPI entrypoint: receives GitHub webhooks and queues PR reviews."""

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.analysis import AnalysisReport, run_analysis
from app.config import get_settings
from app.events import PullRequestEvent
from app.github_auth import GitHubAuth
from app.github_client import GitHubClient
from app.pipeline import build_changeset, build_context
from app.sandbox import get_sandbox
from app.security import verify_signature

logger = logging.getLogger("code_review_agent")

app = FastAPI(title="AI Code Review Agent", version="0.1.0")


def review_pull_request(event: PullRequestEvent) -> None:
    """Review pipeline entrypoint.

    Part 2: fetch diff -> parse -> map changes to functions. Later parts add context
    retrieval, static analysis, LLM review, validated fixes and PR comments.
    """
    logger.info(
        "queued review repo=%s pr=%s head=%s",
        event.repo_full_name,
        event.pr_number,
        event.head_sha[:7],
    )
    settings = get_settings()
    archive = None
    try:
        auth = GitHubAuth(settings.github_app_id, settings.private_key_pem, settings.github_api_url)
        token = auth.get_installation_token(event.installation_id)
        with GitHubClient(token, settings.github_api_url) as client:
            changeset = build_changeset(client, event)
            if changeset.changed_units:
                archive = client.get_repo_archive(event.repo_full_name, event.head_sha)
            contexts = build_context(client, event, changeset, archive=archive)
    except Exception:
        logger.exception("review failed repo=%s pr=%s", event.repo_full_name, event.pr_number)
        return

    for cu in changeset.changed_units:
        ctx = contexts.get(cu.key)
        logger.info(
            "changed unit %s lines=%s removed=%d callers=%d callees=%d tests=%d commits=%d",
            cu.key,
            cu.changed_lines,
            cu.removed_nearby,
            len(ctx.callers) if ctx else 0,
            len(ctx.callees) if ctx else 0,
            len(ctx.tests) if ctx else 0,
            len(ctx.history) if ctx else 0,
        )

    report = AnalysisReport()
    sandbox = get_sandbox(settings) if archive is not None else None
    if sandbox is not None:
        try:
            report = run_analysis(
                archive,
                changeset,
                contexts,
                sandbox,
                analysis_timeout=settings.analysis_timeout,
                test_timeout=settings.test_timeout,
            )
        except Exception:
            logger.exception("analysis failed repo=%s pr=%s", event.repo_full_name, event.pr_number)

    if report.tests is not None:
        t = report.tests
        logger.info(
            "tests ran=%d passed=%d failed=%d errors=%d timed_out=%s crashed=%s",
            t.tests, t.passed, t.failed, t.errors, t.timed_out, t.crashed,
        )  # fmt: skip
    for f in report.relevant:
        logger.info(
            "finding %s %s %s severity=%s scope=%s: %s",
            f.tool, f.rule, f.location, f.severity, f.scope, f.message,
        )  # fmt: skip
    for note in report.notes:
        logger.warning("analysis note: %s", note)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    settings = get_settings()

    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_github_event == "ping":
        return {"status": "pong"}

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event '{x_github_event}' not handled"}

    try:
        event = PullRequestEvent.from_payload(await request.json())
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed payload: {exc}") from exc

    if not event.should_review:
        return {"status": "ignored", "reason": f"action '{event.action}' (draft={event.draft})"}

    background_tasks.add_task(review_pull_request, event)
    return {"status": "queued", "repo": event.repo_full_name, "pr": event.pr_number}
