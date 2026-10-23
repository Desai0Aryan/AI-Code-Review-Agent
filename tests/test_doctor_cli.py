import httpx

from app.cli import main
from app.config import Settings
from app.doctor import check_credentials, check_github, check_llm, check_sandbox, run_doctor


def _settings(rsa_keypair, **kw):
    return Settings(
        github_app_id="1", github_webhook_secret="s", github_private_key=rsa_keypair[0], **kw
    )


def _github(app: dict, hook: dict, installs: list):
    def handler(request: httpx.Request) -> httpx.Response:
        return {
            "/app": httpx.Response(200, json=app),
            "/app/hook/config": httpx.Response(200, json=hook),
            "/app/installations": httpx.Response(200, json=installs),
        }[request.url.path]

    return httpx.MockTransport(handler)


GOOD_APP = {
    "name": "reviewer",
    "permissions": {"pull_requests": "write", "contents": "read", "metadata": "read"},
    "events": ["pull_request"],
}


def test_missing_credentials_are_reported():
    names = {c.name for c in check_credentials(Settings()) if c.level == "fail"}
    assert {"CRA_GITHUB_APP_ID", "CRA_GITHUB_WEBHOOK_SECRET", "CRA_GITHUB_PRIVATE_KEY"} <= names


def test_valid_key_signs(rsa_keypair):
    assert all(c.level == "ok" for c in check_credentials(_settings(rsa_keypair)))
    bad = Settings(github_app_id="1", github_webhook_secret="s", github_private_key="nope")
    assert any(c.level == "fail" for c in check_credentials(bad))


def test_github_config_problems_are_named(rsa_keypair):
    weak = {**GOOD_APP, "permissions": {"pull_requests": "read", "contents": "read", "metadata": "read"},
            "events": []}  # fmt: skip
    checks = check_github(
        _settings(rsa_keypair),
        _github(weak, {"content_type": "form", "url": "https://x/webhook"}, []),
    )
    fails = {c.name for c in checks if c.level == "fail"}
    assert {"permission pull_requests", "events", "webhook content type"} <= fails
    assert any(c.name == "installations" and c.level == "warn" for c in checks)


def test_healthy_github_config_passes(rsa_keypair):
    checks = check_github(
        _settings(rsa_keypair),
        _github(GOOD_APP, {"content_type": "json", "url": "https://x/webhook"}, [{"id": 1}]),
    )
    assert all(c.level == "ok" for c in checks)


def test_sandbox_and_llm_checks_without_docker_or_key():
    assert check_sandbox(Settings(sandbox="off"))[0].level == "warn"
    assert check_sandbox(Settings(sandbox="local"))[0].level == "fail"
    assert check_llm(Settings()).level == "warn"
    assert check_llm(Settings(anthropic_api_key="k")).level == "ok"


def test_run_doctor_offline(rsa_keypair, tmp_path):
    checks = run_doctor(_settings(rsa_keypair, sandbox="off", data_dir=str(tmp_path)), online=False)
    assert not any(c.level == "fail" for c in checks)


def test_demo_command_runs_the_whole_pipeline(capsys, monkeypatch):
    monkeypatch.chdir(__import__("tempfile").mkdtemp())
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "inline comment on shop/cart.py" in out and "aply_discount" in out
