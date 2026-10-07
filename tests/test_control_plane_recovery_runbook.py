from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "zcloud-control-plane-recovery-runbook.md"


class ControlPlaneRecoveryRunbookTests(unittest.TestCase):
    def test_runbook_pins_canonical_safe_recovery_paths(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "python3 scripts/zcloud_healthcheck.py --json",
            "python3 scripts/zcloud_recovery.py status",
            "python3 scripts/zcloud_recovery.py rollback",
            ".github/workflows/zcloud-vps-deploy.yml",
            ".github/workflows/zcloud-safe-idle-retry.yml",
            ".github/workflows/zcloud-production-status-recovery.yml",
            "zcloud/vps-production",
            "zcloud/vps-safe-idle-retry",
            "finalize-production-status",
            "last-known-good.json",
            "POSTDEPLOY_GREEN",
            "history.db",
        ):
            self.assertIn(required, text)

    def test_runbook_preserves_persistent_state_and_forbids_bypass_routes(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("must remain preserved", text)
        self.assertIn("Never replace `history.db`", text)
        self.assertIn("Never bypass", text)
        self.assertIn("Never use `git reset --hard`", text)
        self.assertIn("serialized zCloud production lane authoritative", text)
        self.assertIn("do not start a competing one", text)
        self.assertIn("must never force-stop a worker", text)
        self.assertIn("shorten the 480-second production safe-idle gate", text)
        self.assertIn("Neither is a second production writer", text)
        self.assertIn("dispatch `zcloud-regression-smoke.yml`", text)


if __name__ == "__main__":
    unittest.main()
