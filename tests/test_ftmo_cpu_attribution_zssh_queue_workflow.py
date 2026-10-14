from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ftmo-cpu-attribution-zssh-queue.yml"


class FtmoCpuAttributionZsshQueueWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_receipt_publication_is_branch_protection_compatible(self) -> None:
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("git push origin HEAD:main", self.text)
        self.assertNotIn("git commit -m", self.text)
        self.assertNotIn("git reset --hard origin/main", self.text)

    def test_checkout_is_exact_and_credential_free(self) -> None:
        self.assertIn("ref: ${{ github.sha }}", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertNotIn("ref: main", self.text)

    def test_receipt_uses_immutable_bounded_artifact(self) -> None:
        self.assertRegex(
            self.text,
            r"actions/upload-artifact@[0-9a-f]{40}",
        )
        self.assertIn("retention-days: 14", self.text)
        self.assertIn("if-no-files-found: error", self.text)
        self.assertIn("ftmo-cpu-zssh-queue-receipt-", self.text)

    def test_live_queue_semantics_are_preserved(self) -> None:
        self.assertIn('"zssh-ftmo-cpu-attribution-proof"', self.text)
        self.assertIn('base + "/api/portfolio-queue"', self.text)
        self.assertIn("FTMO_CPU_ZSSH_QUEUE_GREEN", self.text)


if __name__ == "__main__":
    unittest.main()
