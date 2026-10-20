"""FastAPI entrypoint: receives GitHub webhooks and queues PR reviews."""

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.config import get_settings
from app.events import PullRequestEvent
from app.security import verify_signature

logger = logging.getLogger("code_review_agent")

app = FastAPI(title="AI Code Review Agent", version="0.1.0")


def review_pull_request(event: PullRequestEvent) -> None:
    """Review pipeline entrypoint.

    Part 1 only logs. Later parts plug in: diff fetch -> AST parsing -> context
    retrieval -> static analysis -> LLM review -> validated fixes -> PR comments.
    """
    logger.info(
        "queued review repo=%s pr=%s head=%s",
        event.repo_full_name,
        event.pr_number,
        event.head_sha[:7],
    )


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
