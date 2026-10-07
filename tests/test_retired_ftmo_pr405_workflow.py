from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
RETIRED_WORKFLOW = WORKFLOW_DIR / "ftmo-pr405-hour-cache-proof.yml"

# This authority was tied to FTMO PR #405 and two exact CI runs that are
# terminal. It must not silently return under a renamed workflow.
HISTORICAL_CANCELLATION_FINGERPRINTS = (
    "repos/Zennay/Ftmo/pulls/405",
    "36980508317",
    "36980508313",
    "STALE_PR405_CANCEL_REQUESTED",
)


class RetiredFtmoPr405WorkflowTests(unittest.TestCase):
    def test_historical_pr405_cancellation_workflow_stays_retired(self):
        self.assertFalse(
            RETIRED_WORKFLOW.exists(),
            "terminal FTMO PR405 cancellation workflow must stay retired",
        )

    def test_active_workflows_do_not_restore_pr405_cancellation_authority(self):
        offenders = {}
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            hits = [
                marker
                for marker in HISTORICAL_CANCELLATION_FINGERPRINTS
                if marker in text
            ]
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits

        self.assertEqual(
            {},
            offenders,
            "historical FTMO PR405 cancellation authority resurfaced",
        )


if __name__ == "__main__":
    unittest.main()
