import json

from fastapi.testclient import TestClient

from app.main import app
from app.security import compute_signature
from tests.conftest import TEST_SECRET

client = TestClient(app)


def _post(event: str, payload: dict, secret: str = TEST_SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": compute_signature(secret, body),
            "Content-Type": "application/json",
        },
    )


def _pr_payload(action="opened", draft=False):
    return {
        "action": action,
        "installation": {"id": 99},
        "repository": {"full_name": "octo/demo"},
        "pull_request": {
            "number": 5,
            "draft": draft,
            "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40},
        },
    }


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_bad_signature_rejected():
    resp = _post("pull_request", _pr_payload(), secret="wrong")
    assert resp.status_code == 401


def test_ping_answers_pong():
    assert _post("ping", {"zen": "hi"}).json() == {"status": "pong"}


def test_opened_pr_is_queued():
    resp = _post("pull_request", _pr_payload("opened"))
    assert resp.status_code == 202
    assert resp.json() == {"status": "queued", "repo": "octo/demo", "pr": 5}


def test_draft_pr_ignored():
    assert _post("pull_request", _pr_payload(draft=True)).json()["status"] == "ignored"


def test_closed_action_ignored():
    assert _post("pull_request", _pr_payload("closed")).json()["status"] == "ignored"


def test_other_events_ignored():
    assert _post("issues", {"action": "opened"}).json()["status"] == "ignored"


def test_malformed_payload_422():
    assert _post("pull_request", {"action": "opened"}).status_code == 422


def test_form_encoded_payload_is_accepted():
    from urllib.parse import urlencode

    body = urlencode({"payload": json.dumps(_pr_payload("opened"))}).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": compute_signature(TEST_SECRET, body),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert resp.status_code == 202 and resp.json()["status"] == "queued"


def test_duplicate_delivery_is_ignored():
    body = json.dumps(_pr_payload("opened")).encode()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "dup-1",
        "X-Hub-Signature-256": compute_signature(TEST_SECRET, body),
        "Content-Type": "application/json",
    }
    assert client.post("/webhook", content=body, headers=headers).json()["status"] == "queued"
    assert (
        client.post("/webhook", content=body, headers=headers).json()["reason"]
        == "duplicate delivery"
    )
