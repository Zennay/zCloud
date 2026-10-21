from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
RETIRED_WORKFLOW = WORKFLOW_DIR / "ftmo-pr468-469-exact-head-proof.yml"

HISTORICAL_COMPOSITE_PROOF_MARKERS = (
    "FTMO_PR468_EXACT_HEAD_GREEN",
    "FTMO_PR469_EXACT_HEAD_GREEN",
)


class RetiredFtmoCompositeProofTests(unittest.TestCase):
    def test_terminal_composite_proof_stays_retired(self):
        self.assertFalse(
            RETIRED_WORKFLOW.exists(),
            "terminal FTMO PR468/469 composite proof must stay retired",
        )

    def test_active_workflows_do_not_restore_composite_proof_authority(self):
        offenders = {}
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            hits = [
                marker
                for marker in HISTORICAL_COMPOSITE_PROOF_MARKERS
                if marker in text
            ]
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits

        self.assertEqual(
            {},
            offenders,
            "terminal FTMO PR468/469 composite proof authority resurfaced",
        )


if __name__ == "__main__":
    unittest.main()
