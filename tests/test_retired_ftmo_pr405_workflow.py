from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

RETIRED_WORKFLOWS = (
    WORKFLOW_DIR / "ftmo-pr405-hour-cache-proof.yml",
    WORKFLOW_DIR / "ftmo-pr408-provider-boundary-proof.yml",
)

# These authorities were tied to terminal FTMO PR-specific proof/cleanup work.
# They must not silently return under renamed workflows.
HISTORICAL_AUTHORITY_FINGERPRINTS = (
    # PR405 stale-CI cancellation authority.
    "repos/Zennay/Ftmo/pulls/405",
    "36980508317",
    "36980508313",
    "STALE_PR405_CANCEL_REQUESTED",
    # PR408 exact-head proof authority.
    "98c033b2b571cea2313f20bee31820286132226f",
    "FTMO_PR408_TARGETED_GREEN",
)


class RetiredFtmoHistoricalWorkflowTests(unittest.TestCase):
    def test_terminal_ftmo_workflows_stay_retired(self):
        resurrected = [
            str(path.relative_to(ROOT))
            for path in RETIRED_WORKFLOWS
            if path.exists()
        ]
        self.assertEqual(
            [],
            resurrected,
            "terminal FTMO PR-specific workflows must stay retired",
        )

    def test_active_workflows_do_not_restore_historical_authority(self):
        offenders = {}
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            hits = [
                marker
                for marker in HISTORICAL_AUTHORITY_FINGERPRINTS
                if marker in text
            ]
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits

        self.assertEqual(
            {},
            offenders,
            "historical FTMO PR-specific authority resurfaced",
        )


if __name__ == "__main__":
    unittest.main()
