"""Offline real-Git regression coverage for dashboard cache-bust monotonic policy."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_dashboard_cache_bust_gate_20261015.py"
spec = importlib.util.spec_from_file_location("cache_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(gate)


def run(*args: str) -> str:
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()


class GitAssetCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        run("init", "-q")
        run("config", "user.email", "cache@example.invalid")
        run("config", "user.name", "Cache Tests")
        self.public = Path("public")
        self.public.mkdir()
        self.write("public/app.js", "console.log(1);")
        self.write("public/enhancements.js", "console.log(101);")
        self.write("public/enhancements.css", "body{margin:0}")
        self.write("public/index.html", self.html("r8", "r5"))
        self.base = self.commit()
        self.head = self.base

    @staticmethod
    def html(js: str, css: str, enhancements: str = "r2") -> str:
        return ('<link rel="stylesheet" href="/enhancements.css?v=' + css + '">' +
                '<script src="/app.js?v=' + js + '"></script>' +
                '<script src="/enhancements.js?v=' + enhancements + '"></script>')

    def write(self, path: str, value: str) -> None:
        Path(path).write_text(value, encoding="utf-8")

    def commit(self) -> str:
        run("add", "-A")
        run("commit", "-q", "-m", "fixture")
        return run("rev-parse", "HEAD")

    def test_unchanged_legacy_versions_are_allowed(self) -> None:
        result = gate.evaluate(self.base, self.head)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], [])
        self.assertFalse(result["release_authorized"])

    def test_direct_main_push_missing_cache_bump_reproduces_failure(self) -> None:
        # Push-event SHAs are old-main and new-main, not a synthetic PR merge.
        before = self.base
        self.write("public/app.js", "console.log(4242);")
        after = self.commit()
        self.assertEqual(run("merge-base", before, after), before)
        decision = gate.evaluate(before, after)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason"], "changed_asset_pin_unchanged")
        self.assertEqual(decision["changed"], ["public/app.js"])
        self.assertEqual(decision["asset_versions"]["public/app.js"]["after"], "r8")

    def test_direct_main_push_with_correct_cache_bump_allowed(self) -> None:
        before = self.base
        self.write("public/enhancements.css", "body{margin:3px}")
        self.write("public/index.html", self.html("r8", "r6"))
        after = self.commit()
        decision = gate.evaluate(before, after)
        self.assertTrue(decision["ok"])
        self.assertFalse(decision["release_authorized"])

    def test_direct_push_unrelated_history_fails_continuity(self) -> None:
        # A direct force-push is not a legitimate incremental cache-check
        # baseline: its old-main SHA is not an ancestor of new main.
        old_main = self.base
        run("checkout", "--orphan", "unrelated-root")
        self.write("public/app.js", "console.log('independent');")
        new_main = self.commit()
        attempt = subprocess.run(
            ["git", "merge-base", "--is-ancestor", old_main, new_main],
            check=False, capture_output=True, timeout=10,
        )
        self.assertNotEqual(attempt.returncode, 0)
        self.assertFalse(gate.evaluate(old_main, new_main)["ok"])

    def test_changed_js_without_bump_denied(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.head = self.commit()
        result = gate.evaluate(self.base, self.head)
        self.assertEqual(result["reason"], "changed_asset_pin_unchanged")
        self.assertEqual(result["unbumped"], ["public/app.js"])
        self.assertEqual(result["asset_versions"]["public/app.js"], {"before": "r8", "after": "r8"})

    def test_changed_enhancements_js_without_bump_denied(self) -> None:
        self.write("public/enhancements.js", "console.log(102);")
        self.head = self.commit()
        report = gate.evaluate(self.base, self.head)
        self.assertEqual(report["unbumped"], ["public/enhancements.js"])
        self.assertEqual(report["asset_versions"]["public/enhancements.js"],
                         {"before": "r2", "after": "r2"})

    def test_changed_enhancements_js_requires_own_revision_bump(self) -> None:
        self.write("public/enhancements.js", "console.log(102);")
        self.write("public/index.html", self.html("r8", "r5", enhancements="r3"))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_enhancements_js_revision_rollback_denied(self) -> None:
        self.write("public/enhancements.js", "console.log(102);")
        self.write("public/index.html", self.html("r8", "r5", enhancements="r1"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["nonmonotonic"],
                         ["public/enhancements.js"])

    def test_enhancements_js_alias_ref_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script src="/enhancements%2Ejs?v=r3"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "noncanonical_asset_path")

    def test_changed_css_without_bump_denied(self) -> None:
        self.write("public/enhancements.css", "body{margin:1px}")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["unbumped"], ["public/enhancements.css"])

    def test_independent_bumps_required(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.write("public/enhancements.css", "body{margin:1px}")
        self.write("public/index.html", self.html("r9", "r5"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["unbumped"], ["public/enhancements.css"])
        self.write("public/index.html", self.html("r9", "r6"))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])


    def test_changed_js_cannot_reuse_older_version(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.write("public/index.html", self.html("r7", "r5"))
        self.head = self.commit()
        report = gate.evaluate(self.base, self.head)
        self.assertEqual(report["reason"], "nonmonotonic_cache_pin")
        self.assertEqual(report["nonmonotonic"], ["public/app.js"])

    def test_changed_css_cannot_rewind_version(self) -> None:
        self.write("public/enhancements.css", "body{color:red}")
        self.write("public/index.html", self.html("r8", "r4"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["nonmonotonic"], ["public/enhancements.css"])

    def test_changed_js_requires_review_for_nonnumeric_token_scheme(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.write("public/index.html", self.html("digestabcd", "r5"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "nonmonotonic_cache_pin")

    def test_changed_js_accepts_strict_numeric_increase(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.write("public/index.html", self.html("r20", "r5"))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_pin_only_downgrade_is_rejected(self) -> None:
        self.write("public/index.html", self.html("r7", "r5"))
        self.head = self.commit()
        result = gate.evaluate(self.base, self.head)
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["reason"], "nonmonotonic_cache_pin")
        self.assertEqual(result["nonmonotonic"], ["public/app.js"])

    def test_pin_only_change_allowed(self) -> None:
        self.write("public/index.html", self.html("r9", "r6"))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_source_can_be_reverted_without_extra_bump(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.commit()
        self.write("public/app.js", "console.log(1);")
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_duplicate_script_ref_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") + '<script src="/app.js?v=r8"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "missing_or_duplicate_asset_ref")

    def test_dynamic_pin_denied(self) -> None:
        self.write("public/index.html", self.html("${{ secrets.ABC }}", "r5"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "invalid_cache_pin")

    def test_duplicate_html_attribute_denied(self) -> None:
        self.write("public/index.html", '<script src="/app.js?v=r8" src="/app.js?v=r9"></script>' +
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "duplicate_html_attribute")


    def test_template_slash_cannot_promote_inert_script_to_live_cache_pin(self) -> None:
        self.write("public/app.js", "console.log(55);")
        self.write("public/index.html",
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">' +
                   '<script src="/enhancements.js?v=r2"></script>' +
                   '<template/><script src="/app.js?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "self_closing_nonvoid_markup")

    def test_noscript_slash_fails_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") + "<noscript/>")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "self_closing_nonvoid_markup")

    def test_unclosed_js_tag_does_not_count_as_active_cache_evidence(self) -> None:
        # HTMLParser accepts this token, but browser execution is unresolved
        # without a matching </script>. Do not authorize a stale-cache waiver.
        self.write("public/app.js", "console.log(9001);")
        self.write("public/index.html",
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">' +
                   '<script src="/enhancements.js?v=r2"></script>' +
                   '<script src="/app.js?v=r9">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "unclosed_script_markup")

    def test_stray_script_closer_after_valid_asset_refs_fails_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") + "</script>")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "unbalanced_script_markup")

    def test_benign_unclosed_inline_script_fails_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script>document.title = "example"')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "unclosed_script_markup")

    def test_balanced_inline_script_remains_valid(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script>document.title = "example"</script>')
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_script_slash_fails_closed(self) -> None:
        self.write("public/index.html",
                   self.html("r8", "r5") + '<script src="/app.js?v=r9"/>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "self_closing_nonvoid_markup")

    def test_link_void_selfclosing_does_not_create_false_alarm(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            '/enhancements.css?v=r5">', '/enhancements.css?v=r5"/>'))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_mismatched_inert_closer_cannot_produce_fake_live_js_pin(self) -> None:
        # Without a tag stack, </noscript> used to pop an open <template>.
        # The next script appeared live to the gate although browsers kept it inert.
        self.write("public/app.js", "console.log(999);")
        malicious_html = (
            '<link rel="stylesheet" href="/enhancements.css?v=r5">'
            '<script src="/enhancements.js?v=r2"></script>'
            '<template></noscript><script src="/app.js?v=r9"></script></template>'
        )
        self.write("public/index.html", malicious_html)
        self.head = self.commit()
        result = gate.evaluate(self.base, self.head)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unbalanced_inert_markup")

    def test_stray_inert_close_after_complete_valid_refs_fails_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") + "</template>")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "unbalanced_inert_markup")

    def test_unclosed_template_after_valid_refs_fails_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") + "<template>inert data")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "unclosed_inert_markup")

    def test_balanced_nested_template_noscript_without_assets_is_allowed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   "<template><noscript><span>nothing cached</span></noscript></template>")
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_improper_crossed_inert_closers_fail(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   "<template><noscript></template></noscript>")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "unbalanced_inert_markup")

    def test_template_duplicate_attributes_fail_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<template id="first" id="second"></template>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "duplicate_html_attribute")

    def test_noscript_duplicate_attributes_fail_closed(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<noscript id="first" ID="second"></noscript>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "duplicate_html_attribute")

    def test_inert_template_ref_never_counts_as_live_script(self) -> None:
        self.write("public/index.html", '<template><script src="/app.js?v=r9"></script></template>' +
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "inert_asset_ref")

    def test_json_script_ref_rejected(self) -> None:
        self.write("public/index.html", '<script type="application/json" src="/app.js?v=r9"></script>' +
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "inactive_script_asset_ref")

    def test_nomodule_script_cannot_replace_active_js_ref(self) -> None:
        self.write("public/app.js", "console.log(42);")
        self.write("public/index.html",
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">' +
                   '<script src="/app.js?v=r9" nomodule></script>' +
                   '<script src="/enhancements.js?v=r2"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "conditional_script_asset_ref")

    def test_print_media_stylesheet_cannot_replace_active_css_ref(self) -> None:
        self.write("public/enhancements.css", "body{display:none}")
        self.write("public/index.html",
                   '<link rel="stylesheet" media="print" href="/enhancements.css?v=r6">' +
                   '<script src="/app.js?v=r8"></script>' +
                   '<script src="/enhancements.js?v=r2"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "conditional_stylesheet_asset_ref")

    def test_screen_only_css_requires_separate_review(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet" media="screen"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "conditional_stylesheet_asset_ref")

    def test_explicit_all_media_css_is_active(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet" media="all"'))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_alternate_css_does_not_stand_in_for_active_stylesheet(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="alternate stylesheet"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "alternate_stylesheet_asset_ref")

    def test_alternate_css_reversed_rel_tokens_rejected(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet alternate"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "alternate_stylesheet_asset_ref")

    def test_non_css_mime_type_does_not_count_as_active_link(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet" type="text/plain"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "inactive_stylesheet_type")

    def test_explicit_css_mime_type_is_valid(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet" type="text/css"'))
        self.head = self.commit()
        self.assertTrue(gate.evaluate(self.base, self.head)["ok"])

    def test_titled_css_does_not_replace_unconditional_stylesheet(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'rel="stylesheet"', 'rel="stylesheet" title="Night"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "conditional_stylesheet_asset_ref")

    def test_source_only_js_sri_claim_needs_independent_review(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            '<script src="/app.js?v=r8"',
            '<script src="/app.js?v=r8" integrity="sha256-invalid"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "unverified_integrity_asset_ref")

    def test_source_only_css_sri_claim_needs_independent_review(self) -> None:
        self.write("public/index.html", self.html("r8", "r5").replace(
            'href="/enhancements.css?v=r5"',
            'href="/enhancements.css?v=r5" integrity="sha256-invalid"'))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "unverified_integrity_asset_ref")

    def test_disabled_stylesheet_ref_rejected(self) -> None:
        self.write("public/index.html", '<script src="/app.js?v=r8"></script>' +
                   '<link rel="stylesheet" disabled href="/enhancements.css?v=r6">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "disabled_stylesheet_asset_ref")

    def test_noscript_ref_does_not_count_as_active(self) -> None:
        self.write("public/index.html", '<noscript><script src="/app.js?v=r9"></script></noscript>' +
                   '<link rel="stylesheet" href="/enhancements.css?v=r5">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "inert_asset_ref")

    def test_external_base_origin_denied(self) -> None:
        self.write("public/index.html", '<base href="https://example.invalid/">' + self.html("r8", "r5"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "base_href_changes_asset_origin")

    def test_relative_base_origin_also_requires_review(self) -> None:
        self.write("public/index.html", '<base href="/">' + self.html("r8", "r5"))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "base_href_changes_asset_origin")

    def test_browser_backslash_js_ref_denied(self) -> None:
        # Browsers using HTTP(S) normalize backslashes to forward slashes.
        alias = chr(92) + "app.js"
        self.write("public/index.html", self.html("r8", "r5") +
                   f'<script src="{alias}?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "noncanonical_asset_path")

    def test_browser_backslash_js_traversal_denied(self) -> None:
        alias = "/scratch" + chr(92) + ".." + chr(92) + "app.js"
        self.write("public/index.html", self.html("r8", "r5") +
                   f'<script src="{alias}?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "noncanonical_asset_path")

    def test_percent_encoded_backslash_js_ref_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script src="%5Capp.js?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "noncanonical_asset_path")

    def test_percent_encoded_backslash_css_traversal_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<link rel="stylesheet" href="/tmp%5C..%5Cenhancements.css?v=r9">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"],
                         "noncanonical_asset_path")

    def test_percent_encoded_js_alias_cannot_hide_second_asset(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script src="/app%2Ejs?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "noncanonical_asset_path")

    def test_percent_encoded_css_alias_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<link rel="stylesheet" href="/enhancements%2Ecss?v=r6">')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "noncanonical_asset_path")

    def test_dot_segment_js_alias_cannot_hide_second_asset(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script src="/ignored/../app.js?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "noncanonical_asset_path")

    def test_untrusted_non_utf8_percent_encoded_asset_denied(self) -> None:
        self.write("public/index.html", self.html("r8", "r5") +
                   '<script src="/app%FF.js?v=r9"></script>')
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "malformed_asset_path")

    def test_symlinked_asset_denied(self) -> None:
        Path("public/app.js").unlink()
        Path("public/app.js").symlink_to("enhancements.css")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "nonregular_git_asset")

    def test_deleted_asset_denied(self) -> None:
        Path("public/app.js").unlink()
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "missing_or_ambiguous_git_path")

    def test_binary_asset_denied(self) -> None:
        Path("public/app.js").write_bytes(b"a\x00b")
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "invalid_asset_content")

    def test_large_asset_denied(self) -> None:
        Path("public/app.js").write_bytes(b"a" * (gate.MAX_FILE + 1))
        self.head = self.commit()
        self.assertEqual(gate.evaluate(self.base, self.head)["reason"], "oversize_asset")

    def test_invalid_sha_never_reads_git(self) -> None:
        self.assertEqual(gate.evaluate("main", self.head)["reason"], "invalid_sha")
        self.assertEqual(gate.evaluate(self.base, "bad")["reason"], "invalid_sha")

    def test_immutable_base_scanner_rejects_forged_green_pr_head(self) -> None:
        # A candidate PR may replace its own scanner with an always-green stub.
        # CI must execute the policy from base before head-supplied test code.
        scanner = Path("scripts/zcloud_dashboard_cache_bust_gate_20261015.py")
        scanner.parent.mkdir()
        scanner.write_bytes(SCRIPT.read_bytes())
        trusted_base = self.commit()
        self.write("public/app.js", "console.log(2);")
        scanner.write_text("raise SystemExit(0)\n", encoding="utf-8")
        malicious_head = self.commit()
        self.assertEqual(
            subprocess.run(["python3", "-I", str(scanner)], capture_output=True,
                           text=True, check=False, timeout=10).returncode, 0,
        )
        base_scanner = run("show", f"{trusted_base}:scripts/zcloud_dashboard_cache_bust_gate_20261015.py")
        executable = Path(self.tmp.name) / "trusted-base-policy.py"
        executable.write_text(base_scanner, encoding="utf-8")
        result = subprocess.run(
            ["python3", "-I", str(executable), "--base-sha", trusted_base,
             "--head-sha", malicious_head],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        verdict = json.loads(result.stdout)
        self.assertEqual(verdict["reason"], "changed_asset_pin_unchanged")
        self.assertFalse(verdict["release_authorized"])

    def test_no_repository_mutation(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.head = self.commit()
        before = run("status", "--porcelain")
        report = gate.evaluate(self.base, self.head)
        self.assertFalse(report["ok"])
        self.assertEqual(run("status", "--porcelain"), before)
        self.assertFalse(report["mutation_performed"])
        self.assertFalse(report["live_bytes_observed"])



class WorkflowTrustCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (SCRIPT.parents[1] / ".github/workflows/zcloud-dashboard-cache-bust-gate-20261015.yml").read_text(encoding="utf-8")

    def test_immutable_base_policy_has_precedence(self) -> None:
        data = self.workflow
        self.assertIn('git show "$BASE_SHA:$policy_path" > "$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', data)
        self.assertIn('policy_path="$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', data)
        self.assertIn('python3 -I "$policy_path" --base-sha "$BASE_SHA"', data)
        self.assertLess(data.index('policy_path="$RUNNER_TEMP/trusted-dashboard-cache-gate.py"'),
                        data.index('python3 -I "$policy_path" --base-sha "$BASE_SHA"'))
        self.assertIn('test "$mode" = "100644 blob"', data)

    def test_trusted_policy_runs_before_any_head_test_code(self) -> None:
        self.assertLess(
            self.workflow.index("name: Immutable trusted-base scanner first"),
            self.workflow.index("name: Run exact-asset Git regression fixtures"),
        )
        self.assertIn('policy_path="$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', self.workflow)
        self.assertIn('python3 -I "$policy_path" --base-sha "$BASE_SHA"', self.workflow)

    def test_initial_policy_install_warns_untrusted(self) -> None:
        self.assertIn('provenance="untrusted_bootstrap_review_required"', self.workflow)
        self.assertIn('echo "::warning::POLICY_PROVENANCE=$provenance"', self.workflow)
        self.assertIn('python3 -I "$policy_path"', self.workflow)
        self.assertNotIn('POLICY_PROVENANCE=trusted_bootstrap', self.workflow)

    def test_policy_bootstrap_requires_actual_absence_in_exact_base_tree(self) -> None:
        policy = self.workflow
        self.assertIn('policy_entry="$(git ls-tree "$BASE_SHA" -- "$policy_path")"', policy)
        self.assertIn('if [ -n "$policy_entry" ]; then', policy)
        self.assertIn('test "$mode" = "100644 blob"', policy)
        self.assertNotIn('if git cat-file -e "$BASE_SHA:$policy_path"', policy)
        self.assertLess(policy.index('policy_entry="$(git ls-tree "$BASE_SHA"'),
                        policy.index('provenance="immutable_pr_base"'))
        self.assertIn('git show "$BASE_SHA:$policy_path" > "$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', policy)

    def test_forged_head_policy_does_not_affect_bootstrap_decision(self) -> None:
        # Main source absent and candidate PR source present is still a bootstrap,
        # never a claimed immutable-pr-base policy.
        with tempfile.TemporaryDirectory() as workdir:
            subprocess.run(["git", "init", "-q", workdir], check=True)
            subprocess.run(["git", "-C", workdir, "config", "user.email", "bot@example.invalid"], check=True)
            subprocess.run(["git", "-C", workdir, "config", "user.name", "Bot"], check=True)
            (Path(workdir) / "README.md").write_text("base", encoding="utf-8")
            subprocess.run(["git", "-C", workdir, "add", "."], check=True)
            subprocess.run(["git", "-C", workdir, "commit", "-qm", "base"], check=True)
            base = subprocess.check_output(["git", "-C", workdir, "rev-parse", "HEAD"], text=True).strip()
            path = "scripts/zcloud_dashboard_cache_bust_gate_20261015.py"
            tree_path = Path(workdir) / path
            tree_path.parent.mkdir()
            tree_path.write_text("raise SystemExit(0)\\n", encoding="utf-8")
            subprocess.run(["git", "-C", workdir, "add", "."], check=True)
            subprocess.run(["git", "-C", workdir, "commit", "-qm", "candidate"], check=True)
            head = subprocess.check_output(["git", "-C", workdir, "rev-parse", "HEAD"], text=True).strip()
            def entry(revision: str) -> str:
                return subprocess.check_output(
                    ["git", "-C", workdir, "ls-tree", revision, "--", path],
                    text=True,
                ).strip()
            self.assertEqual(entry(base), "")
            self.assertIn("100644 blob", entry(head))

    def test_cache_report_is_limited_before_cat_and_renderer(self) -> None:
        text = self.workflow
        self.assertIn('timeout 90 python3 -I "$policy_path"', text)
        self.assertIn('test -s "$report"', text)
        self.assertIn('test "$(wc -c < "$report")" -le 16384', text)
        self.assertLess(text.index('test "$(wc -c < "$report")" -le 16384'),
                        text.index('cat "$report"'))
        self.assertLess(text.index('cat "$report"'),
                        text.index('REPORT="$report" PROVENANCE="$provenance" python3'))

    def test_current_main_is_rechecked_through_canonical_remote_twice(self) -> None:
        url = "git -C / ls-remote --exit-code https://github.com/Zennay/zCloud.git refs/heads/main"
        self.assertEqual(self.workflow.count(url), 2)
        self.assertEqual(self.workflow.count("env -i PATH=/usr/bin:/bin HOME=/nonexistent"), 2)
        self.assertEqual(self.workflow.count("GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null"), 2)
        self.assertEqual(self.workflow.count('test "$current_main" = "$EXPECTED_MAIN"'), 2)
        self.assertNotIn("git ls-remote origin refs/heads/main", self.workflow)
        self.assertLess(
            self.workflow.index("name: Immutable trusted-base scanner first"),
            self.workflow.index("name: Recheck immutable current main"),
        )
        self.assertLess(
            self.workflow.index("name: Run exact-asset Git regression fixtures"),
            self.workflow.index("name: Recheck immutable current main"),
        )

    def test_hosted_main_push_covers_existing_unprotected_main_path(self) -> None:
        # A direct push cannot be blocked retroactively, but must produce
        # a real hosted red check for unbumped assets after integration.
        workflow = self.workflow
        self.assertIn('  push:\n    branches: [main]', workflow)
        self.assertEqual(workflow.count('      - "public/app.js"'), 2)
        self.assertEqual(workflow.count('      - "public/enhancements.css"'), 2)
        self.assertEqual(workflow.count('      - "public/enhancements.js"'), 2)
        self.assertEqual(workflow.count('      - "public/index.html"'), 2)
        self.assertIn('github.event.pull_request.base.sha || github.event.before', workflow)
        self.assertIn('github.event.pull_request.head.sha || github.sha', workflow)
        self.assertIn('git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"', workflow)

    def test_only_exact_main_push_or_same_repo_pr_is_admitted(self) -> None:
        workflow = self.workflow
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', workflow)
        self.assertIn('test "$GITHUB_SHA" = "$HEAD_SHA"', workflow)
        self.assertIn('test "$HEAD_REPOSITORY" = "$GITHUB_REPOSITORY"', workflow)
        self.assertIn('case "$GITHUB_EVENT_NAME" in', workflow)
        self.assertIn('*) exit 1 ;;', workflow)
        self.assertIn('EXPECTED_MAIN="$HEAD_SHA"', workflow)
        self.assertIn('EXPECTED_MAIN="$BASE_SHA"', workflow)
        self.assertIn('test "$current_main" = "$EXPECTED_MAIN"', workflow)

    def test_push_history_drift_guard_exists(self) -> None:
        self.assertIn('git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"', self.workflow)
        self.assertIn('EXPECTED_MAIN="$HEAD_SHA"', self.workflow)
        self.assertIn('test "$current_main" = "$EXPECTED_MAIN"', self.workflow)

    def run_bounded_summary(self, payload: dict, *, event: str = "push") -> tuple[int, str]:
        script = self.workflow.split('REPORT="$report" PROVENANCE="$provenance" python3 - <<\'PY\'', 1)[1]
        script = script.split("\n          PY", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            summary = Path(folder) / "summary.md"
            report.write_text(json.dumps(payload), encoding="utf-8")
            env = dict(os.environ, REPORT=str(report), GITHUB_STEP_SUMMARY=str(summary),
                       PROVENANCE="untrusted_bootstrap_review_required", GITHUB_EVENT_NAME=event)
            result = subprocess.run(
                ["python3", "-I", "-c", script], cwd=folder, env=env,
                capture_output=True, text=True, check=False, timeout=10,
            )
            content = summary.read_text(encoding="utf-8") if summary.exists() else ""
            return result.returncode, content

    def test_direct_push_renders_bounded_human_friendly_summary(self) -> None:
        code, content = self.run_bounded_summary(
            {"ok": False, "reason": "changed_asset_pin_unchanged", "changed": ["public/app.js"]},
        )
        self.assertEqual(code, 0)
        self.assertIn("Directe wijziging op main", content)
        self.assertIn("AFGEWEZEN", content)
        self.assertIn("Dashboard JavaScript", content)
        self.assertIn("geen toestemming voor deploy", content)
        self.assertNotIn("<script", content)

    def test_pr_success_renders_expected_summary(self) -> None:
        code, content = self.run_bounded_summary(
            {"ok": True, "reason": "cache_bust_delta_ok", "changed": [],
             "asset_versions": {"public/app.js": {"before": "r8", "after": "r9"},
                                "public/enhancements.js": {"before": "r2", "after": "r3"},
                                "public/enhancements.css": {"before": "r5", "after": "r6"}}},
            event="pull_request",
        )
        self.assertEqual(code, 0)
        self.assertIn("GESLAAGD", content)
        self.assertIn("Pull request", content)
        self.assertIn("Geen bronwijziging", content)
        self.assertIn("r8 → r9", content)
        self.assertIn("r5 → r6", content)
        self.assertIn("r2 → r3", content)

    def test_malformed_summary_report_fails_closed(self) -> None:
        for invalid in (
            {"ok": 1, "reason": "cache_bust_delta_ok", "changed": []},
            {"ok": True, "reason": "cache_bust_delta_ok", "changed": ["<img src=x>"]},
            {"ok": True, "reason": "cache_bust_delta_ok", "changed": "public/app.js"},
            {"ok": True, "reason": "cache_bust_delta_ok", "changed": [],
             "asset_versions": {"public/app.js": {"before": "r8", "after": "<script>"}}},
        ):
            with self.subTest(invalid=invalid):
                code, content = self.run_bounded_summary(invalid)
                self.assertNotEqual(code, 0)
                self.assertEqual(content, "")

    def test_policy_failure_status_is_preserved_after_summary(self) -> None:
        self.assertIn('test "$policy_result" -eq 0', self.workflow)
        self.assertIn('|| policy_result=$?', self.workflow)
        self.assertIn('if type(payload.get("ok")) is not bool:', self.workflow)

    def test_hosted_read_only_and_no_automatic_deploy(self) -> None:
        self.assertIn('runs-on: ubuntu-latest', self.workflow)
        self.assertIn('contents: read', self.workflow)
        self.assertIn('persist-credentials: false', self.workflow)
        self.assertNotIn('self-hosted', self.workflow)
        self.assertNotIn('workflow_dispatch:', self.workflow)
        self.assertNotIn('pull_request_target:', self.workflow)
        self.assertNotIn('contents: write', self.workflow)

if __name__ == "__main__":
    unittest.main()
