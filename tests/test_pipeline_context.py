import httpx
import pytest

from app.changes import ChangedUnit, ChangeSet
from app.events import PullRequestEvent
from app.github_client import GitHubClient
from app.index import RepoIndex
from app.pipeline import build_context
from tests.helpers import FAKE_REPO, make_tarball

EVENT = PullRequestEvent(
    installation_id=1,
    repo_full_name="octo/demo",
    pr_number=5,
    head_sha="h" * 40,
    base_sha="b" * 40,
    action="opened",
    draft=False,
)


def changeset_for_total() -> ChangeSet:
    unit = next(
        u
        for u in RepoIndex(FAKE_REPO).files["shop/cart.py"].units
        if u.qualified_name == "Cart.total"
    )
    return ChangeSet(changed_units=[ChangedUnit("shop/cart.py", unit, [8, 9])])


def make_handler(seen: list, commits_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com" and request.url.path.endswith(
            f"/tarball/{'h' * 40}"
        ):
            return httpx.Response(302, headers={"Location": "https://codeload.github.com/dl/abc"})
        if request.url.host == "codeload.github.com":
            return httpx.Response(200, content=make_tarball(FAKE_REPO))
        if request.url.path == "/repos/octo/demo/commits":
            assert request.url.params["path"] == "shop/cart.py"
            if commits_status != 200:
                return httpx.Response(commits_status, json={"message": "nope"})
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "a1b2c3d4e5f6",
                        "commit": {
                            "message": "Fix rounding\n\nlong body",
                            "author": {"name": "Dana", "date": "2026-09-30T10:00:00Z"},
                        },
                    }
                ],
            )
        return httpx.Response(404)

    return handler


def test_build_context_end_to_end_with_redirect_and_history():
    seen: list[httpx.Request] = []
    client = GitHubClient("secret-token", transport=httpx.MockTransport(make_handler(seen)))
    contexts = build_context(client, EVENT, changeset_for_total())

    ctx = contexts["shop/cart.py::Cart.total"]
    assert [r.key for r in ctx.callers] == ["shop/checkout.py::checkout"]
    assert [r.key for r in ctx.callees] == ["shop/pricing.py::apply_discount"]
    assert ctx.history[0].message == "Fix rounding"
    assert ctx.history[0].author == "Dana"

    # The API token must not leak to the cross-origin download host.
    download = next(r for r in seen if r.url.host == "codeload.github.com")
    assert "authorization" not in download.headers


def test_history_failure_does_not_break_context():
    client = GitHubClient("t", transport=httpx.MockTransport(make_handler([], commits_status=500)))
    ctx = build_context(client, EVENT, changeset_for_total())["shop/cart.py::Cart.total"]
    assert ctx.history == []
    assert ctx.callers  # the rest still worked


def test_empty_changeset_makes_no_requests():
    seen: list[httpx.Request] = []
    client = GitHubClient("t", transport=httpx.MockTransport(make_handler(seen)))
    assert build_context(client, EVENT, ChangeSet()) == {}
    assert seen == []


def test_oversized_archive_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1000)

    client = GitHubClient("t", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="exceeds"):
        client.get_repo_archive("octo/demo", "sha", max_bytes=100)
