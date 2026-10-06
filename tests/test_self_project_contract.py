import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_self_project_contract",
    ROOT / "scripts" / "zcloud_self_project_contract.py",
)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(audit_module)


class SelfProjectContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in (
            "projects.json",
            "project-contracts.json",
            "resource-policy.json",
            "autonomy-policy.json",
        ):
            (self.root / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def audit(self):
        return audit_module.audit(
            self.root / "projects.json",
            self.root / "project-contracts.json",
            self.root / "resource-policy.json",
            self.root / "autonomy-policy.json",
        )

    def test_current_repository_contract_is_green(self):
        result = self.audit()
        self.assertTrue(result["ok"], result)
        self.assertIn("runtime:zcloud-stopgate", result["checks"])
        self.assertIn("legacy:no-cloud-drift", result["checks"])

    def test_missing_or_duplicate_cloud_registry_fails_closed(self):
        projects = self.load("projects.json")
        cloud = next(row for row in projects if row["id"] == "cloud")

        self.save("projects.json", [row for row in projects if row["id"] != "cloud"])
        self.assertFalse(self.audit()["ok"])

        self.save("projects.json", projects + [copy.deepcopy(cloud)])
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("duplicate project ids" in error for error in result["errors"]))

    def test_cloud_must_keep_stopgate_owned_autonomy(self):
        contracts = self.load("project-contracts.json")
        contracts["projects"]["cloud"]["autonomy"]["mode"] = "ai_worker"
        self.save("project-contracts.json", contracts)
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("zcloud_stopgate" in error for error in result["errors"]))

    def test_cloud_compute_must_remain_protected(self):
        contracts = self.load("project-contracts.json")
        contracts["projects"]["cloud"]["compute"]["pool"] = "build"
        contracts["projects"]["cloud"]["compute"]["protected"] = False
        self.save("project-contracts.json", contracts)
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("cloud compute.pool must be protected" in error for error in result["errors"]))
        self.assertTrue(any("cloud compute.protected must be true" in error for error in result["errors"]))

    def test_resource_priority_must_match_runtime_contract(self):
        resources = self.load("resource-policy.json")
        resources["cloud"]["priority"] = "background"
        self.save("resource-policy.json", resources)
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("priority must match" in error for error in result["errors"]))

    def test_legacy_autonomy_must_not_reclaim_cloud_truth(self):
        legacy = self.load("autonomy-policy.json")
        legacy["projects"]["cloud"] = {"mode": "ai_worker"}
        self.save("autonomy-policy.json", legacy)
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("legacy autonomy truth" in error for error in result["errors"]))

    def test_symlink_input_is_rejected(self):
        target = self.root / "real-projects.json"
        target.write_text((self.root / "projects.json").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "projects.json").unlink()
        (self.root / "projects.json").symlink_to(target)
        result = self.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("symlink input refused" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
