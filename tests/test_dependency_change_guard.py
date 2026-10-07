import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_dependency_change_guard.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-dependency-change-guard.yml"
VPS_WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-dependency-change-guard-vps-proof.yml"

spec = importlib.util.spec_from_file_location("dependency_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(guard)


class DependencyChangeGuardTests(unittest.TestCase):
    def test_large_lockfile_plus_runtime_change_is_blocked(self):
        result = guard.evaluate_change([
            {"path": "package-lock.json", "added": 260, "deleted": 5},
            {"path": "server.py", "added": 4, "deleted": 1},
        ])
        self.assertFalse(result["ok"])
        self.assertIn("large_dependency_diff", result["reason_codes"])
        self.assertEqual(["server.py"], result["functional_paths"])

    def test_multiple_ecosystems_plus_runtime_change_is_blocked(self):
        result = guard.evaluate_change([
            {"path": "requirements.txt", "added": 1, "deleted": 1},
            {"path": "package-lock.json", "added": 2, "deleted": 2},
            {"path": "firefox-extension/background.js", "added": 1, "deleted": 0},
        ])
        self.assertFalse(result["ok"])
        self.assertIn("multiple_dependency_ecosystems", result["reason_codes"])

    def test_many_dependency_files_plus_operational_change_is_blocked(self):
        result = guard.evaluate_change([
            {"path": "requirements.txt", "added": 1, "deleted": 0},
            {"path": "requirements-dev.txt", "added": 1, "deleted": 0},
            {"path": "pyproject.toml", "added": 1, "deleted": 0},
            {"path": "uv.lock", "added": 1, "deleted": 0},
            {"path": ".github/workflows/deploy.yml", "added": 1, "deleted": 0},
        ])
        self.assertFalse(result["ok"])
        self.assertIn("many_dependency_files", result["reason_codes"])

    def test_mass_dependency_only_change_is_allowed(self):
        result = guard.evaluate_change([
            {"path": "package.json", "added": 5, "deleted": 4},
            {"path": "package-lock.json", "added": 300, "deleted": 100},
        ])
        self.assertTrue(result["ok"])
        self.assertIn("large_dependency_diff", result["reason_codes"])
        self.assertEqual([], result["functional_paths"])

    def test_small_single_ecosystem_update_can_ship_with_code(self):
        result = guard.evaluate_change([
            {"path": "requirements.txt", "added": 1, "deleted": 1},
            {"path": "server.py", "added": 3, "deleted": 2},
        ])
        self.assertTrue(result["ok"])
        self.assertEqual([], result["reason_codes"])

    def test_docs_and_tests_do_not_count_as_functional_mix(self):
        result = guard.evaluate_change([
            {"path": "package-lock.json", "added": 260, "deleted": 0},
            {"path": "docs/dependencies.md", "added": 8, "deleted": 0},
            {"path": "tests/test_dependencies.py", "added": 12, "deleted": 0},
        ])
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["functional_file_count"])

    def test_binary_dependency_diff_fails_conservatively(self):
        with mock.patch.object(guard, "_git", side_effect=[
            "M\tpackage-lock.json\nM\tserver.py\n",
            "-\t-\tpackage-lock.json\n1\t0\tserver.py\n",
        ]):
            changes = guard.collect_changes("base", "head")
        result = guard.evaluate_change(changes)
        self.assertFalse(result["ok"])
        self.assertIn("large_dependency_diff", result["reason_codes"])

    def test_rename_folding_is_disabled_for_numstat(self):
        with mock.patch.object(guard, "_git", side_effect=[
            "R90\tpackage-lock.json\tvendor/package-lock.json\nM\tserver.py\n",
            "0\t12\tpackage-lock.json\n300\t0\tvendor/package-lock.json\n1\t0\tserver.py\n",
        ]) as git:
            changes = guard.collect_changes("base", "head")
        self.assertEqual(
            ("diff", "--numstat", "--no-renames", "base...head"),
            git.call_args_list[1].args,
        )
        result = guard.evaluate_change(changes)
        self.assertFalse(result["ok"])
        self.assertIn("large_dependency_diff", result["reason_codes"])

    def test_cli_guard_error_is_fail_closed(self):
        with mock.patch.object(guard, "collect_changes", side_effect=RuntimeError("bad ref")):
            with mock.patch("builtins.print") as out:
                rc = guard.main(["--base", "bad", "--head", "worse", "--json"])
        self.assertEqual(2, rc)
        payload = json.loads(out.call_args.args[0])
        self.assertFalse(payload["ok"])
        self.assertEqual(["guard_error"], payload["reason_codes"])

    def test_workflow_is_hosted_read_only_and_exact_pr_diff(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("github.event.pull_request.base.sha", text)
        self.assertIn("zcloud_dependency_change_guard.py", text)
        for forbidden in ("curl ", "ssh ", "sudo ", "systemctl ", "sqlite3 "):
            self.assertNotIn(forbidden, text)

    def test_vps_proof_is_exact_head_and_read_only(self):
        text = VPS_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("python3 -m unittest -v tests.test_dependency_change_guard", text)
        self.assertIn("persist-credentials: false", text)
        for forbidden in ("curl ", "ssh ", "sudo ", "systemctl ", "sqlite3 ", "server.py"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
