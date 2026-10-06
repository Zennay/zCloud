import unittest

from scripts.zcloud_pr_security_scan import changed_paths, parse_added_lines, scan_patch


class PrSecurityScanTests(unittest.TestCase):
    def test_allows_pinned_and_local_actions(self):
        patch = """diff --git a/.github/workflows/security.yml b/.github/workflows/security.yml
new file mode 100644
--- /dev/null
+++ b/.github/workflows/security.yml
@@ -0,0 +1,3 @@
+steps:
+  - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
+  - uses: ./local-action
"""
        self.assertEqual(scan_patch(patch), [])

    def test_blocks_floating_action_tag(self):
        patch = """diff --git a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml
+++ b/.github/workflows/security.yml
@@ -4,0 +5 @@
+  - uses: actions/checkout@v6
"""
        findings = scan_patch(patch)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "dependency:action-unpinned")
        self.assertEqual(findings[0].line_no, 5)

    def test_blocks_new_dependency_manifest_until_vulnerability_scanner_exists(self):
        patch = """diff --git a/package.json b/package.json
new file mode 100644
--- /dev/null
+++ b/package.json
@@ -0,0 +1,3 @@
+{
+  "dependencies": {}
+}
"""
        findings = scan_patch(patch)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "dependency:manifest-review-required")
        self.assertEqual(findings[0].path, "package.json")

    def test_blocks_requirements_variant(self):
        patch = """diff --git a/requirements-dev.txt b/requirements-dev.txt
new file mode 100644
--- /dev/null
+++ b/requirements-dev.txt
@@ -0,0 +1 @@
+example-lib==1.2.3
"""
        findings = scan_patch(patch)
        self.assertEqual([f.code for f in findings], ["dependency:manifest-review-required"])

    def test_blocks_high_confidence_new_secrets(self):
        github_token = "gh" + "p_" + ("A" * 36)
        api_secret = "api_key=" + chr(34) + ("Z" * 28) + chr(34)
        patch = f"""diff --git a/config.txt b/config.txt
--- a/config.txt
+++ b/config.txt
@@ -1,0 +2,2 @@
+token={github_token}
+{api_secret}
"""
        findings = scan_patch(patch)
        self.assertEqual(
            [finding.code for finding in findings],
            ["secret:github-token", "secret:literal-secret-assignment"],
        )

    def test_ignores_removed_secret_material(self):
        token = "gh" + "p_" + ("B" * 36)
        patch = f"""diff --git a/config.txt b/config.txt
--- a/config.txt
+++ b/config.txt
@@ -1 +1 @@
-token={token}
+token=example-placeholder
"""
        self.assertEqual(scan_patch(patch), [])

    def test_placeholder_literal_is_allowed(self):
        patch = """diff --git a/docs/example.md b/docs/example.md
--- a/docs/example.md
+++ b/docs/example.md
@@ -1,0 +2 @@
+api_key="example-placeholder-value-123456789"
"""
        self.assertEqual(scan_patch(patch), [])

    def test_changed_paths_are_unique_and_ignore_deletions(self):
        patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
diff --git a/b.txt b/b.txt
deleted file mode 100644
--- a/b.txt
+++ /dev/null
@@ -1 +0,0 @@
-old
"""
        self.assertEqual(changed_paths(patch), ["a.txt"])

    def test_added_line_numbers_follow_hunks_and_context(self):
        patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -10,2 +10,3 @@
 context
+new one
 context two
@@ -30,0 +32 @@
+new two
"""
        added = parse_added_lines(patch)
        self.assertEqual([(line.line_no, line.text) for line in added], [(11, "new one"), (32, "new two")])

    def test_rejects_invalid_hunk_header(self):
        patch = """diff --git a/a b/a
--- a/a
+++ b/a
@@ invalid @@
+value
"""
        with self.assertRaises(ValueError):
            parse_added_lines(patch)


if __name__ == "__main__":
    unittest.main()
