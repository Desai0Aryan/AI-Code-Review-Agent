"""FastAPI entrypoint: receives GitHub webhooks and queues PR reviews."""

import json
import logging
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from app.config import get_settings
from app.events import PullRequestEvent
from app.logging_config import configure_logging
from app.security import verify_signature
from app.service import ReviewService
from app.store import Store

logger = logging.getLogger("code_review_agent")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    configure_logging(get_settings().log_level)
    yield


app = FastAPI(title="AI Code Review Agent", version="1.0.0", lifespan=_lifespan)

_seen_deliveries: OrderedDict[str, None] = OrderedDict()
_seen_lock = threading.Lock()
_pr_locks: dict[str, threading.Lock] = {}
_pr_locks_guard = threading.Lock()
_semaphore: threading.Semaphore | None = None


def _first_delivery(delivery_id: str | None) -> bool:
    """GitHub redelivers on timeouts; process each delivery id once."""
    if not delivery_id:
        return True
    with _seen_lock:
        if delivery_id in _seen_deliveries:
            return False
        _seen_deliveries[delivery_id] = None
        while len(_seen_deliveries) > 1000:
            _seen_deliveries.popitem(last=False)
    return True


def _pr_lock(key: str) -> threading.Lock:
    with _pr_locks_guard:
        return _pr_locks.setdefault(key, threading.Lock())


def _limiter(n: int) -> threading.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = threading.Semaphore(max(1, n))
    return _semaphore


def review_pull_request(event: PullRequestEvent) -> None:
    """Background job. One review per PR at a time, a few PRs in parallel."""
    settings = get_settings()
    with (
        _limiter(settings.max_concurrent_reviews),
        _pr_lock(f"{event.repo_full_name}#{event.pr_number}"),
    ):
        try:
            ReviewService(settings, store=Store(settings.data_dir)).run(event)
        except Exception:
            logger.exception("review crashed repo=%s pr=%s", event.repo_full_name, event.pr_number)


def _decode_payload(body: bytes, content_type: str) -> dict:
    """GitHub can send JSON or form-encoded (payload=<json>); accept both."""
    if "application/x-www-form-urlencoded" in content_type:
        fields = parse_qs(body.decode("utf-8", "replace"))
        return json.loads(fields["payload"][0])
    return json.loads(body)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
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
        payload = _decode_payload(body, request.headers.get("content-type", ""))
        event = PullRequestEvent.from_payload(payload)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed payload: {exc}") from exc

    if not event.should_review:
        return {"status": "ignored", "reason": f"action '{event.action}' (draft={event.draft})"}

    if not _first_delivery(x_github_delivery):
        return {"status": "ignored", "reason": "duplicate delivery"}

    background_tasks.add_task(review_pull_request, event)
    return {"status": "queued", "repo": event.repo_full_name, "pr": event.pr_number}
