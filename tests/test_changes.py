import httpx

from app.changes import map_changes
from app.diff import parse_unified_diff
from app.events import PullRequestEvent
from app.github_client import GitHubClient
from app.pipeline import build_changeset

OLD = """\
class Cart:
    def __init__(self):
        self.items = []

    def total(self):
        return sum(i.price for i in self.items)


def helper():
    return 1
"""

NEW = """\
import math

class Cart:
    def __init__(self):
        self.items = []

    def total(self, discount=0):
        raw = sum(i.price for i in self.items)
        return raw - discount


def helper():
    return 1
"""

DIFF = """\
diff --git a/shop/cart.py b/shop/cart.py
--- a/shop/cart.py
+++ b/shop/cart.py
@@ -1,3 +1,5 @@
+import math
+
 class Cart:
     def __init__(self):
         self.items = []
@@ -5,2 +7,3 @@ class Cart:
-    def total(self):
-        return sum(i.price for i in self.items)
+    def total(self, discount=0):
+        raw = sum(i.price for i in self.items)
+        return raw - discount
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 hi
+there
"""


def test_changed_lines_map_to_functions():
    cs = map_changes(
        parse_unified_diff(DIFF), {"shop/cart.py": NEW}, {"shop/cart.py": OLD}
    )
    assert [c.key for c in cs.changed_units] == ["shop/cart.py::Cart.total"]
    total = cs.changed_units[0]
    assert total.changed_lines == [7, 8, 9]
    assert total.removed_nearby == 2


def test_module_level_and_skipped_files():
    cs = map_changes(parse_unified_diff(DIFF), {"shop/cart.py": NEW}, {"shop/cart.py": OLD})
    assert cs.module_level["shop/cart.py"] == [1, 2]  # the new import and blank line
    assert cs.skipped["README.md"] == "source unavailable"


def test_unsupported_language_skipped():
    cs = map_changes(parse_unified_diff(DIFF), {"README.md": "hi\nthere\n"})
    assert cs.skipped["README.md"] == "unsupported language"


def test_build_changeset_end_to_end_with_mock_github():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("ref")))
        if request.url.path == "/repos/octo/demo/pulls/5":
            return httpx.Response(200, text=DIFF)
        if request.url.path == "/repos/octo/demo/contents/shop/cart.py":
            ref = request.url.params["ref"]
            return httpx.Response(200, text=NEW if ref == "h" * 40 else OLD)
        return httpx.Response(404)

    event = PullRequestEvent(
        installation_id=1,
        repo_full_name="octo/demo",
        pr_number=5,
        head_sha="h" * 40,
        base_sha="b" * 40,
        action="opened",
        draft=False,
    )
    client = GitHubClient("tok", transport=httpx.MockTransport(handler))
    cs = build_changeset(client, event)

    assert [c.key for c in cs.changed_units] == ["shop/cart.py::Cart.total"]
    assert cs.changed_units[0].removed_nearby == 2
    # README.md is not a supported language, so we never fetched its contents
    assert all("README" not in p for p, _ in seen)
