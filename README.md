# AI Code Review Agent

A GitHub App that reviews pull requests, traces bugs across files, flags security issues,
and proposes fixes backed by line-specific evidence. Uncertain findings are flagged, not invented.

> Status: Part 4 of 8 (static analysis and sandboxed tests). See [docs/ROADMAP.md](docs/ROADMAP.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
cp .env.example .env   # fill in GitHub App credentials
uvicorn app.main:app --reload
```

Docker: `docker compose up --build`

Sandbox image (needed for static analysis and test runs, Part 4 onward):

```bash
docker build -f docker/sandbox.Dockerfile -t code-review-sandbox .
```
Run the app itself on the host (`uvicorn app.main:app`) so it can start sandbox containers. Do not
mount `/var/run/docker.sock` into the app container: that is root on the host. If Docker is missing,
analysis is skipped with a log message and the rest of the pipeline still runs.

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
- `app/index.py`: downloads the repo tarball once (in memory, never to disk), parses every Python file,
  and indexes definitions and call sites. Skips vendored dirs, path traversal and oversized files.
- `app/context.py`: for each changed function, finds callers (with call-site lines), callees resolved
  across files, related tests (by direct call and by `test_<module>.py` naming), and recent commits.
  Ambiguous name matches are flagged rather than guessed, and every limit is recorded as a caveat
  (e.g. "no tests reference this function") so the reviewer can calibrate confidence.
- `UnitContext.to_text()` renders that evidence as a compact block for the LLM prompt (Part 5).

### Known limits of context retrieval
Resolution is by name, not full type inference, so dynamic dispatch can hide or confuse call edges.
That is why ambiguous matches carry an `ambiguous` flag. History is per-file commit history from the
REST API (GitHub's REST API has no blame endpoint).

## Part 4: sandboxed analysis

- `app/sandbox.py`: **everything that touches PR code runs in a sandbox**, including the linters,
  because they read repo config. `DockerSandbox` starts one throwaway container per command:
  `--network none`, read-only code mount, `--read-only` root, all capabilities dropped,
  `no-new-privileges`, uid 65534, memory/CPU/PID limits, hard timeout with `docker kill`.
  Host environment variables (GitHub key, tokens) are never forwarded.
  `LocalSandbox` has **no isolation** and only runs when `CRA_ALLOW_UNSAFE_LOCAL_SANDBOX=true`.
- `app/workspace.py`: unpacks the repo tarball for the sandbox; drops symlinks, hard links, `..`
  paths and `.git`; enforces file-count and size caps.
- `app/analyzers.py`: ruff (`F,B,E9,PLE` only, config ignored), bandit, and mypy
  (`--check-untyped-defs --follow-imports=silent`, so changed files are checked against their
  imports across files). Parsers are separate from runners and tested on canned output.
- `app/testrun.py`: runs the related tests from Part 3 with pytest; results come back as JUnit XML,
  parsed with `defusedxml` because the XML is produced by untrusted code. Failures are located at the
  innermost traceback frame in non-test code.
- `app/analysis.py`: orchestration. Dependencies come from `requirements.txt` as **wheels only**
  (no build scripts) in a separate network-enabled step that never executes PR code; tests then run
  with no network.
- `app/findings.py`: every tool's output becomes one `Finding` (tool, rule, severity, confidence,
  category, path, line, message, evidence). Each finding is scoped as `changed-line`,
  `changed-function`, `related-test` or `other`; only the first three are reported, so pre-existing
  lint noise in untouched code never reaches the reviewer.

### Known limits
- Test failures caused by a wrong return value point at the assertion in the test, not the buggy
  source line (no traceback frame exists there); the finding is linked to the changed function via
  its context instead.
- Tests that need C-extension dependencies without wheels, or services like a database, will fail
  or be skipped; the report says when tests crashed or timed out rather than guessing.
- A failure may pre-date the PR. Part 6 compares against the base commit before blaming the change.
