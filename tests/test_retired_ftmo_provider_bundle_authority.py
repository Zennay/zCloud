import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED_FTMO_PROVIDER_BUNDLE_AUTHORITY = {
    ".github/workflows/ftmo-pr496-research-state-sync-proof.yml":
        "FTMO PR #496 closed and superseded",
    ".github/workflows/ftmo-pr518-provider-bundle-proof.yml":
        "FTMO PR #518 closed+merged",
    ".github/workflows/ftmo-pr521-provider-bundle-integration-proof.yml":
        "FTMO PR #521 closed+merged",
    ".github/workflows/ftmo-pr521-runner-recovery.yml":
        "FTMO PR #521 closed+merged and historical recovery target is terminal",
}


class RetiredFtmoProviderBundleAuthorityTests(unittest.TestCase):
    def test_terminal_provider_bundle_authority_stays_retired(self):
        for relative_path, basis in RETIRED_FTMO_PROVIDER_BUNDLE_AUTHORITY.items():
            with self.subTest(workflow=relative_path, basis=basis):
                self.assertFalse(
                    (ROOT / relative_path).exists(),
                    f"{relative_path} was retired because {basis}; "
                    "use a fresh narrowly-scoped workflow for new evidence or recovery",
                )


if __name__ == "__main__":
    unittest.main()
