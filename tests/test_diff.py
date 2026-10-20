from app.diff import parse_unified_diff

SAMPLE = """\
diff --git a/shop/cart.py b/shop/cart.py
index 1111111..2222222 100644
--- a/shop/cart.py
+++ b/shop/cart.py
@@ -1,6 +1,7 @@
 class Cart:
     def __init__(self):
         self.items = []
-    def total(self):
-        return sum(i.price for i in self.items)
+    def total(self, discount=0):
+        raw = sum(i.price for i in self.items)
+        return raw - discount

 def helper():
@@ -20,3 +21,4 @@ def helper():
     return 1
+# trailing

diff --git a/new_file.py b/new_file.py
new file mode 100644
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-gone = True
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


def test_parses_all_files():
    files = parse_unified_diff(SAMPLE)
    assert [f.path for f in files] == ["shop/cart.py", "new_file.py", "old.py", "img.png"]


def test_line_numbers_and_content():
    cart = parse_unified_diff(SAMPLE)[0]
    assert len(cart.hunks) == 2
    first = cart.hunks[0]
    assert first.old_start == 1 and first.new_start == 1
    assert [n for n, _ in first.added] == [4, 5, 6]
    assert [n for n, _ in first.removed] == [4, 5]
    assert "return raw - discount" in first.added[2][1]
    assert cart.hunks[1].added == [(22, "# trailing")]


def test_new_deleted_binary_flags():
    files = {f.path: f for f in parse_unified_diff(SAMPLE)}
    assert files["new_file.py"].is_new
    assert files["new_file.py"].added_lines == [1, 2]
    assert files["old.py"].is_deleted
    assert files["img.png"].is_binary


def test_empty_diff():
    assert parse_unified_diff("") == []
