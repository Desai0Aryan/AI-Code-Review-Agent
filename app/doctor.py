"""Preflight checks: tells you what is wrong with your setup before a PR does.

Each check returns (level, message). Levels: ok, warn, fail.
"""

import shutil
import subprocess
from dataclasses import dataclass

import httpx
import jwt

from app.config import Settings
from app.github_auth import create_app_jwt

REQUIRED_PERMISSIONS = {"pull_requests": "write", "contents": "read", "metadata": "read"}
LEVELS = {"read": 0, "write": 1, "admin": 2}


@dataclass
class Check:
    level: str  # ok | warn | fail
    name: str
    detail: str = ""


def check_python_stack() -> Check:
    try:
        from app.parsing import parse_python

        parse_python("x.py", "def f():\n    return 1\n")
    except (ImportError, OSError, RuntimeError) as exc:
        return Check("fail", "tree-sitter", f"parser does not work: {exc}")
    return Check("ok", "tree-sitter parser")


def check_credentials(s: Settings) -> list[Check]:
    out: list[Check] = []
    if not s.github_app_id:
        out.append(
            Check("fail", "CRA_GITHUB_APP_ID", "not set (GitHub App settings page, 'App ID')")
        )
    if not s.github_webhook_secret:
        out.append(Check("fail", "CRA_GITHUB_WEBHOOK_SECRET", "not set; webhooks will be rejected"))
    if not s.github_private_key:
        out.append(
            Check(
                "fail", "CRA_GITHUB_PRIVATE_KEY", "not set (paste the .pem with \\n for newlines)"
            )
        )
    elif s.github_app_id:
        try:
            create_app_jwt(s.github_app_id, s.private_key_pem)
            out.append(Check("ok", "private key", "parses and can sign a JWT"))
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            out.append(Check("fail", "CRA_GITHUB_PRIVATE_KEY", f"cannot sign a JWT: {exc}"))
    return out


def check_github(s: Settings, transport: httpx.BaseTransport | None = None) -> list[Check]:
    """Ask GitHub what the App is configured to do. Needs working credentials."""
    out: list[Check] = []
    try:
        token = create_app_jwt(s.github_app_id, s.private_key_pem)
    except (ValueError, TypeError, jwt.PyJWTError):
        return out
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(
            base_url=s.github_api_url, headers=headers, timeout=15, transport=transport
        ) as c:
            r = c.get("/app")
            if r.status_code == 401:
                return [
                    Check(
                        "fail",
                        "GitHub App",
                        "GitHub rejected the credentials (wrong app id or key?)",
                    )
                ]
            r.raise_for_status()
            app = r.json()
            out.append(Check("ok", "GitHub App", f"authenticated as '{app.get('name')}'"))

            perms = app.get("permissions", {})
            for name, need in REQUIRED_PERMISSIONS.items():
                have = perms.get(name)
                if have is None or LEVELS.get(have, -1) < LEVELS[need]:
                    out.append(
                        Check("fail", f"permission {name}", f"needs '{need}', App has '{have}'")
                    )
            if not any(c_.level == "fail" for c_ in out):
                out.append(Check("ok", "permissions", "pull requests, contents, metadata"))
            if "pull_request" not in app.get("events", []):
                out.append(
                    Check("fail", "events", "App is not subscribed to 'Pull request' events")
                )
            else:
                out.append(Check("ok", "events", "subscribed to pull_request"))

            hook = c.get("/app/hook/config")
            if hook.status_code == 200:
                cfg = hook.json()
                if cfg.get("content_type") != "json":
                    out.append(
                        Check(
                            "fail",
                            "webhook content type",
                            f"is '{cfg.get('content_type')}', set it to application/json",
                        )
                    )
                else:
                    out.append(Check("ok", "webhook content type", "application/json"))
                if not cfg.get("url"):
                    out.append(Check("fail", "webhook URL", "not set"))
                else:
                    out.append(Check("ok", "webhook URL", cfg["url"]))

            inst = c.get("/app/installations")
            if inst.status_code == 200:
                n = len(inst.json())
                lvl = "ok" if n else "warn"
                out.append(
                    Check(
                        lvl,
                        "installations",
                        f"installed on {n} account(s)"
                        if n
                        else "not installed anywhere yet: install it on a test repository",
                    )
                )
    except httpx.HTTPError as exc:
        out.append(Check("fail", "GitHub API", f"could not reach {s.github_api_url}: {exc}"))
    return out


def check_sandbox(s: Settings) -> list[Check]:
    mode = s.sandbox.lower()
    if mode == "off":
        return [
            Check(
                "warn",
                "sandbox",
                "off: no static analysis or tests (findings come from the model only)",
            )
        ]
    if mode == "local":
        if not s.allow_unsafe_local_sandbox:
            return [Check("fail", "sandbox", "local needs CRA_ALLOW_UNSAFE_LOCAL_SANDBOX=true")]
        return [
            Check(
                "warn",
                "sandbox",
                "local: PR code runs WITHOUT isolation. Use only on a throwaway machine",
            )
        ]
    docker = shutil.which("docker")
    if not docker:
        return [Check("fail", "docker", "not installed (or set CRA_SANDBOX=local for development)")]
    try:
        v = subprocess.run(
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        v = None
    if v is None or v.returncode != 0:
        return [
            Check(
                "fail", "docker", "installed, but the daemon is not reachable (is Docker running?)"
            )
        ]
    out = [Check("ok", "docker", f"daemon {v.stdout.strip()}")]
    img = subprocess.run(
        [docker, "image", "inspect", s.sandbox_image], capture_output=True, check=False, timeout=15
    )
    if img.returncode != 0:
        out.append(
            Check(
                "fail", "sandbox image", f"'{s.sandbox_image}' is missing. Build it: make sandbox"
            )
        )
        return out
    import tempfile
    from pathlib import Path

    from app.sandbox import DockerSandbox

    with tempfile.TemporaryDirectory() as tmp:
        res = DockerSandbox(image=s.sandbox_image).run(
            ["python", "-c", "print('sandbox-ok')"], work=Path(tmp), timeout=60
        )
    if "sandbox-ok" in res.stdout:
        out.append(
            Check("ok", "sandbox image", "runs a container with no network, read-only mounts")
        )
    else:
        out.append(
            Check("fail", "sandbox image", f"test run failed: {(res.stderr or res.stdout)[-200:]}")
        )
    return out


def check_llm(s: Settings, call: bool = False) -> Check:
    if not s.anthropic_api_key:
        return Check(
            "warn",
            "LLM",
            "CRA_ANTHROPIC_API_KEY not set: tool findings only, no model review or fixes",
        )
    if not call:
        return Check(
            "ok", "LLM", f"key set, model {s.llm_model} (use --llm to test it with a real call)"
        )
    from app.llm import LLMError, get_llm

    try:
        get_llm(s).complete_json(  # type: ignore[union-attr]
            system="Reply by calling the tool.",
            user="ping",
            tool_name="pong",
            tool_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            max_tokens=64,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        return Check("fail", "LLM", f"call failed: {exc}")
    return Check("ok", "LLM", f"call to {s.llm_model} succeeded")


def check_data_dir(s: Settings) -> Check:
    from app.store import Store

    try:
        Store(s.data_dir)
    except OSError as exc:
        return Check("fail", "data dir", f"cannot write to '{s.data_dir}': {exc}")
    return Check("ok", "data dir", s.data_dir)


def run_doctor(s: Settings, *, online: bool = True, llm_call: bool = False) -> list[Check]:
    checks = [check_python_stack(), check_data_dir(s)]
    creds = check_credentials(s)
    checks += creds
    if online and s.github_app_id and not any(c.level == "fail" for c in creds):
        checks += check_github(s)
    checks += check_sandbox(s)
    checks.append(check_llm(s, llm_call))
    return checks


def format_checks(checks: list[Check]) -> str:
    icon = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]"}
    lines = [f"{icon[c.level]} {c.name}" + (f": {c.detail}" if c.detail else "") for c in checks]
    fails = sum(c.level == "fail" for c in checks)
    warns = sum(c.level == "warn" for c in checks)
    lines.append("")
    lines.append("Ready." if not fails else f"{fails} problem(s) to fix before reviews will work.")
    if warns and not fails:
        lines.append(f"{warns} warning(s): the agent runs, with reduced capability.")
    return "\n".join(lines)
