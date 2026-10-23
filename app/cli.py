"""Command line: serve the app, check the setup, run the offline demo, or review a real PR."""

import argparse
import sys

from app.config import Settings, get_settings
from app.logging_config import configure_logging


def cmd_serve(args) -> int:
    import uvicorn

    s = get_settings()
    configure_logging(s.log_level)
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level=s.log_level.lower())
    return 0


def cmd_doctor(args) -> int:
    from app.doctor import format_checks, run_doctor

    checks = run_doctor(get_settings(), online=not args.offline, llm_call=args.llm)
    print(format_checks(checks))
    return 1 if any(c.level == "fail" for c in checks) else 0


def cmd_demo(_args) -> int:
    from app.devtools.demo import run_demo

    configure_logging("WARNING")
    fake, out = run_demo(get_settings())
    print(f"status: {out.status}\n")
    for c in fake.review_comments:
        print(f"--- inline comment on {c['path']}:{c['line']} ---\n{c['body']}\n")
    for c in fake.issue_comments:
        print(f"--- summary comment ---\n{c['body']}")
    if not get_settings().anthropic_api_key:
        print("(no CRA_ANTHROPIC_API_KEY set: this run used static analysis and tests only)")
    return 0 if out.status == "reviewed" else 1


def cmd_review(args) -> int:
    """Review one real pull request without waiting for a webhook. Dry run unless --post."""
    import httpx

    from app.doctor import create_app_jwt
    from app.events import PullRequestEvent
    from app.github_client import GitHubClient
    from app.service import ReviewService

    s: Settings = get_settings()
    configure_logging(s.log_level)
    repo, number = args.repo, args.number
    jwt_token = create_app_jwt(s.github_app_id, s.private_key_pem)
    resp = httpx.get(
        f"{s.github_api_url}/repos/{repo}/installation",
        headers={"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"The App is not installed on {repo} (GitHub said {resp.status_code}).")
        return 1
    installation_id = resp.json()["id"]
    service = ReviewService(s)
    with service._github_factory(
        PullRequestEvent(installation_id, repo, number, "", "", "opened", False)
    ) as client:
        assert isinstance(client, GitHubClient)
        pr = client.get_pull_request(repo, number)
    event = PullRequestEvent(
        installation_id, repo, number, pr["head"]["sha"], pr["base"]["sha"], "opened", False
    )
    out = service.run(event, publish=args.post)
    print(out.markdown or f"status: {out.status} {out.error or ''}")
    if not args.post:
        print("\n(dry run: nothing was posted. Add --post to publish to the pull request.)")
    return 0 if out.status in ("reviewed", "nothing-to-review") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("serve", help="run the webhook server")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("doctor", help="check your setup")
    sp.add_argument("--offline", action="store_true", help="skip calls to GitHub")
    sp.add_argument("--llm", action="store_true", help="make one real call to the model")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("demo", help="offline demo with a seeded bug (no GitHub needed)")
    sp.set_defaults(fn=cmd_demo)

    sp = sub.add_parser("review", help="review a real pull request now")
    sp.add_argument("repo", help="owner/name")
    sp.add_argument("number", type=int)
    sp.add_argument("--post", action="store_true", help="publish comments (default: print only)")
    sp.set_defaults(fn=cmd_review)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
