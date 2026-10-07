from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = "ftmo-stale-ci-zssh-queue.yml"


class RetiredFtmoPr443StaleCiQueueTests(unittest.TestCase):
    def test_terminal_pr443_queue_path_is_absent(self) -> None:
        self.assertFalse(
            (WORKFLOWS / RETIRED).exists(),
            f"{RETIRED} must stay retired",
        )

    def test_terminal_pr443_queue_identity_is_not_reintroduced(self) -> None:
        markers = (
            "Enqueue FTMO stale CI cleanup on zSSH lane",
            "zssh-ftmo-cancel-superseded-pr-ci-20261002-worker1-pr443",
        )
        active = "\n".join(
            path.read_text(encoding="utf-8")
            for pattern in ("*.yml", "*.yaml")
            for path in sorted(WORKFLOWS.glob(pattern))
        )
        for marker in markers:
            self.assertNotIn(marker, active)


if __name__ == "__main__":
    unittest.main()
