from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-iteration-effect-gate.yml"


class IterationEffectGateWorkflowTests(unittest.TestCase):
    def test_gate_is_hosted_read_only_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$HEAD_SHA"', text)

    def test_gate_has_no_runtime_mutation_surface(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in (
            "server.py",
            "history.db",
            "systemctl",
            "sudo ",
            "runner-control",
            "portfolio_queue",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
