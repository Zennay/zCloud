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
    PlanError, plan, snapshot, verify_cache_bust,
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

    def pinned_index(self, app="/app.js?v=r8", css="/enhancements.css?v=r5"):
        return (
            '<!doctype html><html><head>'
            '<link rel="stylesheet" href="' + css + '">'
            '<script src="' + app + '" defer></script>'
            '</head><body></body></html>'
        ).encode("utf-8")

    def write_snapshot_bundle(self):
        self.write("public/app.js", b"'use strict'; const cockpit=1;\n")
        self.write("public/enhancements.css", b".worker-cockpit{display:grid}\n")
        self.write("public/index.html", self.pinned_index())
        return self.commit("static cockpit bundle")

    def test_git_snapshot_has_exact_asset_sha256_and_no_authority(self):
        sha = self.write_snapshot_bundle()
        inventory = snapshot(self.repo, sha)
        self.assertEqual(inventory["schema"], "zcloud-dashboard-git-snapshot-v1")
        self.assertTrue(inventory["index_references_pinned"])
        self.assertEqual(
            inventory["index_asset_versions"],
            {"app.js": "r8", "enhancements.css": "r5"},
        )
        self.assertEqual(inventory["source_revision_sha"], sha)
        self.assertEqual(
            [r["path"] for r in inventory["assets"]],
            ["public/app.js", "public/enhancements.css", "public/index.html"],
        )
        self.assertEqual(
            inventory["total_bytes"],
            sum(item["bytes"] for item in inventory["assets"]),
        )
        expected = {
            "public/index.html": self.pinned_index(),
            "public/app.js": b"'use strict'; const cockpit=1;\n",
            "public/enhancements.css": b".worker-cockpit{display:grid}\n",
        }
        for asset in inventory["assets"]:
            with self.subTest(path=asset["path"]):
                self.assertEqual(asset["sha256"], hashlib.sha256(expected[asset["path"]]).hexdigest())
                self.assertEqual(asset["bytes"], len(expected[asset["path"]]))
                self.assertEqual(len(asset["git_blob_sha"]), 40)
        self.assertTrue(inventory["source_bytes_only"])
        for key in (
            "runtime_bytes_observed", "baseline_proven_deployed",
            "live_runtime_compatibility_proven", "sqlite_ownership_proven",
            "production_status_proven", "release_authorized", "promotion_authorized",
        ):
            self.assertFalse(inventory[key], key)

    def test_snapshot_is_immune_to_modified_working_tree(self):
        head = self.write_snapshot_bundle()
        immutable = snapshot(self.repo, head)
        self.write("public/app.js", b"uncommitted attack bytes\n")
        self.assertEqual(snapshot(self.repo, head), immutable)

    def test_snapshot_requires_complete_three_asset_bundle(self):
        with self.assertRaisesRegex(PlanError, "required_dashboard_asset_missing"):
            snapshot(self.repo, self.base)
        self.write("public/app.js", b"present but CSS missing")
        sha = self.commit("partial cockpit")
        with self.assertRaisesRegex(PlanError, "required_dashboard_asset_missing"):
            snapshot(self.repo, sha)

    def test_snapshot_rejects_new_symlink_and_invalid_text(self):
        self.write("public/app.js", b"normal JS")
        (self.repo / "public" / "enhancements.css").symlink_to("../server.py")
        sha = self.commit("symlink CSS")
        with self.assertRaisesRegex(PlanError, "non_regular_dashboard_asset"):
            snapshot(self.repo, sha)
        (self.repo / "public" / "enhancements.css").unlink()
        self.write("public/enhancements.css", b"not\x00CSS")
        sha = self.commit("NUL css")
        with self.assertRaisesRegex(PlanError, "untrusted_static_asset_bytes"):
            snapshot(self.repo, sha)

    def test_snapshot_rejects_missing_unpinned_or_ambiguous_ui_links(self):
        cases = (
            (b"<html></html>", "missing_index_asset_reference"),
            (self.pinned_index(app="/app.js"), "untrusted_index_asset_reference"),
            (self.pinned_index(css="/enhancements.css"), "untrusted_index_asset_reference"),
            (self.pinned_index(app="/app.js?v=r8&x=1"), "untrusted_index_asset_reference"),
            (self.pinned_index(app="https://cdn.example.invalid/app.js?v=r8"), "untrusted_index_asset_reference"),
            (self.pinned_index(css="../enhancements.css?v=r5"), "untrusted_index_asset_reference"),
            (self.pinned_index(app="/app.js?v=r8") + b'<script src="/app.js?v=r9"></script>',
             "ambiguous_index_asset_reference"),
            (self.pinned_index(css="/style.css"), "missing_index_asset_reference"),
        )
        self.write("public/app.js", b"const app = 1;\n")
        self.write("public/enhancements.css", b"body{margin:0}")
        for i, (html, reason) in enumerate(cases):
            with self.subTest(case=i):
                self.write("public/index.html", html)
                head = self.commit(f"bad pin {i}")
                with self.assertRaisesRegex(PlanError, reason):
                    snapshot(self.repo, head)

    def test_snapshot_rejects_malformed_asset_url_as_policy_denial(self):
        self.write("public/app.js", b"const app=1")
        self.write("public/enhancements.css", b"body{}")
        self.write("public/index.html",
                   self.pinned_index(app="http://[invalid/app.js?v=r8"))
        sha = self.commit("malformed external app URL")
        with self.assertRaisesRegex(PlanError, "malformed_index_asset_reference"):
            snapshot(self.repo, sha)

    def test_snapshot_denies_duplicate_html_attributes(self):
        self.write("public/app.js", b"const app=1")
        self.write("public/enhancements.css", b"body{}")
        self.write("public/index.html", b'<link rel="stylesheet" href="/enhancements.css?v=r5">'
                   b'<script src="/app.js?v=r8" src="/app.js?v=r9"></script>')
        head = self.commit("duplicated script src")
        with self.assertRaisesRegex(PlanError, "duplicate_index_attribute"):
            snapshot(self.repo, head)

    def test_snapshot_allows_new_valid_asset_versions_without_hardcoding(self):
        self.write("public/app.js", b"const app=1")
        self.write("public/enhancements.css", b"body{}")
        self.write("public/index.html", self.pinned_index(app="/app.js?v=build-17",
                                                          css="/enhancements.css?v=v6"))
        head = self.commit("cache bump")
        evidence = snapshot(self.repo, head)
        self.assertEqual(evidence["index_asset_versions"],
                         {"app.js": "build-17", "enhancements.css": "v6"})
        self.assertFalse(evidence["promotion_authorized"])

    def test_snapshot_rejects_stale_or_invalid_git_ref(self):
        head = self.write_snapshot_bundle()
        self.write("public/index.html", b"another revision")
        self.commit("main moved")
        with self.assertRaisesRegex(PlanError, "candidate_does_not_equal_checked_out_head"):
            snapshot(self.repo, head)
        with self.assertRaisesRegex(PlanError, "invalid_commit_sha"):
            snapshot(self.repo, "deadbeef")

    def test_snapshot_refuses_inert_template_and_noscript_references(self):
        cases = (
            (b'<template><script src="/app.js?v=r8"></script></template>'
             b'<link rel="stylesheet" href="/enhancements.css?v=r5">',
             "inactive_index_asset_reference"),
            (b'<script src="/app.js?v=r8"></script>'
             b'<noscript><link rel="stylesheet" href="/enhancements.css?v=r5"></noscript>',
             "inactive_index_asset_reference"),
        )
        self.write("public/app.js", b"console.log('active')")
        self.write("public/enhancements.css", b"body{}")
        for i, (html, reason) in enumerate(cases):
            with self.subTest(i=i):
                self.write("public/index.html", html)
                head = self.commit(f"inert fixture {i}")
                with self.assertRaisesRegex(PlanError, reason):
                    snapshot(self.repo, head)

    def test_snapshot_refuses_non_executable_app_script_tags(self):
        cases = (
            '<script src="/app.js?v=r8" type="application/json"></script>',
            '<script src="/app.js?v=r8" nomodule></script>',
            '<script src="/app.js?v=r8" type="importmap"></script>',
        )
        self.write("public/app.js", b"console.log('active')")
        self.write("public/enhancements.css", b"body{}")
        for i, tag in enumerate(cases):
            with self.subTest(i=i):
                self.write("public/index.html",
                           (tag + '<link rel="stylesheet" href="/enhancements.css?v=r5">').encode())
                head = self.commit(f"inert script type {i}")
                with self.assertRaisesRegex(PlanError, "inactive_index_asset_reference"):
                    snapshot(self.repo, head)

    def test_snapshot_refuses_inactive_stylesheets(self):
        cases = (
            '<link rel="stylesheet" href="/enhancements.css?v=r5" media="print">',
            '<link rel="stylesheet" href="/enhancements.css?v=r5" disabled>',
            '<link rel="alternate stylesheet" href="/enhancements.css?v=r5">',
        )
        self.write("public/app.js", b"console.log('active')")
        self.write("public/enhancements.css", b"body{}")
        for i, tag in enumerate(cases):
            with self.subTest(i=i):
                self.write("public/index.html",
                           (tag + '<script src="/app.js?v=r8"></script>').encode())
                head = self.commit(f"inactive CSS {i}")
                with self.assertRaisesRegex(PlanError, "inactive_index_asset_reference"):
                    snapshot(self.repo, head)

    def test_snapshot_refuses_unclosed_inert_template(self):
        self.write("public/app.js", b"console.log('active')")
        self.write("public/enhancements.css", b"body{}")
        self.write("public/index.html",
                   b'<template><script src="/app.js?v=r8"></script>'
                   b'<link rel="stylesheet" href="/enhancements.css?v=r5">')
        head = self.commit("unterminated template")
        with self.assertRaisesRegex(PlanError, "malformed_inert_index_structure"):
            snapshot(self.repo, head)

    def test_snapshot_accepts_active_module_script_and_screen_stylesheet(self):
        self.write("public/app.js", b"export const active=1")
        self.write("public/enhancements.css", b"body{color:white}")
        self.write("public/index.html",
                   b'<script type="module" src="/app.js?v=v11"></script>'
                   b'<link rel="stylesheet" media="screen" href="/enhancements.css?v=dark-7">')
        head = self.commit("active modern assets")
        evidence = snapshot(self.repo, head)
        self.assertEqual(evidence["index_asset_versions"],
                         {"app.js": "v11", "enhancements.css": "dark-7"})
        self.assertFalse(evidence["release_authorized"])

    def build_cache_baseline(self):
        self.write("public/index.html", self.pinned_index())
        self.write("public/app.js", b"const first=1;\n")
        self.write("public/enhancements.css", b".layout{display:grid}")
        return self.commit("complete pinned baseline")

    def test_cache_contract_refuses_changed_app_without_version_bump(self):
        base = self.build_cache_baseline()
        self.write("public/app.js", b"const second=2;\n")
        head = self.commit("app changes but old cache pin")
        self.assertTrue(plan(self.repo, base, head)["static_diff_eligible"])
        with self.assertRaisesRegex(PlanError, "cache_revision_not_bumped"):
            verify_cache_bust(self.repo, base, head)

    def test_cache_contract_refuses_changed_css_without_version_bump(self):
        base = self.build_cache_baseline()
        self.write("public/enhancements.css", b".layout{display:flex}")
        head = self.commit("CSS changed with stale version")
        with self.assertRaisesRegex(PlanError, "cache_revision_not_bumped"):
            verify_cache_bust(self.repo, base, head)

    def test_cache_contract_requires_bump_for_every_changed_asset(self):
        base = self.build_cache_baseline()
        self.write("public/app.js", b"const second=2;\n")
        self.write("public/enhancements.css", b".layout{display:flex}")
        self.write("public/index.html", self.pinned_index(app="/app.js?v=r9"))
        head = self.commit("only JS pin bumped")
        with self.assertRaisesRegex(PlanError, "cache_revision_not_bumped"):
            verify_cache_bust(self.repo, base, head)

    def test_cache_contract_both_bumped_remains_non_authorizing(self):
        base = self.build_cache_baseline()
        self.write("public/app.js", b"const second=2;\n")
        self.write("public/enhancements.css", b".layout{display:flex}")
        self.write("public/index.html", self.pinned_index(
            app="/app.js?v=r9", css="/enhancements.css?v=r6"))
        head = self.commit("both assets changed and independently versioned")
        result = verify_cache_bust(self.repo, base, head)
        self.assertTrue(result["cache_bust_checked"])
        self.assertTrue(result["cache_bust_verified"])
        self.assertEqual(result["baseline_index_asset_versions"],
                         {"app.js": "r8", "enhancements.css": "r5"})
        self.assertEqual(result["candidate_index_asset_versions"],
                         {"app.js": "r9", "enhancements.css": "r6"})
        self.assertFalse(result["runtime_bytes_observed"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["promotion_authorized"])

    def test_cache_contract_missing_or_invalid_baseline_denied(self):
        self.write("public/app.js", b"const first=1")
        self.write("public/enhancements.css", b".first{}")
        missing_refs = self.commit("assets exist but no index refs")
        self.write("public/index.html", self.pinned_index())
        head = self.commit("valid cache refs only in candidate")
        with self.assertRaisesRegex(PlanError, "baseline_index_asset_reference_untrusted"):
            verify_cache_bust(self.repo, missing_refs, head)

    def test_cache_contract_missing_baseline_asset_denied(self):
        self.write("public/index.html", self.pinned_index())
        base = self.commit("baseline mentions absent JS")
        self.write("public/app.js", b"const app=1")
        self.write("public/enhancements.css", b"body{}")
        head = self.commit("adds missing assets")
        with self.assertRaisesRegex(PlanError, "baseline_dashboard_asset_missing"):
            verify_cache_bust(self.repo, base, head)

    def test_cache_contract_index_only_valid_change(self):
        base = self.build_cache_baseline()
        self.write("public/index.html", self.pinned_index(
            app="/app.js?v=r8", css="/enhancements.css?v=r5") + b"<!-- layout -->")
        head = self.commit("index layout comment only")
        out = verify_cache_bust(self.repo, base, head)
        self.assertEqual([x["path"] for x in out["assets"]], ["public/index.html"])
        self.assertTrue(out["cache_bust_verified"])
        self.assertFalse(out["promotion_authorized"])

    def test_cache_contract_cli_mode_json_and_denial(self):
        base = self.build_cache_baseline()
        self.write("public/app.js", b"const second=2;\n")
        self.write("public/index.html", self.pinned_index(app="/app.js?v=r9"))
        head = self.commit("app update pinned")
        accepted = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--baseline-sha", base, "--candidate-sha", head, "--check-cache-bust"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        obj = json.loads(accepted.stdout)
        self.assertTrue(obj["cache_bust_verified"])
        self.assertFalse(obj["release_authorized"])
        denied = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--snapshot-sha", head, "--check-cache-bust"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(denied.returncode, 2, denied.stdout)
        self.assertEqual(json.loads(denied.stdout)["reason"], "incompatible_modes")

    def test_snapshot_cli_never_authorizes_and_modes_are_exclusive(self):
        head = self.write_snapshot_bundle()
        good = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--snapshot-sha", head],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        obj = json.loads(good.stdout)
        self.assertTrue(obj["source_bytes_only"])
        self.assertFalse(obj["runtime_bytes_observed"])
        self.assertFalse(obj["release_authorized"])
        self.assertFalse(obj["promotion_authorized"])
        bad = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo),
             "--snapshot-sha", head, "--baseline-sha", self.base],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(bad.returncode, 2)
        obj = json.loads(bad.stdout)
        self.assertEqual(obj["reason"], "incompatible_modes")
        self.assertFalse(obj["release_authorized"])

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
