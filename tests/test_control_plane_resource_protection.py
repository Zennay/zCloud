import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_control_plane_resource_protection.py"

spec = importlib.util.spec_from_file_location("resource_protection", MODULE_PATH)
resource_protection = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = resource_protection
spec.loader.exec_module(resource_protection)


class ResourceProtectionTests(unittest.TestCase):
    def test_parse_show_keeps_only_bounded_resource_properties(self):
        parsed = resource_protection.parse_show(
            "ActiveState=active\nCPUWeight=1000\nEnvironment=SECRET=value\nNice=0\n"
        )
        self.assertEqual(
            {"ActiveState": "active", "CPUWeight": "1000", "Nice": "0"},
            parsed,
        )

    def test_read_unit_uses_user_manager_only_for_firefox_scope(self):
        seen = []

        def runner(command, **kwargs):
            seen.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="ActiveState=active\nCPUWeight=2000\nNice=0\n",
                stderr="",
            )

        system = resource_protection.read_unit(
            resource_protection.UnitSpec("zennay-cloud.service", "system", "control"),
            runner=runner,
        )
        user = resource_protection.read_unit(
            resource_protection.UnitSpec("chatgpt-firefox.service", "user", "control"),
            runner=runner,
        )

        self.assertTrue(system["available"])
        self.assertTrue(user["available"])
        self.assertNotIn("--user", seen[0][0])
        self.assertIn("--user", seen[1][0])
        for command, kwargs in seen:
            self.assertEqual("systemctl", command[0])
            self.assertEqual("show", command[1] if "--user" not in command else command[2])
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs["check"])
            self.assertLessEqual(kwargs["timeout"], 5)

    def test_current_like_low_weight_control_plane_is_not_claimed_protected(self):
        result = resource_protection.classify(
            [
                {
                    "name": "zennay-cloud.service",
                    "scope": "system",
                    "role": "control",
                    "available": True,
                    "active": True,
                    "cpu_weight": 100,
                    "nice": 0,
                },
                {
                    "name": "chatgpt-firefox.service",
                    "scope": "user",
                    "role": "control",
                    "available": True,
                    "active": True,
                    "cpu_weight": 100,
                    "nice": 10,
                },
                {
                    "name": "ftmo-autonomous-marathon.service",
                    "scope": "system",
                    "role": "compute",
                    "available": True,
                    "active": True,
                    "cpu_weight": 10000,
                    "nice": 0,
                },
            ]
        )
        self.assertEqual("needs_hardening", result["status"])
        self.assertIn(
            "zennay-cloud.service:cpu_weight_below_floor", result["issues"]
        )
        self.assertIn(
            "chatgpt-firefox.service:positive_nice", result["issues"]
        )
        self.assertIn(
            "zennay-cloud.service:active_compute_weight_not_lower:ftmo-autonomous-marathon.service",
            result["issues"],
        )

    def test_protected_control_units_pass_when_compute_is_lower_in_same_scope(self):
        result = resource_protection.classify(
            [
                {
                    "name": "zennay-cloud.service",
                    "scope": "system",
                    "role": "control",
                    "available": True,
                    "active": True,
                    "cpu_weight": 5000,
                    "nice": 0,
                },
                {
                    "name": "chatgpt-firefox.service",
                    "scope": "user",
                    "role": "control",
                    "available": True,
                    "active": True,
                    "cpu_weight": 5000,
                    "nice": 0,
                },
                {
                    "name": "ftmo-autonomous-marathon.service",
                    "scope": "system",
                    "role": "compute",
                    "available": True,
                    "active": True,
                    "cpu_weight": 1000,
                    "nice": 0,
                },
                {
                    "name": "haxlab-autonomy.service",
                    "scope": "system",
                    "role": "compute",
                    "available": True,
                    "active": False,
                    "cpu_weight": 10000,
                    "nice": 0,
                },
            ]
        )
        self.assertEqual("protected", result["status"])
        self.assertEqual([], result["issues"])

    def test_cross_scope_weights_are_never_compared(self):
        result = resource_protection.classify(
            [
                {
                    "name": "chatgpt-firefox.service",
                    "scope": "user",
                    "role": "control",
                    "available": True,
                    "active": True,
                    "cpu_weight": 5000,
                    "nice": 0,
                },
                {
                    "name": "compute.service",
                    "scope": "system",
                    "role": "compute",
                    "available": True,
                    "active": True,
                    "cpu_weight": 10000,
                    "nice": 0,
                },
            ]
        )
        self.assertEqual("protected", result["status"])
        self.assertFalse(
            any("active_compute_weight_not_lower" in issue for issue in result["issues"])
        )
        self.assertFalse(result["policy"]["cross_scope_weight_comparison"])

    def test_missing_or_unreadable_control_state_fails_closed(self):
        result = resource_protection.classify(
            [
                {
                    "name": "zennay-cloud.service",
                    "scope": "system",
                    "role": "control",
                    "available": False,
                    "active": False,
                }
            ]
        )
        self.assertEqual("needs_hardening", result["status"])
        self.assertIn(
            "zennay-cloud.service:resource_state_unavailable", result["issues"]
        )


if __name__ == "__main__":
    unittest.main()
