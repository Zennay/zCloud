import json
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_vps_runner_guard import validate_runner


class VpsRunnerGuardTests(unittest.TestCase):
    def policy(self) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({
            "runner": {
                "host": "vps-bb300bba",
                "forbidden_name_tokens": ["haxlab"],
            }
        }, tmp)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink, missing_ok=True)
        return Path(tmp.name)

    def test_accepts_permanent_zcloud_runner(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="zcloud-vps-1",
        )
        self.assertTrue(result["ok"])
        self.assertEqual([], result["errors"])

    def test_rejects_temporary_haxlab_runner_even_on_same_host(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="vps-bb300bba-haxlab",
        )
        self.assertFalse(result["ok"])
        self.assertIn("forbidden_runner_name:haxlab", result["errors"])

    def test_rejects_wrong_host(self):
        result = validate_runner(
            self.policy(),
            hostname="another-host",
            runner_name="zcloud-vps-1",
        )
        self.assertFalse(result["ok"])
        self.assertIn("host_mismatch", result["errors"])

    def test_rejects_missing_runner_name(self):
        result = validate_runner(
            self.policy(),
            hostname="vps-bb300bba",
            runner_name="",
        )
        self.assertFalse(result["ok"])
        self.assertIn("runner_name_missing", result["errors"])


    def test_current_production_policy_accepts_only_observed_migrated_agent(self):
        policy = Path(__file__).resolve().parents[1] / "vps-execution-policy.json"
        host = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
        agent = "vm-7eb5ee16-zcloud"
        accepted = validate_runner(policy, hostname=host, runner_name=agent)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual([], accepted["errors"])

        wrong_agent = validate_runner(
            policy, hostname=host, runner_name="vm-7eb5ee16-ftmo"
        )
        self.assertFalse(wrong_agent["ok"])
        self.assertIn("runner_identity_mismatch", wrong_agent["errors"])

        wrong_host = validate_runner(
            policy, hostname="vm-similar-but-unauthorized", runner_name=agent
        )
        self.assertFalse(wrong_host["ok"])
        self.assertIn("host_mismatch", wrong_host["errors"])

    def test_current_policy_preserves_existing_legacy_runner_identity(self):
        policy = Path(__file__).resolve().parents[1] / "vps-execution-policy.json"
        result = validate_runner(
            policy, hostname="vps-bb300bba", runner_name="zcloud-vps-1"
        )
        self.assertTrue(result["ok"], result)

    def test_migrated_host_denies_missing_or_forbidden_runner_name(self):
        policy = Path(__file__).resolve().parents[1] / "vps-execution-policy.json"
        host = "VM-7eb5ee16-2c71-440d-b7fd-8095d204e930"
        missing = validate_runner(policy, hostname=host, runner_name="")
        self.assertFalse(missing["ok"])
        self.assertIn("runner_name_missing", missing["errors"])
        forbidden = validate_runner(
            policy, hostname=host, runner_name="vm-7eb5ee16-haxlab"
        )
        self.assertFalse(forbidden["ok"])
        self.assertIn("forbidden_runner_name:haxlab", forbidden["errors"])

    def test_malformed_migrated_identity_policy_fails_closed(self):
        policy = self.policy()
        data = json.loads(policy.read_text())
        data["runner"]["verified_host_runners"] = ["unbounded", "not a map"]
        policy.write_text(json.dumps(data))
        result = validate_runner(
            policy, hostname="vm-7eb5ee16-2c71-440d-b7fd-8095d204e930",
            runner_name="vm-7eb5ee16-zcloud",
        )
        self.assertFalse(result["ok"])
        self.assertIn("runner_identity_policy_invalid", result["errors"])


if __name__ == "__main__":
    unittest.main()
