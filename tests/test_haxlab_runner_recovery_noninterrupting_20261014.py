"""Guard the historic HaxLab listener recovery from automatic/unsafe restarts.

Hosted-only source inspection: never starts, stops, probes, or reserves a VPS.
"""
from pathlib import Path
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/haxlab-runner-recovery.yml"
PIN = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SHA = "${{ github.sha }}"
INPUT = "${{ inputs.confirm_sha }}"


def problems(source: str) -> list[str]:
    errors = []
    try:
        header, job = source.split("\n  recover:\n", 1)
        events = header.split("\npermissions:", 1)[0]
        _, admission = job.split("    if: >-\n", 1)
        admission = admission.split("    runs-on:", 1)[0]
        pre, live = job.split(
            "      - name: Only start an inactive HaxLab listener; preserve active assignments\n", 1
        )
    except ValueError:
        return ["workflow_shape"]
    if "  workflow_dispatch:\n" not in events or "      confirm_sha:\n" not in events or \
            "      approve_inactive_start:\n" not in events:
        errors.append("manual_approval")
    if any("\n  " + t + ":" in events for t in (
        "push", "pull_request", "pull_request_target", "schedule", "workflow_run", "repository_dispatch"
    )):
        errors.append("automatic_trigger")
    if "\npermissions:\n  contents: read\n" not in header:
        errors.append("permissions")
    if "cancel-in-progress: false" not in header:
        errors.append("cancel_active_recovery")
    expected = (
        "github.event_name == 'workflow_dispatch'",
        "github.repository == 'Zennay/zCloud'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'Zennay'",
        "github.triggering_actor == 'Zennay'",
        "github.run_attempt == 1",
        "inputs.approve_inactive_start == true",
        "inputs.confirm_sha == github.sha",
    )
    if any(x not in admission for x in expected):
        errors.append("owner_gate")
    if "    runs-on: [self-hosted, zcloud, vps]" not in job:
        errors.append("runner_labels")
    if PIN not in job or "ref: "+SHA not in job or "persist-credentials: false" not in job:
        errors.append("immutable_checkout")
    common = (
        "CONFIRMED_SHA: "+INPUT,
        'test "$GITHUB_EVENT_NAME" = workflow_dispatch',
        'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
        'test "$GITHUB_REF" = refs/heads/main',
        'test "$GITHUB_ACTOR" = Zennay',
        'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
        'test "$GITHUB_RUN_ATTEMPT" = 1',
        'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"',
        'timeout 15s git ls-remote --exit-code origin refs/heads/main',
        'test "$live_main" = "$GITHUB_SHA"',
        'test "$(hostname)" = vps-bb300bba',
        'test "$(id -un)" = ubuntu',
        "python3 scripts/zcloud_vps_runner_guard.py --json >/dev/null",
    )
    # Require both preflight and same-step prewrite identity proofs.
    if any(x not in pre for x in common) or any(x not in live for x in common):
        errors.append("current_main_recheck")
    if "sudo -n systemctl start \"$SERVICE\"" not in live:
        errors.append("bounded_inactive_start")
    if 'if [ "$pre_state" != "active" ]; then' not in live:
        errors.append("inactive_only")
    if any(x in live for x in (
        'sudo -n systemctl restart "$SERVICE"',
        'sudo -n systemctl stop "$SERVICE"',
        "systemctl kill",
        "pkill",
        "kill -9",
    )):
        errors.append("disruptive_restart")
    if 'action=preserve-active-listener' not in live:
        errors.append("active_listener_preservation")
    if 'cmdline="$(tr' not in live or '[[ "$cmdline" == *"Runner.Listener"*' not in live:
        errors.append("process_identity")
    if 'echo "$cmdline"' in live or 'ps -ef' in live or 'env |' in live:
        errors.append("sensitive_log")
    if "timeout-minutes: 5" not in job:
        errors.append("bounded_runtime")
    return errors


class HaxLabListenerRecoverySafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_current_source_is_bounded_and_manual(self):
        self.assertEqual([], problems(self.source))

    def test_push_or_pr_cannot_start_a_listener(self):
        for event in ("push", "pull_request", "workflow_run", "schedule", "pull_request_target"):
            with self.subTest(event=event):
                bad = self.source.replace("\n  workflow_dispatch:\n", "\n  "+event+":\n  workflow_dispatch:\n")
                self.assertIn("automatic_trigger", problems(bad))

    def test_owner_original_and_triggering_actor_are_mandatory(self):
        for entry in (
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "github.ref == 'refs/heads/main'",
            "inputs.approve_inactive_start == true",
        ):
            with self.subTest(entry=entry):
                self.assertIn("owner_gate", problems(self.source.replace(entry, "true")))

    def test_confirmation_and_exact_checkout_mandatory(self):
        bad = self.source.replace("ref: "+SHA, "ref: main")
        self.assertIn("immutable_checkout", problems(bad))
        self.assertIn("owner_gate", problems(self.source.replace(
            "inputs.confirm_sha == github.sha", "true"
        )))

    def test_prewrite_main_must_still_be_current(self):
        bad = self.source.replace(
            'test "$live_main" = "$GITHUB_SHA"', 'echo "old head accepted"'
        )
        self.assertIn("current_main_recheck", problems(bad))

    def test_cannot_restart_active_listener_even_with_no_runner_worker(self):
        bad = self.source.replace('sudo -n systemctl start "$SERVICE"',
                                  'sudo -n systemctl restart "$SERVICE"')
        self.assertIn("disruptive_restart", problems(bad))

    def test_cannot_stop_active_listener(self):
        bad = self.source.replace('sudo -n systemctl start "$SERVICE"',
                                  'sudo -n systemctl stop "$SERVICE"')
        self.assertIn("disruptive_restart", problems(bad))

    def test_cannot_run_on_unlabeled_runner(self):
        self.assertIn("runner_labels", problems(self.source.replace(
            "runs-on: [self-hosted, zcloud, vps]", "runs-on: self-hosted")))

    def test_does_not_log_cmdlines(self):
        bad = self.source.replace('echo "HAXLAB_LISTENER_RECOVERY', 'echo "$cmdline"\n          echo "HAXLAB_LISTENER_RECOVERY')
        self.assertIn("sensitive_log", problems(bad))

    def test_no_cancellation_of_active_recovery(self):
        bad = self.source.replace("cancel-in-progress: false", "cancel-in-progress: true")
        self.assertIn("cancel_active_recovery", problems(bad))


if __name__ == "__main__":
    unittest.main()
