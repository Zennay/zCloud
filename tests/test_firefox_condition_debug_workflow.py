from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/firefox-condition-debug.yml"


class FirefoxConditionDebugWorkflowTests(unittest.TestCase):
    def test_pr_execution_is_owner_guarded_and_exact_head(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        self.assertIn("types: [opened, synchronize, reopened]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn("github.triggering_actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "EXPECTED_ZCLOUD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn('test "$actual" = "$EXPECTED_ZCLOUD_SHA"', text)

    def test_diagnostic_is_bound_to_permanent_vps_runner(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertNotIn("runs-on: self-hosted\n", text)
        self.assertIn("cancel-in-progress: true", text)

        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("fetch-depth: 1", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(id -un)" = ubuntu', text)

        exact = "Verify exact zCloud checkout"
        guard = "Verify permanent zCloud VPS runner"
        inspect = "Inspect live Firefox unit and drop-ins"
        self.assertLess(text.index(exact), text.index(guard))
        self.assertLess(text.index(guard), text.index(inspect))

    def test_diagnostics_preserve_state_evidence_without_raw_config_dumps(self):
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("systemctl --user show chatgpt-firefox.service", text)
        self.assertIn("-p ExecCondition -p DropInPaths -p FragmentPath", text)
        self.assertIn("stat -c '%n mode=%a size=%s mtime=%Y'", text)
        self.assertIn('sha256sum "$file"', text)
        self.assertIn('sha256sum "$prestart"', text)
        self.assertIn("ZCLOUD_FIREFOX_CONDITION_DEBUG_GREEN=1", text)

        self.assertNotIn("systemctl --user cat", text)
        self.assertNotIn('cat "$1"', text)
        self.assertNotIn("sed -n '1,220p'", text)

        for forbidden in (
            "systemctl --user restart",
            "systemctl --user stop",
            "systemctl --user start",
            "sudo -n install",
            "scripts/zcloud_transactional_promote.py",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
