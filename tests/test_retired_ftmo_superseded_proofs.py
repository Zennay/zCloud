from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

RETIRED_WORKFLOWS = (
    WORKFLOW_DIR / "ftmo-pr469-exact-head-proof.yml",
    WORKFLOW_DIR / "ftmo-pr475-exact-head-proof.yml",
)

# Both source PRs are closed and explicitly superseded. Guard only the
# workflow-specific success authority here: historical target SHAs may remain
# valid evidence in other composite proofs and must not be claimed by this
# retirement slice.
SUPERSEDED_PROOF_FINGERPRINTS = (
    "FTMO_PR469_ZCLOUD_PROOF_GREEN",
    "FTMO_PR475_ZCLOUD_PROOF_GREEN",
)


class RetiredFtmoSupersededProofTests(unittest.TestCase):
    def test_superseded_exact_head_proofs_stay_retired(self):
        resurrected = [
            str(path.relative_to(ROOT))
            for path in RETIRED_WORKFLOWS
            if path.exists()
        ]
        self.assertEqual(
            [],
            resurrected,
            "superseded FTMO exact-head proof workflows must stay retired",
        )

    def test_active_workflows_do_not_restore_superseded_proof_authority(self):
        offenders = {}
        for path in sorted(WORKFLOW_DIR.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            hits = [
                marker
                for marker in SUPERSEDED_PROOF_FINGERPRINTS
                if marker in text
            ]
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits

        self.assertEqual(
            {},
            offenders,
            "superseded FTMO proof authority resurfaced",
        )


if __name__ == "__main__":
    unittest.main()
