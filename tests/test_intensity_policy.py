import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_intensity_policy import PolicyError, policy_for

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = json.loads((ROOT / "project-contracts.json").read_text(encoding="utf-8"))


class IntensityPolicyTests(unittest.TestCase):
    def test_boundary_values_map_deterministically(self):
        expected = {
            0: "minimum",
            20: "minimum",
            21: "conservative",
            40: "conservative",
            41: "balanced",
            60: "balanced",
            61: "accelerated",
            80: "accelerated",
            81: "maximum",
            100: "maximum",
        }
        for intensity, tier in expected.items():
            with self.subTest(intensity=intensity):
                self.assertEqual(
                    tier,
                    policy_for("ftmo", intensity, contracts=CONTRACTS)["tier"],
                )

    def test_out_of_range_and_unknown_project_fail_closed(self):
        for intensity in (-1, 101):
            with self.subTest(intensity=intensity):
                with self.assertRaisesRegex(PolicyError, "intensity_out_of_range"):
                    policy_for("ftmo", intensity, contracts=CONTRACTS)
        with self.assertRaisesRegex(PolicyError, "project_unknown"):
            policy_for("does-not-exist", 50, contracts=CONTRACTS)

    def test_slider_never_exceeds_existing_cpu_contract(self):
        for project_id, project in CONTRACTS["projects"].items():
            limit = float(project["compute"]["cpu_soft_cores"])
            for intensity in (0, 20, 40, 60, 80, 100):
                with self.subTest(project=project_id, intensity=intensity):
                    policy = policy_for(project_id, intensity, contracts=CONTRACTS)
                    self.assertLessEqual(
                        policy["scheduler"]["cpu_target_cores"],
                        limit,
                    )
                    self.assertEqual(
                        limit,
                        policy["guardrails"]["cpu_soft_cores_max"],
                    )

    def test_protected_projects_never_become_burst_eligible(self):
        for project_id, project in CONTRACTS["projects"].items():
            if project["compute"]["protected"]:
                with self.subTest(project=project_id):
                    policy = policy_for(project_id, 100, contracts=CONTRACTS)
                    self.assertFalse(policy["scheduler"]["burst_eligible"])
                    self.assertTrue(policy["guardrails"]["protected"])

    def test_disabled_projects_never_request_compute_or_idle_borrow(self):
        policy = policy_for("ulab", 100, contracts=CONTRACTS)
        self.assertEqual(0.0, policy["scheduler"]["cpu_target_cores"])
        self.assertFalse(policy["scheduler"]["allow_idle_capacity_borrow"])
        self.assertFalse(policy["scheduler"]["burst_eligible"])

    def test_intensity_does_not_change_worker_cap_or_contract_input(self):
        original = copy.deepcopy(CONTRACTS)
        low = policy_for("supa", 0, contracts=CONTRACTS)
        high = policy_for("supa", 100, contracts=CONTRACTS)
        self.assertEqual(low["guardrails"]["ai_worker_cap"], high["guardrails"]["ai_worker_cap"])
        self.assertFalse(high["guardrails"]["slider_may_override_worker_cap"])
        self.assertFalse(high["guardrails"]["slider_may_override_pool_admission"])
        self.assertFalse(high["guardrails"]["slider_may_override_memory_guard"])
        self.assertFalse(high["guardrails"]["slider_may_preempt_protected_capacity"])
        self.assertEqual(original, CONTRACTS)

    def test_policy_is_monotonic_for_queue_weight_and_cpu_target(self):
        previous_weight = -1
        previous_cpu = -1.0
        for intensity in range(101):
            policy = policy_for("ftmo", intensity, contracts=CONTRACTS)
            weight = policy["scheduler"]["queue_weight"]
            cpu = policy["scheduler"]["cpu_target_cores"]
            self.assertGreaterEqual(weight, previous_weight)
            self.assertGreaterEqual(cpu, previous_cpu)
            previous_weight = weight
            previous_cpu = cpu

    def test_cli_emits_bounded_json_and_rejects_bad_input(self):
        ok = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "zcloud_intensity_policy.py"),
                "--project",
                "ftmo",
                "--intensity",
                "75",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, ok.returncode, ok.stderr)
        payload = json.loads(ok.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("ftmo", payload["policy"]["project_id"])
        self.assertNotIn("memory_soft_mb", payload["policy"]["scheduler"])

        bad = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "zcloud_intensity_policy.py"),
                "--project",
                "ftmo",
                "--intensity",
                "101",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, bad.returncode)
        self.assertEqual(
            {"ok": False, "error": "intensity_out_of_range"},
            json.loads(bad.stdout),
        )

    def test_malformed_contract_fails_closed(self):
        broken = copy.deepcopy(CONTRACTS)
        broken["projects"]["ftmo"]["compute"]["cpu_soft_cores"] = "many"
        with self.assertRaisesRegex(PolicyError, "cpu_soft_cores_invalid"):
            policy_for("ftmo", 50, contracts=broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)
