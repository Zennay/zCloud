import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/prove-workers-generating-now.yml")


class GenerationProofWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_pr_validation_never_forces_generation(self):
        self.assertIn("validate:\n    if: github.event_name == 'pull_request'", self.text)
        self.assertIn("prove:\n    if: github.event_name != 'pull_request'", self.text)
        self.assertIn("runs-on: ubuntu-latest", self.text)

    def test_live_proof_is_exact_head_and_permanent_runner_guarded(self):
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", self.text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            self.text,
        )
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', self.text)
        self.assertIn('test "$(id -un)" = "ubuntu"', self.text)
        self.assertIn("python3 scripts/zcloud_vps_runner_guard.py --json", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_database_proof_is_strictly_read_only(self):
        self.assertIn('"?mode=ro"', self.text)
        self.assertIn("sqlite3.connect(uri, uri=True", self.text)
        self.assertIn('connection.execute("PRAGMA query_only=ON")', self.text)
        self.assertIn("st_mtime_ns", self.text)
        self.assertNotIn("SELECT ts,event,reason", self.text)
        self.assertNotIn("SELECT id,action,status,result", self.text)

    def test_runtime_payload_logging_is_bounded(self):
        self.assertNotIn('print("BEFORE",', self.text)
        self.assertNotIn('print("FORCE_PUSH",', self.text)
        self.assertNotIn('print("FINAL_DEBUG",', self.text)
        self.assertNotIn('result={"error":repr(exc)}', self.text)
        self.assertIn("BEFORE_SUMMARY=", self.text)
        self.assertIn("FORCE_PUSH_SUMMARY=", self.text)
        self.assertIn("NEW_CHAT_SUMMARY=", self.text)
        self.assertIn("FINAL_DEBUG_SUMMARY=", self.text)

    def test_existing_mutation_scope_is_preserved(self):
        self.assertIn('"/api/dynamic-workers/force-push"', self.text)
        self.assertIn('"action": "new_chat"', self.text)
        self.assertNotIn('"action": "start"', self.text)
        self.assertIn("ZCLOUD_GENERATION_PROOF_GREEN=1", self.text)


if __name__ == "__main__":
    unittest.main()
