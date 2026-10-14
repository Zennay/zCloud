"""Adversarial GitHub Actions check provenance admission tests (offline only)."""
from copy import deepcopy
import contextlib
import io
import json
import unittest

from scripts import zcloud_autonomous_pr_integrator as engine
from tests.test_zcloud_autonomous_pr_integrator import A, B, ENV, checks, files, pr, optional_cpu_check


class BoundHostedSmokeTests(unittest.TestCase):
    def test_write_scoped_hourly_job_runs_all_trust_suites_before_merge(self):
        from pathlib import Path
        wf = (Path(__file__).resolve().parents[1]
              / ".github/workflows/zcloud-autonomous-safe-pr-integration.yml"
              ).read_text(encoding="utf-8")
        preflight = wf.index("Validate engine and safety gates")
        write = wf.index("Plan or merge one approved development PR")
        self.assertLess(preflight, write)
        for module in (
            "tests.test_zcloud_autonomous_pr_integrator",
            "tests.test_deployops_automerge_trust_roots_20261014_w12",
            "tests.test_deployops_automerge_check_provenance_20261014_w14",
            "tests.test_zcloud_automerge_report_20261014",
        ):
            with self.subTest(module=module):
                self.assertIn(module, wf[preflight:write])
        self.assertIn("runs-on: ubuntu-latest", wf)
        self.assertNotIn("runs-on: [self-hosted", wf)

    def test_actual_github_hosted_shape_is_eligible(self):
        sample = checks()
        self.assertEqual(engine.trusted_job_identity(sample[0]), (400, 300))
        self.assertEqual(engine.trusted_job_identity(sample[1]), (400, 300))
        self.assertEqual(engine.decide(pr(), files(), sample, [], B), "eligible")

    def test_untrusted_smoke_app_job_or_owner_never_approves(self):
        edits = (
            ("app", {"id": 999, "slug": "spoofed", "owner": {"login": "github"}}),
            ("app", {"id": 15368, "slug": "github-actions", "owner": {"login": "attacker"}}),
            ("app", {"id": True, "slug": "github-actions", "owner": {"login": "github"}}),
            ("details_url", "https://github.com/fork/repo/actions/runs/400/job/501"),
            ("details_url", "https://github.com/Zennay/zCloud/actions/runs/400/job/502"),
            ("html_url", "https://github.com/attacker/repo"),
            ("id", 502),
            ("id", True),
            ("check_suite", {"id": 0}),
            ("check_suite", {"id": "300"}),
        )
        for field, changed in edits:
            sample = checks()
            sample[0][field] = changed
            with self.subTest(field=field, value=changed):
                self.assertEqual(
                    engine.decide(pr(), files(), sample, [], B),
                    "untrusted_required_check",
                )

    def test_admission_missing_skipped_or_foreign_suite_is_denied(self):
        sample = checks()
        self.assertEqual(engine.decide(pr(), files(), [sample[0]], [], B),
                         "required_admission_missing")
        skipped = checks()
        skipped[1]["conclusion"] = "skipped"
        self.assertEqual(engine.decide(pr(), files(), skipped, [], B),
                         "required_admission_not_green")
        for change in ({"check_suite": {"id": 301}},
                       {"details_url": "https://github.com/Zennay/zCloud/actions/runs/401/job/502",
                        "html_url": "https://github.com/Zennay/zCloud/actions/runs/401/job/502"}):
            sample = checks()
            sample[1].update(change)
            self.assertEqual(engine.decide(pr(), files(), sample, [], B),
                             "unbound_admission_suite")

    def test_duplicate_smoke_or_admission_job_is_denied(self):
        for copy_index in (0, 1):
            sample = checks()
            duplicate = deepcopy(sample[copy_index])
            duplicate["id"] = duplicate["id"] + 20
            duplicate["details_url"] = duplicate["details_url"].replace(
                "/job/" + str(sample[copy_index]["id"]),
                "/job/" + str(duplicate["id"]))
            duplicate["html_url"] = duplicate["details_url"]
            sample.append(duplicate)
            self.assertEqual(engine.decide(pr(), files(), sample, [], B),
                             "duplicate_required_check")

    def test_optional_queued_vps_does_not_impersonate_required_hosted_check(self):
        optional = optional_cpu_check()
        p = pr()
        p["mergeable_state"] = "unstable"
        self.assertEqual(engine.decide(p, files(), checks() + [optional], [], B),
                         "eligible")
        optional["name"] = "critical-runner-flows"
        self.assertEqual(engine.decide(p, files(), checks() + [optional], [], B),
                         "duplicate_required_check")

    def test_last_second_untrusted_check_fails_before_sha_bound_merge(self):
        calls = []
        times = 0
        def transport(token, method, endpoint, payload=None):
            nonlocal times
            calls.append((method, endpoint))
            if method == "PUT":
                self.fail("forged or stale second-pass GitHub check allowed a merge")
            if endpoint == "/branches/main":
                return {"commit": {"sha": B}}
            if endpoint.startswith("/pulls?"):
                return [pr()]
            if endpoint == "/pulls/21":
                return pr()
            if endpoint == "/pulls/21/files?per_page=100":
                return files()
            if endpoint == f"/commits/{A}/check-runs?per_page=100":
                times += 1
                sample = checks()
                if times == 2:
                    sample[0]["app"]["id"] = 42
                return {"total_count": len(sample), "check_runs": sample}
            if endpoint == f"/commits/{A}/statuses?per_page=100":
                return []
            self.fail("unexpected endpoint " + endpoint)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(engine.main(["--apply"], ENV, transport), 0)
        self.assertEqual(times, 2)
        self.assertEqual(json.loads(out.getvalue())["receipts"][0]["decision"],
                         "recheck_untrusted_required_check")
        self.assertFalse(any(method == "PUT" for method, _ in calls))


if __name__ == "__main__":
    unittest.main()
