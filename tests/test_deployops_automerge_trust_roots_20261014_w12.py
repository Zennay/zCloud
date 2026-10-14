"""Prevent autonomous PR integrator from rewriting its own trust policy.

Pure in-memory GitHub transport. No API, live runner, CLI or repo mutation.
"""
import contextlib
import io
import json
import unittest

from scripts import zcloud_autonomous_pr_integrator as engine
from tests.test_zcloud_autonomous_pr_integrator import A, B, ENV, pr, checks, files


class ProtectedAutoIntegrationRoots(unittest.TestCase):
    def test_every_protected_root_is_banned_from_safe_path(self):
        required = {
            "scripts/zcloud_autonomous_pr_integrator.py",
            "scripts/zcloud_automerge_report_20261014.py",
            "tests/test_zcloud_autonomous_pr_integrator.py",
            "tests/test_zcloud_automerge_report_20261014.py",
            "tests/test_deployops_automerge_workflow_replay_20261014_w11.py",
    "tests/test_deployops_automerge_trust_roots_20261014_w12.py",
            "tests/test_deployops_automerge_check_provenance_20261014_w14.py",
            "tests/test_deployops_automerge_file_count_cpu_20261014_w15.py",
            "tests/test_deployops_automerge_import_shadow_20261014_w16.py",
        }
        self.assertEqual(required, engine.PROTECTED_INTEGRATION_FILES)
        for path in sorted(required):
            with self.subTest(path=path):
                self.assertFalse(engine.safe_path(path))
                self.assertEqual(
                    "protected_integration_source",
                    engine.decide(pr(), [{"filename": path, "status": "modified"}],
                                  checks(), [], B),
                )

    def test_mixed_harmless_and_trust_root_pr_is_rejected_entirely(self):
        for root in sorted(engine.PROTECTED_INTEGRATION_FILES):
            mixed = files() + [{"filename": root, "status": "modified"}]
            with self.subTest(root=root):
                self.assertEqual(
                    engine.decide(pr(), mixed, checks(), [], B),
                    "protected_integration_source",
                )

    def test_label_and_green_smoke_do_not_authorize_self_mutation(self):
        calls = []
        def transport(token, method, endpoint, payload=None):
            calls.append((method, endpoint))
            if method != "GET":
                self.fail("self-modifying PR must never call the merge endpoint")
            if endpoint == "/branches/main":
                return {"commit": {"sha": B}}
            if endpoint.startswith("/pulls?"):
                return [pr()]
            if endpoint == "/pulls/21":
                return pr()
            if endpoint == "/pulls/21/files?per_page=100":
                return [{"filename": "scripts/zcloud_autonomous_pr_integrator.py",
                         "status": "modified"}]
            if endpoint == f"/commits/{A}/check-runs?per_page=100":
                return {"total_count": len(checks()), "check_runs": checks()}
            if endpoint == f"/commits/{A}/statuses?per_page=100":
                return []
            self.fail("unexpected API " + endpoint)
        for mode in ([], ["--apply"]):
            with self.subTest(mode=mode):
                calls.clear()
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(0, engine.main(mode, ENV, transport))
                self.assertEqual(
                    "protected_integration_source",
                    json.loads(out.getvalue())["receipts"][0]["decision"],
                )
                self.assertFalse([c for c in calls if c[0] != "GET"])

    def test_last_second_file_scope_drift_denied_before_merge(self):
        count = 0
        writes = []
        def transport(token, method, endpoint, payload=None):
            nonlocal count
            if method == "PUT":
                writes.append((endpoint, payload))
                self.fail("self-mutating PR cannot pass exact-head recheck")
            if endpoint == "/branches/main":
                return {"commit": {"sha": B}}
            if endpoint.startswith("/pulls?"):
                return [pr()]
            if endpoint == "/pulls/21":
                return pr()
            if endpoint == "/pulls/21/files?per_page=100":
                count += 1
                if count == 1:
                    return files()
                return files() + [{"filename": "tests/test_zcloud_autonomous_pr_integrator.py",
                                  "status": "modified"}]
            if endpoint == f"/commits/{A}/check-runs?per_page=100":
                return {"total_count": len(checks()), "check_runs": checks()}
            if endpoint == f"/commits/{A}/statuses?per_page=100":
                return []
            self.fail("unexpected API " + endpoint)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, engine.main(["--apply"], ENV, transport))
        self.assertEqual(count, 2)
        self.assertEqual(writes, [])
        self.assertEqual(
            "recheck_protected_integration_source",
            json.loads(out.getvalue())["receipts"][0]["decision"],
        )

    def test_normal_documents_and_independent_scripts_remain_eligible(self):
        for filename in (
            "docs/deploy-ops-guide.md",
            "tests/test_separate_workflow.py",
            "scripts/standalone_source_health.py",
        ):
            with self.subTest(filename=filename):
                self.assertTrue(engine.safe_path(filename))
                self.assertEqual(
                    "eligible",
                    engine.decide(pr(), [{"filename": filename, "status": "added"}],
                                  checks(), [], B),
                )

if __name__ == "__main__":
    unittest.main()
