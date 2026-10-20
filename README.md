# AI Code Review Agent

A GitHub App that reviews pull requests, traces bugs across files, flags security issues,
and proposes fixes backed by line-specific evidence. Uncertain findings are flagged, not invented.

> Status: Part 2 of 8 (diff parsing and AST). See [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
cp .env.example .env   # fill in GitHub App credentials
uvicorn app.main:app --reload
```

Docker: `docker compose up --build`

## Create the GitHub App

1. GitHub > Settings > Developer settings > GitHub Apps > New GitHub App.
2. Webhook URL: your public URL + `/webhook` (use `smee.io` or `ngrok` locally).
3. Webhook secret: any random string; put it in `CRA_GITHUB_WEBHOOK_SECRET`.
4. Permissions: Pull requests (read & write), Contents (read), Metadata (read), Checks (read & write).
5. Subscribe to events: Pull request.
6. Generate a private key; put it in `CRA_GITHUB_PRIVATE_KEY` and the App ID in `CRA_GITHUB_APP_ID`.
7. Install the app on a test repository.

## What exists so far

- `POST /webhook`: HMAC-verified (`X-Hub-Signature-256`), handles `ping` and `pull_request`
  (opened, synchronize, reopened, ready_for_review; drafts ignored).
- `app/github_auth.py`: app JWT (RS256) and cached installation tokens.
- Dockerfile, compose file, GitHub Actions CI (ruff + pytest + docker build).
- `app/diff.py`: unified-diff parser with exact old/new line numbers; handles new, deleted and binary files.
- `app/parsing.py`: tree-sitter Python parser that extracts functions, methods, nested functions and
  classes with line spans (decorators included) and the names each function calls.
- `app/changes.py`: maps added/removed lines onto the functions that contain them, and separates
  module-level edits from function edits.
- `app/github_client.py` and `app/pipeline.py`: fetch the PR diff and file contents at base/head,
  then build the change set. The webhook now logs every changed function per PR.
