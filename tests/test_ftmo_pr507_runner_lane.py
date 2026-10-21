from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ftmo-runner-self-health-proof-20261002.yml"
PROOF_WORKFLOW = ROOT / ".github/workflows/ftmo-runner-self-health-contract.yml"


class FtmoDedicatedRunnerLaneTests(unittest.TestCase):
    def test_current_ftmo_runner_proof_uses_dedicated_ftmo_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, ftmo-research]", text)
        self.assertNotIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)

    def test_current_ftmo_runner_proof_remains_observation_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(
            'paths:\n      - ".github/workflows/ftmo-runner-self-health-proof-20261002.yml"',
            text,
        )
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("sqlite3", text)
        self.assertNotIn("server.py", text)

        for mutation in (
            "systemctl start",
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "systemctl disable",
            "systemctl daemon-reload",
        ):
            self.assertNotIn(mutation, text)

        host_guard = text.index('test "$(hostname)" = "vps-bb300bba"')
        runner_guard = text.index('test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"')
        first_live_observation = text.index('state="$(systemctl is-active "$SERVICE" || true)"')
        self.assertLess(host_guard, first_live_observation)
        self.assertLess(runner_guard, first_live_observation)
        self.assertIn("action=preserve-active-runner", text)

    def test_heavy_runner_diagnostics_are_failure_and_identity_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        failure_step = text.index("- name: Capture failure-only runner diagnostics")
        failure_path = text[failure_step:]
        failure_gate = failure_path.index("if: failure()")
        failure_host_guard = failure_path.index('test "$(hostname)" = "vps-bb300bba"')
        failure_runner_guard = failure_path.index(
            'test "${RUNNER_NAME:-}" = "vps-bb300bba-ftmo"'
        )
        journal = failure_path.index("journalctl")
        process_dump = failure_path.index("ps -o pid=,ppid=,lstart=,etime=,cmd=")

        success_path = text[:failure_step]
        self.assertNotIn("journalctl", success_path)
        self.assertNotIn("ps -o pid=,ppid=,lstart=,etime=,cmd=", success_path)
        self.assertLess(failure_gate, failure_host_guard)
        self.assertLess(failure_gate, failure_runner_guard)
        self.assertLess(failure_host_guard, journal)
        self.assertLess(failure_host_guard, process_dump)
        self.assertLess(failure_runner_guard, journal)
        self.assertLess(failure_runner_guard, process_dump)

    def test_contract_proof_is_same_repo_read_only_and_guarded(self):
        text = PROOF_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == github.repository_owner", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("needs: validate", text)
        self.assertIn(
            "python3 -m unittest -v tests.test_ftmo_pr507_runner_lane",
            text,
        )

        for forbidden in (
            "contents: write",
            "actions: write",
            "git push",
            "sqlite3",
            "server.py",
            "systemctl start",
            "systemctl restart",
            "systemctl stop",
            "systemctl enable",
            "systemctl disable",
            "systemctl daemon-reload",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
