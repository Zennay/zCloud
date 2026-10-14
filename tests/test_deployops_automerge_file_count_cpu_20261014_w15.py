"""No auto-merge from incomplete PR file lists or spoofed queued VPS checks.

All test transport is in-memory; this never accesses a VPS or GitHub write API.
"""
from copy import deepcopy
import contextlib
import io
import json
import unittest

from scripts import zcloud_autonomous_pr_integrator as engine
from tests.test_zcloud_autonomous_pr_integrator import (
    A, B, ENV, checks, files, optional_cpu_check, pr,
)


class ExactChangedFileEvidenceTests(unittest.TestCase):
    def test_normal_exact_file_count_is_eligible(self):
        self.assertEqual(engine.decide(pr(), files(), checks(), [], B), "eligible")
        good = pr()
        good["changed_files"] = 2
        self.assertEqual(engine.decide(good, files() + [
            {"filename": "docs/other.md", "status": "added"}], checks(), [], B), "eligible")

    def test_omitted_or_ambiguous_changed_file_total_is_refused(self):
        for n in (None, True, False, 0, -1, 2, 99, 100, 150, 999, "1", 1.0, [], {}):
            candidate = pr()
            candidate["changed_files"] = n
            with self.subTest(n=repr(n)):
                self.assertEqual(engine.decide(candidate, files(), checks(), [], B),
                                 "incomplete_file_inventory")
        candidate = pr()
        del candidate["changed_files"]
        self.assertEqual(engine.decide(candidate, files(), checks(), [], B),
                         "incomplete_file_inventory")

    def test_truncated_safe_snapshot_cannot_hide_privileged_file(self):
        candidate = pr()
        candidate["changed_files"] = 2   # File endpoint returned only safe first page.
        safe_only = [{"filename": "docs/benign.md", "status": "modified"}]
        self.assertEqual(engine.decide(candidate, safe_only, checks(), [], B),
                         "incomplete_file_inventory")
        candidate["changed_files"] = 100
        self.assertEqual(engine.decide(candidate, safe_only, checks(), [], B),
                         "incomplete_file_inventory")
        candidate["changed_files"] = 2
        mixed = safe_only + [{"filename": ".github/workflows/privileged.yml",
                              "status": "added"}]
        self.assertEqual(engine.decide(candidate, mixed, checks(), [], B),
                         "unsafe_or_duplicate_path")

    def test_second_admission_file_count_drift_denies_before_put(self):
        gets = 0
        writes = []
        def transport(token, method, path, data=None):
            nonlocal gets
            if method == "PUT":
                writes.append((path, data))
                self.fail("must not merge with changed or incomplete file scope")
            if path == "/branches/main":
                return {"commit": {"sha": B}}
            if path.startswith("/pulls?"):
                return [pr()]
            if path == "/pulls/21":
                gets += 1
                candidate = pr()
                if gets == 2:
                    candidate["changed_files"] = 2
                return candidate
            if path == "/pulls/21/files?per_page=100":
                return files()
            if path == f"/commits/{A}/check-runs?per_page=100":
                sample = checks()
                return {"total_count": len(sample), "check_runs": sample}
            if path == f"/commits/{A}/statuses?per_page=100":
                return []
            self.fail("unexpected API endpoint " + path)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(engine.main(["--apply"], ENV, transport), 0)
        self.assertEqual(gets, 2)
        self.assertEqual(writes, [])
        self.assertEqual(json.loads(output.getvalue())["receipts"][0]["decision"],
                         "recheck_incomplete_file_inventory")


class OptionalCpuOriginTests(unittest.TestCase):
    def test_real_queued_cpu_from_github_actions_remains_observation_only(self):
        pending = optional_cpu_check()
        p = pr()
        p["mergeable_state"] = "unstable"
        self.assertIsNotNone(engine.trusted_job_identity(pending))
        self.assertEqual(engine.decide(p, files(), checks() + [pending], [], B),
                         "eligible")

    def test_spoofed_optional_cpu_cannot_explain_unstable_branch(self):
        mutations = (
            ("app", {"id": 1, "slug": "attacker", "owner": {"login": "attacker"}}),
            ("app", None),
            ("details_url", "https://evil.invalid/actions/runs/401/job/503"),
            ("html_url", "https://evil.invalid/actions/runs/401/job/503"),
            ("id", 999),
            ("check_suite", {"id": 0}),
            ("check_suite", None),
        )
        for key, value in mutations:
            cpu = optional_cpu_check()
            cpu[key] = value
            p = pr()
            p["mergeable_state"] = "unstable"
            with self.subTest(key=key, value=value):
                self.assertEqual(engine.decide(p, files(), checks() + [cpu], [], B),
                                 "untrusted_optional_cpu_check")
        missing = {"head_sha": A, "name": "cpu-snapshot",
                   "status": "queued", "conclusion": None}
        self.assertEqual(engine.decide(pr(), files(), checks() + [missing], [], B),
                         "untrusted_optional_cpu_check")

    def test_unrelated_pending_checks_still_cannot_pass(self):
        pending = optional_cpu_check()
        pending["name"] = "unrelated-required-check"
        self.assertEqual(engine.decide(pr(), files(), checks() + [pending], [], B),
                         "check_not_green")
        cpu = optional_cpu_check()
        cpu["conclusion"] = "skipped"  # Invalid for non-terminal queued job.
        self.assertEqual(engine.decide(pr(), files(), checks() + [cpu], [], B),
                         "check_not_green")


if __name__ == "__main__":
    unittest.main()
