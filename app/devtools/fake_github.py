"""An in-memory GitHub for tests, the offline demo and the benchmark.

It speaks the subset of the REST API the agent uses and enforces the rules that make real
GitHub reject requests, most importantly: a review comment must sit on a line that is part of
the pull request's diff (otherwise 422).
"""

import io
import json
import re
import tarfile
import time
from datetime import UTC, datetime, timedelta

import httpx

from app.devtools.diffing import make_unified_diff
from app.diff import parse_unified_diff
from app.events import PullRequestEvent
from app.github_client import GitHubClient

BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40


def make_tarball(files: dict[str, str], prefix: str = "octo-demo-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{prefix}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeGitHub:
    def __init__(
        self,
        base_files: dict[str, str],
        head_files: dict[str, str],
        *,
        repo: str = "octo/demo",
        pr_number: int = 1,
        installation_id: int = 1,
        history: list[dict] | None = None,
    ):
        self.repo = repo
        self.pr_number = pr_number
        self.installation_id = installation_id
        self.base_files, self.head_files = base_files, head_files
        self.base_sha, self.head_sha = BASE_SHA, HEAD_SHA
        self.current_head_sha = HEAD_SHA  # change it to simulate a newer push
        self.history = history or []
        self.diff_text = make_unified_diff(base_files, head_files)
        self.diffs = parse_unified_diff(self.diff_text)
        self.commentable = {d.path: d.commentable_lines for d in self.diffs if not d.is_deleted}

        self.reviews: list[dict] = []
        self.review_comments: list[dict] = []
        self.issue_comments: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self._next_id = 1000

    # ---- helpers -----------------------------------------------------------------------

    def event(self, action: str = "opened") -> PullRequestEvent:
        return PullRequestEvent(
            installation_id=self.installation_id,
            repo_full_name=self.repo,
            pr_number=self.pr_number,
            head_sha=self.head_sha,
            base_sha=self.base_sha,
            action=action,
            draft=False,
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> GitHubClient:
        return GitHubClient("ghs_fake", transport=self.transport())

    def factory(self):
        """A `github_factory` for ReviewService: yields a client bound to this fake."""
        fake = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner.client = fake.client()
                return self_inner.client

            def __exit__(self_inner, *exc):
                self_inner.client.close()

        return lambda event: _Ctx()

    def _files_at(self, ref: str) -> dict[str, str]:
        return self.head_files if ref == self.head_sha else self.base_files

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    # ---- request routing ---------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.requests.append((method, path))
        r = re.escape(self.repo)
        accept = request.headers.get("accept", "")

        if m := re.fullmatch(r"/app/installations/(\d+)/access_tokens", path):
            expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return httpx.Response(201, json={"token": "ghs_fake", "expires_at": expires})

        if request.url.host == "codeload.github.com":
            ref = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=make_tarball(self._files_at(ref)))
        if m := re.fullmatch(rf"/repos/{r}/tarball/(\w+)", path):
            return httpx.Response(
                302, headers={"Location": f"https://codeload.github.com/fake/{m.group(1)}"}
            )

        if re.fullmatch(rf"/repos/{r}/pulls/{self.pr_number}", path) and method == "GET":
            if "diff" in accept:
                return httpx.Response(200, text=self.diff_text)
            return httpx.Response(
                200,
                json={
                    "number": self.pr_number,
                    "state": "open",
                    "head": {"sha": self.current_head_sha},
                    "base": {"sha": self.base_sha},
                },
            )

        if m := re.fullmatch(rf"/repos/{r}/contents/(.+)", path):
            text = self._files_at(request.url.params.get("ref", "")).get(m.group(1))
            return httpx.Response(404) if text is None else httpx.Response(200, text=text)

        if re.fullmatch(rf"/repos/{r}/commits", path):
            return httpx.Response(200, json=self.history)

        if re.fullmatch(rf"/repos/{r}/pulls/{self.pr_number}/comments", path):
            return self._page(self.review_comments, request)

        if re.fullmatch(rf"/repos/{r}/pulls/{self.pr_number}/reviews", path) and method == "POST":
            return self._create_review(json.loads(request.content))

        if re.fullmatch(rf"/repos/{r}/issues/{self.pr_number}/comments", path):
            if method == "GET":
                return self._page(self.issue_comments, request)
            comment = {"id": self._id(), "body": json.loads(request.content)["body"]}
            self.issue_comments.append(comment)
            return httpx.Response(201, json=comment)

        if method == "PATCH" and re.fullmatch(rf"/repos/{r}/issues/comments/(\d+)", path):
            cid = int(path.rsplit("/", 1)[-1])
            for c in self.issue_comments:
                if c["id"] == cid:
                    c["body"] = json.loads(request.content)["body"]
                    return httpx.Response(200, json=c)
            return httpx.Response(404)

        return httpx.Response(404, json={"message": f"fake github: no route for {method} {path}"})

    @staticmethod
    def _page(items: list[dict], request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=items if page == 1 else [])

    def _create_review(self, payload: dict) -> httpx.Response:
        def reject(why: str) -> httpx.Response:
            return httpx.Response(422, json={"message": "Unprocessable Entity", "errors": [why]})

        if payload.get("commit_id") != self.current_head_sha:
            return reject("commit_id is not the head of the pull request")
        comments = payload.get("comments") or []
        for c in comments:
            lines = self.commentable.get(c.get("path", ""))
            if lines is None:
                return reject(f"{c.get('path')} is not part of the diff")
            wanted = {c["line"], c.get("start_line", c["line"])}
            if not wanted <= lines:
                return reject(f"line {sorted(wanted)} of {c['path']} is not in the diff")
            if c.get("side", "RIGHT") != "RIGHT":
                return reject("only RIGHT side comments are supported by this fake")
        review = {"id": self._id(), "body": payload.get("body", ""), "comments": comments}
        self.reviews.append(review)
        for c in comments:
            self.review_comments.append({**c, "id": self._id(), "created": time.time()})
        return httpx.Response(200, json={"id": review["id"]})
