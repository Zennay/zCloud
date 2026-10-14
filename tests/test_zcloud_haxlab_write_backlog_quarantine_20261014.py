"""Source-only safety regression: legacy HaxLab backlog must not hijack workers.

An authorization review of this workflow is not a production permission. This
test runs only on GitHub-hosted runners and never touches the live queue.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github/workflows/haxlab-write-backlog.yml"
PIN = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
SHA = "${{ github.sha }}"
INPUT = "${{ inputs.confirm_sha }}"


def problems(source):
    issues = []
    try:
        header, job = source.split("\n  normalize:\n", 1)
        events = header.split("\npermissions:", 1)[0]
    except ValueError:
        return ["shape"]
    if "  workflow_dispatch:\n" not in events or \
            "      confirm_sha:\n" not in events or \
            "      approve_queue_mutations:\n" not in events:
        issues.append("explicit_manual_admission")
    if any("\n  "+event+":" in events for event in
           ("push","pull_request","pull_request_target","schedule","workflow_run","repository_dispatch")):
        issues.append("automatic_writer")
    if "\npermissions:\n  contents: read\n" not in header:
        issues.append("least_privilege")
    gate = job.split("    runs-on:", 1)[0]
    needed = (
        "github.event_name == 'workflow_dispatch'",
        "github.repository == 'Zennay/zCloud'",
        "github.ref == 'refs/heads/main'",
        "github.actor == 'Zennay'",
        "github.triggering_actor == 'Zennay'",
        "github.run_attempt == 1",
        "inputs.approve_queue_mutations == true",
        "inputs.confirm_sha == github.sha",
    )
    if any(token not in gate for token in needed):
        issues.append("actor_scope")
    if "runs-on: [self-hosted, zcloud, vps]" not in job:
        issues.append("runner")
    if PIN not in job or "actions/checkout@v4" in job:
        issues.append("checkout_pin")
    if "ref: "+SHA not in job or "persist-credentials: false" not in job:
        issues.append("immutable_checkout")
    trust = (
        "EXPECTED_SHA: "+SHA,
        "CONFIRMED_SHA: "+INPUT,
        'test "$EXPECTED_REPO" = "Zennay/zCloud"',
        'test "$CONFIRMED_SHA" = "$EXPECTED_SHA"',
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "git ls-remote --exit-code origin refs/heads/main",
        'test "$(hostname)" = "vps-bb300bba"',
        'test "$(id -un)" = "ubuntu"',
        "python3 scripts/zcloud_vps_runner_guard.py --json",
    )
    if any(token not in job for token in trust):
        issues.append("fresh_main")
    if job.find("python3 scripts/zcloud_vps_runner_guard.py --json") >= \
            job.find('python3 - <<\'PY\'') or \
            job.find("python3 scripts/zcloud_vps_runner_guard.py --json") < 0:
        issues.append("prewrite_order")
    if 'if row.get("project_id")!="haxlab":' not in job or \
            'print("FOREIGN_QUEUE_ITEM_UNTOUCHED")' not in job:
        issues.append("foreign_project")
    if '"result":"DONE"' in job or '"result":"CONTINUE"' in job:
        issues.append("active_worker_preemption")
    if 'print("ACTIVE_OWNERSHIP_PRESERVED"' not in job:
        issues.append("active_owner")
    if "legacy synthetic P3 continuation is occupying" in job or \
            'waiting_hax_p1' in job:
        issues.append("legacy_cross_project_preemption")
    if 'result.get("ok") is not True' not in job:
        issues.append("mutation_ack_required")
    if 'base+"/api/dynamic-workers"' in job or 'post("/api/dynamic-workers"' in job:
        issues.append("global_pool_write")
    if any(raw in job for raw in (
        'json.dumps(reconciled,sort_keys=True)',
        'print("ALLOCATION_FINAL"',
        'print("HAXLAB_ELIGIBLE_FINAL"',
        'json.dumps(result,sort_keys=True)',
    )):
        issues.append("raw_data_logging")
    for required in ('"action":"enqueue"', 'post("/api/portfolio-queue"', "get_queue()"):
        if required not in job and not (required == 'post("/api/portfolio-queue"' and 'base+"/api/portfolio-queue"' in job):
            issues.append("canonical_work_preservation")
            break
    return issues


class HaxLabWriteBacklogSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SOURCE.read_text(encoding="utf-8")

    def test_current_source_passes_policy(self):
        self.assertEqual([], problems(self.text))

    def test_never_automatically_writes(self):
        for e in ("push","pull_request","workflow_run","schedule","pull_request_target"):
            with self.subTest(event=e):
                bad = self.text.replace("  workflow_dispatch:", "  "+e+":\n  workflow_dispatch:")
                self.assertIn("automatic_writer", problems(bad))

    def test_missing_confirmation_denied(self):
        self.assertIn("explicit_manual_admission", problems(
            self.text.replace("      confirm_sha:\n", "      unsafe:\n")))

    def test_actors_and_run_attempt_not_optional(self):
        for token in ("github.actor == 'Zennay'", "github.triggering_actor == 'Zennay'",
                      "github.run_attempt == 1", "inputs.approve_queue_mutations == true"):
            with self.subTest(token=token):
                self.assertIn("actor_scope", problems(self.text.replace(token, "true")))

    def test_wrong_repo_or_main_ref_denied(self):
        for token in ("github.repository == 'Zennay/zCloud'",
                      "github.ref == 'refs/heads/main'",
                      "inputs.confirm_sha == github.sha"):
            with self.subTest(token=token):
                self.assertIn("actor_scope", problems(self.text.replace(token, "true")))

    def test_must_use_immutable_checkout(self):
        self.assertIn("checkout_pin", problems(self.text.replace(PIN, "actions/checkout@v4")))
        self.assertIn("immutable_checkout", problems(self.text.replace("ref: "+SHA, "ref: main")))

    def test_permanent_runner_required(self):
        self.assertIn("runner", problems(self.text.replace(
            "[self-hosted, zcloud, vps]", "self-hosted")))

    def test_fresh_main_and_host_required(self):
        for token in ("git ls-remote --exit-code origin refs/heads/main",
                      'test "$(hostname)" = "vps-bb300bba"',
                      "python3 scripts/zcloud_vps_runner_guard.py --json"):
            with self.subTest(token=token):
                self.assertIn("fresh_main", problems(self.text.replace(token, "echo bypass")))

    def test_no_foreign_task_reprioritization(self):
        self.assertIn("foreign_project", problems(
            self.text.replace('if row.get("project_id")!="haxlab":', "if False:")))

    def test_no_active_task_completion_or_cross_project_preemption(self):
        self.assertIn("active_worker_preemption", problems(
            self.text.replace('"action":"enqueue"', '"result":"DONE"')))
        self.assertIn("active_worker_preemption", problems(
            self.text.replace('"action":"enqueue"', '"result":"CONTINUE"')))

    def test_queue_error_cannot_be_ignored(self):
        broken = self.text.replace('result.get("ok") is not True', "False")
        self.assertIn("mutation_ack_required", problems(broken))

    def test_global_worker_pool_writes_are_denied(self):
        self.assertNotIn('base+"/api/dynamic-workers"', self.text)
        injected = self.text.replace('final=get_queue()',
            'urllib.request.urlopen(base+"/api/dynamic-workers")\n          final=get_queue()')
        self.assertIn("global_pool_write", problems(injected))

    def test_no_raw_worker_or_allocation_payload_log(self):
        self.assertIn("raw_data_logging", problems(
            self.text.replace('print("HAXLAB_ELIGIBLE_COUNT",len(hax))',
                              'print("ALLOCATION_FINAL",json.dumps(final.get("allocation"),sort_keys=True))')))

    def test_original_global_pool_reconcile_is_absent(self):
        self.assertNotIn('same_settings', self.text)
        self.assertNotIn('WORKER_POOL_RECONCILED', self.text)
        self.assertNotIn('X-ZCloud-Actor', self.text)


if __name__ == "__main__":
    unittest.main()
