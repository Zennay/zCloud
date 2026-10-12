"""Real-Git, tempfile-only tests for non-authorizing static dashboard planning."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.zcloud_dashboard_static_candidate_plan_20261012_w1 import (
    PlanError, plan,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "zcloud_dashboard_static_candidate_plan_20261012_w1.py"


class StaticDashboardCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-static-plan-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Static Planning Fixture")
        self.git("config", "user.email", "static-fixture@example.invalid")
        self.write("public/index.html", b"<!doctype html><title>Before</title>")
        self.write("server.py", b"print('backend unchanged')\n")
        self.base = self.commit("baseline")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True,
            check=True, timeout=15,
        )
        return result.stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def deny(self, base, head, reason):
        with self.assertRaises(PlanError) as caught:
            plan(self.repo, base, head)
        self.assertEqual(str(caught.exception), reason)

    def test_one_static_asset_has_exact_git_bytes(self):
        content = b"<!doctype html><title>Updated</title>"
        self.write("public/index.html", content)
        head = self.commit("UI only")
        result = plan(self.repo, self.base, head)
        self.assertEqual(result["schema"], "zcloud-dashboard-static-candidate-plan-v1")
        self.assertEqual(result["assets"][0]["path"], "public/index.html")
        self.assertEqual(result["assets"][0]["change"], "M")
        self.assertEqual(result["assets"][0]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["assets"][0]["bytes"], len(content))
        self.assertEqual(result["total_bytes"], len(content))
        self.assertTrue(result["static_diff_eligible"])
        for field in (
            "release_authorized", "promotion_authorized", "baseline_proven_deployed",
            "live_runtime_compatibility_proven", "sqlite_ownership_proven",
            "production_status_proven",
        ):
            self.assertFalse(result[field], field)

    def test_worker_cockpit_enhancement_css_is_allowed_as_static_data(self):
        css = b".worker-cockpit {display: grid}\n"
        self.write("public/enhancements.css", css)
        head = self.commit("static cockpit CSS")
        result = plan(self.repo, self.base, head)
        self.assertEqual([item["path"] for item in result["assets"]],
                         ["public/enhancements.css"])
        self.assertEqual(result["assets"][0]["sha256"], hashlib.sha256(css).hexdigest())
        self.assertTrue(result["static_diff_eligible"])
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["live_runtime_compatibility_proven"])

    def test_multiple_static_assets_and_git_blob_identity(self):
        self.write("public/app.js", b"const version = 1;\n")
        self.write("public/design-system.css", b"body {margin: 0}\n")
        head = self.commit("UI additions")
        result = plan(self.repo, self.base, head)
        self.assertEqual([x["path"] for x in result["assets"]],
                         ["public/app.js", "public/design-system.css"])
        self.assertEqual([x["change"] for x in result["assets"]], ["A", "A"])
        self.assertTrue(all(len(x["git_blob_sha"]) == 40 for x in result["assets"]))
        self.assertFalse(result["promotion_authorized"])

    def test_mixed_static_backend_change_is_denied(self):
        self.write("public/index.html", b"new")
        self.write("server.py", b"print('changed backend')\n")
        head = self.commit("unsafe mixed candidate")
        self.deny(self.base, head, "non_static_or_deleted_change")

    def test_userscript_is_not_a_passive_dashboard_asset(self):
        self.write("public/zcloud-worker.user.js", b"// browser automation changed\n")
        head = self.commit("dangerous user script change")
        self.deny(self.base, head, "non_static_or_deleted_change")

    def test_asset_deletion_is_denied(self):
        (self.repo / "public" / "index.html").unlink()
        head = self.commit("delete dashboard")
        self.deny(self.base, head, "non_static_or_deleted_change")

    def test_existing_asset_changed_to_symlink_is_denied(self):
        (self.repo / "public" / "index.html").unlink()
        (self.repo / "public" / "index.html").symlink_to("../server.py")
        head = self.commit("symlink dashboard")
        # git --name-status identifies a blob-to-symlink type change as T.
        # This fails even earlier, before the Git-tree mode check.
        self.deny(self.base, head, "non_static_or_deleted_change")

    def test_newly_added_symlink_fails_git_tree_mode_validation(self):
        (self.repo / "public" / "app.js").symlink_to("../server.py")
        head = self.commit("add symlink dashboard app")
        self.deny(self.base, head, "non_regular_dashboard_asset")

    def test_non_utf8_and_nul_static_payloads_are_denied(self):
        for payload, reason in (
            (b"broken\xff", "non_utf8_static_asset"),
            (b"text\x00hidden", "untrusted_static_asset_bytes"),
        ):
            with self.subTest(payload=repr(payload)):
                self.write("public/index.html", payload)
                head = self.commit("invalid bytes")
                self.deny(self.base, head, reason)

    def test_static_asset_size_budget_is_enforced(self):
        self.write("public/app.js", b"a" * (3 * 1024 * 1024 + 1))
        head = self.commit("oversize static")
        self.deny(self.base, head, "static_byte_budget_exceeded")

    def test_candidate_must_be_exact_checkout_head(self):
        self.write("public/index.html", b"first")
        first = self.commit("first")
        self.write("public/index.html", b"second")
        self.commit("second")
        self.deny(self.base, first, "candidate_does_not_equal_checked_out_head")

    def test_invalid_or_nonexistent_sha_fails_closed(self):
        self.write("public/index.html", b"new")
        head = self.commit("after")
        self.deny("Z" * 40, head, "invalid_commit_sha")
        self.deny("0" * 40, head, "git_identity_or_object_unavailable")
        self.deny(self.base, "not-a-sha", "invalid_commit_sha")

    def test_no_delta_and_unrelated_branch_denied(self):
        self.deny(self.base, self.base, "no_commit_delta")
        self.git("checkout", "-q", "--orphan", "other")
        self.git("rm", "-rfq", ".")
        self.write("public/index.html", b"new unrelated branch")
        unrelated = self.commit("unrelated")
        self.deny(self.base, unrelated, "baseline_not_ancestor")

    def test_uncommitted_working_tree_bytes_cannot_contaminate_manifest(self):
        self.write("public/app.js", b"committed content\n")
        head = self.commit("commit")
        expected = plan(self.repo, self.base, head)
        self.write("public/app.js", b"uncommitted modified content\n")
        before = self.git("status", "--porcelain")
        observed = plan(self.repo, self.base, head)
        after = self.git("status", "--porcelain")
        self.assertEqual(before, after)
        self.assertEqual(expected, observed)

    def test_cli_json_never_grants_promotion_even_on_success(self):
        self.write("public/index.html", b"compatible-looking")
        head = self.commit("UI")
        good = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--baseline-sha", self.base, "--candidate-sha", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        body = json.loads(good.stdout)
        self.assertTrue(body["static_diff_eligible"])
        self.assertFalse(body["promotion_authorized"])
        self.assertFalse(body["release_authorized"])
        bad = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--baseline-sha", head, "--candidate-sha", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(bad.returncode, 2)
        body = json.loads(bad.stdout)
        self.assertEqual(body["reason"], "no_commit_delta")
        self.assertFalse(body["promotion_authorized"])


if __name__ == "__main__":
    unittest.main()
