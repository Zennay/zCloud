from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "ftmo-burst-once.yml"

LEGACY_AUTHORITY_MARKERS = (
    "FTMO VPS useful-compute rescue",
    "ftmo-vps-useful-compute-rescue",
)


class FtmoBurstOnceRetirementTests(unittest.TestCase):
    def test_stale_burst_rescue_workflow_stays_retired(self):
        self.assertFalse(
            RETIRED.exists(),
            "stale one-shot FTMO rescue workflow must not be reintroduced",
        )

    def test_legacy_rescue_authority_markers_do_not_move_to_another_workflow(self):
        offenders = {}
        for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
            text = path.read_text(encoding="utf-8")
            found = [
                marker for marker in LEGACY_AUTHORITY_MARKERS if marker in text
            ]
            if found:
                offenders[path.name] = found

        self.assertEqual(
            offenders,
            {},
            "legacy FTMO burst-rescue identity was moved instead of retired",
        )


if __name__ == "__main__":
    unittest.main()
