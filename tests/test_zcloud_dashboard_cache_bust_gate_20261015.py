"""Offline real-Git regression coverage for dashboard cache-bust monotonic policy."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
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
        self.write("public/enhancements.css", "body{margin:0}")
        self.write("public/index.html", self.html("r8", "r5"))
        self.base = self.commit()
        self.head = self.base

    @staticmethod
    def html(js: str, css: str) -> str:
        return '<link rel="stylesheet" href="/enhancements.css?v=' + css + '">' + (
            '<script src="/app.js?v=' + js + '"></script>'
        )

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

    def test_changed_js_without_bump_denied(self) -> None:
        self.write("public/app.js", "console.log(2);")
        self.head = self.commit()
        result = gate.evaluate(self.base, self.head)
        self.assertEqual(result["reason"], "changed_asset_pin_unchanged")
        self.assertEqual(result["unbumped"], ["public/app.js"])

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
        self.assertIn('python3 -I "$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', data)
        self.assertLess(data.index('POLICY_PROVENANCE=immutable_pr_base'),
                        data.index('POLICY_PROVENANCE=untrusted_bootstrap_review_required'))
        self.assertIn('test "$mode" = "100644 blob"', data)

    def test_trusted_policy_runs_before_any_head_test_code(self) -> None:
        self.assertLess(
            self.workflow.index("name: Immutable trusted-base scanner first"),
            self.workflow.index("name: Run exact-asset Git regression fixtures"),
        )
        self.assertIn('python3 -I "$RUNNER_TEMP/trusted-dashboard-cache-gate.py"', self.workflow)

    def test_initial_policy_install_warns_untrusted(self) -> None:
        self.assertIn('::warning::POLICY_PROVENANCE=untrusted_bootstrap_review_required', self.workflow)
        self.assertIn('python3 -I "$policy_path"', self.workflow)
        self.assertNotIn('POLICY_PROVENANCE=trusted_bootstrap', self.workflow)

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
