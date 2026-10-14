"""Fail-closed hosted zCloud cockpit CI admission without VPS access."""
from pathlib import Path
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-worker-cockpit-ui-hosted.yml")

CONDITIONS = (
    "github.repository == 'Zennay/zCloud'",
    "github.run_attempt == 1",
    "github.event_name == 'pull_request'",
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.event.pull_request.base.repo.full_name == github.repository",
    "github.event_name == 'push'",
    "github.ref == 'refs/heads/main'",
    "github.ref == 'refs/heads/fix/worker-allocation-cockpit-20261013'",
    "github.event_name == 'workflow_dispatch'",
    "github.actor == 'Zennay'",
    "github.triggering_actor == 'Zennay'",
)

RUNTIME = (
    'test "$REPOSITORY" = Zennay/zCloud',
    'test "$HEAD_REPOSITORY" = "$REPOSITORY"',
    'test "$BASE_REPOSITORY" = "$REPOSITORY"',
    'test "$ATTEMPT" = 1',
    'pull_request) test "$GITHUB_BASE_REF" = main ;;',
    'push) [[ "$EVENT_REF" == refs/heads/main || "$EVENT_REF" == refs/heads/fix/worker-allocation-cockpit-20261013 ]] ;;',
    'test "$EVENT_REF" = refs/heads/main',
    'test "$ACTOR" = Zennay',
    'test "$TRIGGERING_ACTOR" = Zennay',
    '*) echo "Unrecognized workflow event" >&2; exit 1 ;;',
    'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
)

def admission_failures(source: str) -> list[str]:
    """Source regression invariant; CI remains the GitHub expression evaluator."""
    job = source.split("\n  ui-contract:", 1)[1] if "\n  ui-contract:" in source else ""
    condition = job.split("    if: >-", 1)[1].split("    runs-on:", 1)[0] if "    if: >-" in job else ""
    step = job.split("      - name: Confirm tested candidate SHA", 1)[1].split(
        "      - name: Check JavaScript syntax", 1)[0] if "      - name: Confirm tested candidate SHA" in job else ""
    failures = ["job-if:" + item for item in CONDITIONS if item not in condition]
    failures += ["runtime:" + item for item in RUNTIME if item not in step]
    if "runs-on: ubuntu-latest" not in job:
        failures.append("hosted-runner")
    if "persist-credentials: false" not in job:
        failures.append("no-credentials")
    head_expr = "ref: " + chr(36) + "{{ github.event.pull_request.head.sha || github.sha }}"
    if head_expr not in job:
        failures.append("immutable-checkout")
    if "tests.test_deployops_cockpit_dispatch_admission_20261014_w3" not in job:
        failures.append("new-negative-regressions")
    if "tests/test_deployops_cockpit_dispatch_admission_20261014_w3.py" not in source:
        failures.append("change-trigger")
    return failures

class CockpitHostedDispatchAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = WORKFLOW.read_text(encoding="utf-8")

    def test_current_policy_complete(self):
        self.assertEqual([], admission_failures(self.src))

    def test_negative_job_admission_mutations(self):
        for condition in CONDITIONS:
            with self.subTest(condition=condition):
                changed = self.src.replace(condition, "false /* removed gate */", 1)
                self.assertIn("job-if:" + condition, admission_failures(changed))

    def test_negative_runtime_provenance_mutations(self):
        for required in RUNTIME:
            with self.subTest(required=required):
                changed = self.src.replace(required, "echo unguarded", 1)
                self.assertIn("runtime:" + required, admission_failures(changed))

    def test_manual_dispatch_requires_first_attempt_owner_main(self):
        for item in ("github.event_name == 'workflow_dispatch'",
                     "github.ref == 'refs/heads/main'",
                     "github.actor == 'Zennay'",
                     "github.triggering_actor == 'Zennay'",
                     "github.run_attempt == 1"):
            self.assertIn(item, self.src)

    def test_runner_and_credential_escape_fail_closed(self):
        self.assertIn("hosted-runner", admission_failures(
            self.src.replace("runs-on: ubuntu-latest", "runs-on: [self-hosted, zcloud, vps]", 1)))
        self.assertIn("no-credentials", admission_failures(
            self.src.replace("persist-credentials: false", "persist-credentials: true", 1)))

    def test_old_suite_and_nonprivileged_events_preserved(self):
        for item in ("fix/worker-allocation-cockpit-20261013",
                     "node tests/test_worker_debug_panel.js",
                     "node --check public/app.js",
                     "tests.test_worker_cockpit_ui_hosted_trust_20261014"):
            self.assertIn(item, self.src)

if __name__ == "__main__":
    unittest.main()
