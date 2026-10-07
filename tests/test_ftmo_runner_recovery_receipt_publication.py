from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FILES = (
    "ftmo-runner-listener-recovery.yml",
    "ftmo-runner-recovery-20261002.yml",
)


class FtmoRunnerRecoveryReceiptPublicationTests(unittest.TestCase):
    def _text(self, name: str) -> str:
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_recovery_receipts_do_not_write_directly_to_main(self) -> None:
        for name in FILES:
            text = self._text(name)
            self.assertIn("contents: read", text, name)
            self.assertNotIn("contents: write", text, name)
            self.assertNotIn("git push origin HEAD:main", text, name)
            self.assertNotIn("git commit -m", text, name)
            self.assertNotIn("git reset --hard origin/main", text, name)

    def test_checkout_is_exact_and_credential_free(self) -> None:
        for name in FILES:
            text = self._text(name)
            self.assertIn("ref: ${{ github.sha }}", text, name)
            self.assertIn("persist-credentials: false", text, name)
            self.assertNotIn("ref: main", text, name)

    def test_receipts_use_immutable_bounded_artifacts(self) -> None:
        for name in FILES:
            text = self._text(name)
            self.assertRegex(text, r"actions/upload-artifact@[0-9a-f]{40}", name)
            self.assertIn("retention-days: 14", text, name)
            self.assertIn("if-no-files-found: error", text, name)

    def test_live_recovery_semantics_remain_present(self) -> None:
        listener = self._text("ftmo-runner-listener-recovery.yml")
        fallback = self._text("ftmo-runner-recovery-20261002.yml")
        self.assertIn("FTMO_RUNNER_RECOVERY_QUEUE_GREEN", listener)
        self.assertIn("restart-unhealthy-listener", listener)
        self.assertIn("zssh-ftmo-verify72-runner-recovery-20261002", fallback)
        self.assertIn("restart-idle-listener", fallback)


if __name__ == "__main__":
    unittest.main()
