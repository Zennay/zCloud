import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_live_mutation_trigger_audit.py"

spec = importlib.util.spec_from_file_location("zcloud_live_mutation_trigger_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
assert spec.loader is not None
spec.loader.exec_module(audit)


class LiveMutationTriggerAuditTests(unittest.TestCase):
    def _write(self, root: Path, name: str, body: str) -> Path:
        path = root / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_detects_automatic_systemd_mutation_without_echoing_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_marker = "DO_NOT_ECHO_THIS_COMMAND_BODY"
            self._write(
                root,
                "recover.yml",
                f"""name: recover
on:
  push:
    branches: [main]
  workflow_dispatch:
jobs:
  recover:
    runs-on: self-hosted
    steps:
      - run: |
          echo {secret_marker}
          sudo -n systemctl restart zennay-cloud.service
""",
            )
            report = audit.audit_directory(root)

        self.assertTrue(report["inventory_complete"])
        self.assertFalse(report["mutation_performed"])
        self.assertEqual(report["automatic_live_mutation_candidate_count"], 1)
        record = report["records"][0]
        self.assertEqual(record["path"], "recover.yml")
        self.assertEqual(record["automatic_triggers"], ("push",))
        self.assertEqual(record["mutation_reasons"], ("systemd_mutation",))
        self.assertEqual(record["classification"], "automatic_live_mutation_candidate")
        self.assertNotIn(secret_marker, repr(report))

    def test_manual_only_mutator_is_not_automatic_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "manual.yml",
                """name: manual
on:
  workflow_dispatch:
jobs:
  mutate:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - run: sudo -n systemctl restart zennay-cloud.service
""",
            )
            report = audit.audit_directory(root)

        self.assertEqual(report["automatic_live_mutation_candidate_count"], 0)
        self.assertEqual(report["manual_or_called_mutation_candidate_count"], 1)
        self.assertEqual(
            report["records"][0]["classification"],
            "manual_or_called_mutation_candidate",
        )

    def test_queue_and_runner_control_writes_are_reason_coded(self):
        text = """name: queue
on: [schedule, workflow_dispatch]
jobs:
  queue:
    runs-on: self-hosted
    steps:
      - run: |
          python3 - <<'PY'
          import urllib.request
          urllib.request.Request(
              "http://127.0.0.1:8765/api/portfolio-queue",
              data=b"{}",
              method="POST",
          )
          urllib.request.Request(
              "http://127.0.0.1:8765/api/runner-control",
              data=b"{}",
              method="POST",
          )
          PY
"""
        triggers = audit.parse_triggers(text)
        reasons = audit.mutation_reasons(text)
        self.assertEqual(triggers, ("schedule", "workflow_dispatch"))
        self.assertIn("zcloud_queue_write", reasons)
        self.assertIn("zcloud_runner_control", reasons)
        self.assertEqual(
            audit.classify(triggers, reasons),
            "automatic_live_mutation_candidate",
        )

    def test_read_only_workflow_is_not_misclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "observe.yml",
                """name: observe
on:
  workflow_run:
    workflows: ["zCloud regression smoke"]
    types: [completed]
jobs:
  observe:
    runs-on: ubuntu-latest
    steps:
      - run: curl --fail --silent http://127.0.0.1:8765/api/status
""",
            )
            report = audit.audit_directory(root)

        record = report["records"][0]
        self.assertEqual(record["automatic_triggers"], ("workflow_run",))
        self.assertEqual(record["mutation_reasons"], ())
        self.assertEqual(record["classification"], "non_mutating_or_unclassified")

    def test_symlink_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = self._write(root, "target.txt", "on: [push]\n")
            (root / "linked.yml").symlink_to(target)
            with self.assertRaisesRegex(audit.AuditError, "symlink_workflow"):
                audit.audit_directory(root)

    def test_file_count_and_size_bounds_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "a.yml", "on: [push]\n")
            self._write(root, "b.yml", "on: [push]\n")
            with self.assertRaisesRegex(audit.AuditError, "workflow_file_limit_exceeded"):
                audit.audit_directory(root, max_files=1)
            with self.assertRaisesRegex(audit.AuditError, "workflow_file_too_large"):
                audit.audit_directory(root, max_bytes=2)

    def test_require_clear_exit_code_distinguishes_debt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "write.yml",
                """on:
  pull_request:
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - run: git push origin HEAD:main
""",
            )
            self.assertEqual(
                audit.main(["--root", str(root), "--json", "--require-clear"]),
                2,
            )


class LiveMutationTriggerAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (
            ROOT / ".github/workflows/zcloud-live-mutation-trigger-audit.yml"
        ).read_text(encoding="utf-8")

    def test_proof_runs_only_on_trusted_permanent_vps_revision(self):
        text = self.text
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_ZCLOUD_SHA"', text)
        self.assertIn('test "${RUNNER_NAME:-}" = "zcloud-vps-1"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)

    def test_proof_is_read_only_and_reports_only_bounded_summary(self):
        text = self.text
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("--require-clear", text)
        self.assertIn(
            "python3 -m unittest -v tests.test_zcloud_live_mutation_trigger_audit",
            text,
        )
        self.assertIn(
            "python3 scripts/zcloud_live_mutation_trigger_audit.py --json",
            text,
        )
        self.assertIn("ZCLOUD_LIVE_MUTATION_TRIGGER_AUDIT_GREEN", text)
        self.assertNotIn("actions/upload-artifact@", text)


if __name__ == "__main__":
    unittest.main()
