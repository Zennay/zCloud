import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_resource_profile_policy.py"
SPEC = importlib.util.spec_from_file_location("zcloud_resource_profile_policy", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MOD)


class ResourceProfilePolicyTests(unittest.TestCase):
    def setUp(self):
        self.contracts = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))
        self.policy = json.loads((ROOT / "resource-profile-policy.v1.json").read_text(encoding="utf-8"))

    def test_current_candidate_is_compatible_and_complete(self):
        report = MOD.build_report(self.contracts, self.policy)
        self.assertEqual("compatible", report["status"])
        self.assertEqual(set(self.contracts["projects"]), {p["project_id"] for p in report["profiles"]})
        self.assertFalse(report["runtime_mutation"])
        self.assertEqual("candidate_only", report["integration_state"])

    def test_ordering_is_fail_closed(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["ftmo"]["minimum_cpu_cores"] = 5
        with self.assertRaisesRegex(MOD.PolicyError, "minimum <= target <= maximum"):
            MOD.build_report(self.contracts, bad)

    def test_bool_is_not_accepted_as_number(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["supa"]["minimum_cpu_cores"] = False
        with self.assertRaisesRegex(MOD.PolicyError, "finite number required"):
            MOD.build_report(self.contracts, bad)

    def test_target_must_preserve_current_soft_cpu_during_candidate_phase(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["haxlab"]["target_cpu_cores"] = 0.5
        with self.assertRaisesRegex(MOD.PolicyError, "target must equal current cpu_soft_cores"):
            MOD.build_report(self.contracts, bad)

    def test_every_canonical_project_is_required(self):
        bad = copy.deepcopy(self.policy)
        del bad["projects"]["zguard"]
        with self.assertRaisesRegex(MOD.PolicyError, "projects mismatch"):
            MOD.build_report(self.contracts, bad)

    def test_protected_minimum_cannot_weaken_existing_reserve(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["cloud"]["minimum_cpu_cores"] = 0
        with self.assertRaisesRegex(MOD.PolicyError, "protected minimum may not weaken"):
            MOD.build_report(self.contracts, bad)

    def test_disabled_pool_must_remain_zero(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["ulab"]["maximum_cpu_cores"] = 1
        with self.assertRaisesRegex(MOD.PolicyError, "disabled pool requires zero"):
            MOD.build_report(self.contracts, bad)

    def test_unknown_profile_fields_are_refused(self):
        bad = copy.deepcopy(self.policy)
        bad["projects"]["supa"]["note"] = "hidden"
        with self.assertRaisesRegex(MOD.PolicyError, "profile keys must be exactly"):
            MOD.build_report(self.contracts, bad)

    def test_symlink_profile_input_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            real = td / "real.json"
            real.write_text(json.dumps(self.policy), encoding="utf-8")
            link = td / "profile.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(MOD.PolicyError, "symlink inputs are refused"):
                MOD._load_json(link)

    def test_workflow_is_permanent_vps_read_only_exact_head(self):
        text = (ROOT / ".github/workflows/zcloud-resource-profile-policy-proof.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn("permissions:\n  contents: read", text)
        for forbidden in ("sudo ", "systemctl ", "sqlite3 ", "curl -X", "gh api --method"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
