# AI Code Review Agent

A GitHub App that reviews pull requests the way a careful colleague would: it reads the changed
functions in context, runs static analysis and the related tests in a sandbox, and only reports
what it can back with evidence. Anything it is unsure about is flagged as a question, not stated as fact.

On a PR it will:

1. Parse the diff with tree-sitter and find which functions changed.
2. Gather context: callers, callees, related tests, recent commit history.
3. Run ruff, bandit, mypy and the related pytest tests in a Docker sandbox (no network, read-only code).
4. Check whether a failing test also fails on the base commit (then it is not blamed on the PR).
5. Ask the model for bugs, then **ground** every claim: the cited line must have been shown to the model
   and the quoted text must really be there. Claims that fail are discarded and counted.
6. Decide status in code, not by the model: `confirmed` (a tool or failing test backs it), `likely`, or `uncertain`.
7. Try a fix, apply it in the sandbox and re-run lint and tests. Only fixes that pass are offered, as
   one-click GitHub suggestions.
8. Post inline comments (only on lines GitHub accepts, never duplicated) and one summary comment that is updated in place.

## Try it in two minutes (no GitHub needed)

```bash
make install && source .venv/bin/activate
make test          # full test suite
make demo          # a PR with a seeded bug goes through the whole pipeline
make benchmark     # 15 seeded bugs + 6 clean PRs, scored
```

## Connect your GitHub App

Prerequisites: Python 3.11+, Docker (for the sandbox), a GitHub App, optionally an Anthropic API key.

1. **GitHub App settings** (already created): Permissions: Pull requests *read & write*, Contents *read*,
   Metadata *read*. Subscribe to **Pull request** events. Webhook content type must be **application/json**.
   Generate a private key.
2. `cp .env.example .env` and fill in `CRA_GITHUB_APP_ID`, `CRA_GITHUB_WEBHOOK_SECRET`,
   `CRA_GITHUB_PRIVATE_KEY` (the .pem with literal `\n` for newlines) and, for model review and fixes,
   `CRA_ANTHROPIC_API_KEY`.
3. `make sandbox` builds the isolated image used to run linters and tests on PR code.
4. `make doctor` checks everything, including asking GitHub whether the App's permissions, events,
   webhook URL and content type are right, and runs a real container through the sandbox. Fix anything marked FAIL.
5. `make serve` starts the server on port 8000. Run it on the host, not in the compose container, so it can start sandboxes.
6. Expose it: `ngrok http 8000` (or smee.io) and set the App's Webhook URL to `https://<public-host>/webhook`.
7. Install the App on a test repo and open a PR.

Review a PR right now without waiting for a webhook (prints, posts nothing unless `--post`):

```bash
python -m app review owner/repo 12
python -m app review owner/repo 12 --post
```

Good first PR: change a function so it has an obvious bug (swap an operator, mistype a variable) in a repo that has pytest tests.

## Configuration

See `.env.example`. Notable: `CRA_DRY_RUN=true` (review, post nothing), `CRA_SANDBOX=docker|local|off`,
`CRA_ENABLE_FIXES`, `CRA_MIN_CONFIDENCE`, `CRA_LLM_MODEL`. `CRA_SANDBOX=local` has **no isolation**
and needs `CRA_ALLOW_UNSAFE_LOCAL_SANDBOX=true`; use it only on a throwaway machine.

## Safety model

- Webhooks are verified with HMAC; repo names are validated; duplicate deliveries are ignored.
- PR code only ever runs inside the sandbox: `--network none`, read-only mounts, all capabilities dropped,
  non-root, memory/CPU/PID limits, hard timeout. Host secrets are never passed in. Dependencies install as
  wheels only (no build scripts) in a separate step that does not run PR code.
- Repo text sent to the model is wrapped in random delimiters and treated as data, never instructions.
- Stale reviews (the PR moved on) are never posted. Source code of changed functions and their callers
  is sent to the Anthropic API when a key is configured.

## Benchmark

`python -m benchmark` runs 21 seeded cases (8 bugs a test catches, 5 a linter catches, 2 only a reader
can catch, 6 clean PRs) through the real pipeline against an in-memory GitHub. Add `--llm` (needs an API key)
to include the model. Results are written to `benchmark/results/`. Deterministic baseline:
**13/15 bugs caught, 0 false alarms on 21 cases**; the 2 misses are exactly the bugs with no covering test,
which is what the model review is for. See [docs/BENCHMARK.md](docs/BENCHMARK.md) for method and caveats.

## Layout

| Path | What |
|---|---|
| `app/main.py` | webhook server |
| `app/service.py` | the end-to-end review |
| `app/{diff,parsing,changes,index,context}.py` | diff, tree-sitter, change mapping, repo context |
| `app/{sandbox,analyzers,testrun,analysis,baseline}.py` | sandboxed tools and tests |
| `app/{llm,prompts,grounding,reviewer}.py` | model review with grounding |
| `app/{fixer,publisher,render}.py` | tested fixes and PR comments |
| `app/devtools/` | in-memory GitHub, demo |
| `benchmark/` | seeded-defect corpus and scorer |

## Known limits

- Python only. Callers/callees resolve by name (ambiguous matches are flagged), not full type inference.
- The Docker sandbox path has been unit-tested for its command construction but needs your Docker to exercise for real: run `make doctor`.
- Tests needing services, or dependencies without wheels, will not run; the report says so.
- Inline comments only land on lines in the diff; findings elsewhere appear in the summary.
