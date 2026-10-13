"""Independent deploy-ops ratchet regressions (no overlap with timeout-workflow owner)."""

from __future__ import annotations

import unittest

from scripts import zcloud_self_hosted_timeout_audit as audit


class TimeoutDebtRatchetingTests(unittest.TestCase):
    def test_dynamic_runner_and_yaml_alias_are_never_silent(self):
        source = """jobs:
  dynamic:
    runs-on: ${{ matrix.runner }}
    timeout-minutes: 5
  alias:
    runs-on: *private_runner
    timeout-minutes: 10
  hosted:
    runs-on: ubuntu-latest
    timeout-minutes: 50
  misleading:
    runs-on: not-self-hosted
    timeout-minutes: 99
"""
        rows = audit.inspect_workflow(".github/workflows/dynamic.yml", source, max_minutes=30)
        self.assertEqual(["dynamic", "alias"], [item.job for item in rows])
        self.assertTrue(all(item.status == "dynamic_runner_unknown" for item in rows))
        counts = audit.summarize(rows, max_minutes=30)
        self.assertEqual(2, counts["finding_count"])
        self.assertEqual(2, counts["by_status"]["dynamic_runner_unknown"])
        self.assertFalse(counts["mutation_performed"])

    def test_bounded_self_hosted_label_is_still_checked(self):
        source = """jobs:
  real:
    runs-on: [self-hosted, zcloud, vps]
    timeout-minutes: 10
  different:
    runs-on: not-self-hosted
    timeout-minutes: 300
"""
        rows = audit.inspect_workflow(".github/workflows/labels.yml", source, max_minutes=30)
        self.assertEqual([("real", "bounded")], [(r.job, r.status) for r in rows])

    def test_legacy_excessive_timeout_cannot_get_longer_or_disappear(self):
        path = ".github/workflows/old.yml"
        base = [audit.TimeoutFinding(path, "slow", 35, "over_limit")]
        worsened = [audit.TimeoutFinding(path, "slow", 60, "over_limit")]
        missing = [audit.TimeoutFinding(path, "slow", None, "missing")]
        improved = [audit.TimeoutFinding(path, "slow", 31, "over_limit")]
        fixed = [audit.TimeoutFinding(path, "slow", 30, "bounded")]
        self.assertEqual(worsened, audit.new_debt(base, worsened))
        self.assertEqual(missing, audit.new_debt(base, missing))
        self.assertEqual([], audit.new_debt(base, improved))
        self.assertEqual([], audit.new_debt(base, fixed))

    def test_new_dynamic_runner_creates_policy_debt(self):
        row = audit.TimeoutFinding(".github/workflows/new.yml", "probe", 5, "dynamic_runner_unknown")
        self.assertEqual([row], audit.new_debt([], [row]))

    def test_existing_missing_timeout_can_be_fixed(self):
        base = [audit.TimeoutFinding("x.yml", "x", None, "missing")]
        head = [audit.TimeoutFinding("x.yml", "x", 10, "bounded")]
        self.assertEqual([], audit.new_debt(base, head))


if __name__ == "__main__":
    unittest.main()
