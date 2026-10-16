"""Real Git-fixture regressions for source-only workflow_run recursion admission."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.zcloud_workflow_run_dispatch_gate_20261016_w34 import (
    EvidenceError, classify, evaluate, main, workflow_run_event,
)

WORKFLOW = ".github/workflows/loop.yml"
TEMPLATE = """name: offline proof
on:
  workflow_run:
    workflows: [zCloud regression]
    types: [completed]
permissions:
  contents: read
jobs:
  proof:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo harmless
"""
DANGEROUS = TEMPLATE.replace("echo harmless", "gh workflow run unsafe.yml")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Gate test")
        self.git("config", "user.email", "gate@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo).decode().strip()

    def commit(self, content, name=WORKFLOW):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_new_recursion_rejected(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS)
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["new_findings"], [WORKFLOW])
        self.assertFalse(report["production_mutation_authorized"])
        self.assertFalse(report["merge_authorized"])
        self.assertEqual(main(["--root", str(self.repo), "--base", base, "--head", head]), 1)

    def test_unchanged_legacy_is_inventory_only(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS + "\n# only comment\n")
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["new_findings"], [])

    def test_second_identical_dispatch_in_existing_workflow_is_denied(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace(
            "gh workflow run unsafe.yml",
            "gh workflow run unsafe.yml\n          gh workflow run unsafe.yml"))
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["current_findings"], 2)
        self.assertEqual(report["new_findings"], [WORKFLOW])

    def test_changed_dispatch_target_is_new_risk(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace("unsafe.yml", "different.yml"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_benign_adjacent_edit_preserves_legacy_fingerprint(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS + "\n# unrelated note\n")
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_reduced_debt_is_allowed(self):
        base = self.commit(DANGEROUS)
        head = self.commit(TEMPLATE)
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_safe_trigger_does_not_count(self):
        base = self.commit(TEMPLATE.replace("workflow_run:", "push:"))
        head = self.commit(DANGEROUS.replace("workflow_run:", "push:"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_real_git_addition_and_symlink_fail_closed(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS, ".github/workflows/new.yml")
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [".github/workflows/new.yml"])
        (self.repo / WORKFLOW).unlink()
        (self.repo / WORKFLOW).symlink_to("/etc/passwd")
        self.git("add", ".")
        self.git("commit", "-qm", "symlink")
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, base, self.git("rev-parse", "HEAD"))

    def test_oversized_workflow_denies_before_reading_blob(self):
        from unittest.mock import patch
        from scripts import zcloud_workflow_run_dispatch_gate_20261016_w34 as gate

        base = self.commit(TEMPLATE)
        oversized = TEMPLATE + ("#" * (gate.MAX_SOURCE + 1))
        head = self.commit(oversized)
        with patch.object(gate, "git", wraps=gate.git) as git_call:
            with self.assertRaisesRegex(EvidenceError, "oversized_workflow"):
                evaluate(self.repo, base, head)
        # Metadata preflight must prevent capture of attacker-sized blobs.
        blobs = [call for call in git_call.call_args_list
                 if len(call.args) >= 3 and call.args[1:3] == ("cat-file", "blob")]
        self.assertEqual(len(blobs), 1)  # only the small baseline source

    def test_workflow_blob_size_must_match_git_metadata(self):
        from unittest.mock import patch
        from scripts import zcloud_workflow_run_dispatch_gate_20261016_w34 as gate

        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS)
        original = gate.git
        def swapped_blob(root, *args):
            data = original(root, *args)
            if len(args) >= 3 and args[:2] == ("cat-file", "blob"):
                return data + b" "
            return data

        with patch.object(gate, "git", side_effect=swapped_blob):
            with self.assertRaisesRegex(EvidenceError, "inconsistent_blob_size"):
                evaluate(self.repo, base, head)

    def test_nonancestor_rejected(self):
        base = self.commit(TEMPLATE)
        head = self.commit(DANGEROUS)
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, head, base)

    def test_malformed_sha_rejected(self):
        base = self.commit(TEMPLATE)
        with self.assertRaises(EvidenceError):
            evaluate(self.repo, "main", base)

    def test_missing_duplicate_or_aliased_on_rejected(self):
        for src in ["name: no event\n", "on: push\non: workflow_run\n",
                    "on: *danger\n"]:
            with self.assertRaises(EvidenceError):
                workflow_run_event(src)

    def test_inline_trigger_alias_and_ambiguous_list_deny(self):
        malformed = (
            "on: [*untrusted]\njobs:\n",
            "on: [push, *untrusted]\njobs:\n",
            "on: [workflow_run, *untrusted]\njobs:\n",
            "on: {push: {}, workflow_run: *untrusted}\njobs:\n",
            "on: {<<: *defaults, workflow_run: {}}\njobs:\n",
            "on: [push, workflow_run, workflow_run]\njobs:\n",
            "on: [push, !custom]\njobs:\n",
            "on: []\njobs:\n",
        )
        for source in malformed:
            with self.subTest(source=source), self.assertRaises(EvidenceError):
                workflow_run_event(source)

    def test_inline_map_duplicate_events_are_unverifiable(self):
        for source in (
            "on: {workflow_run: {types: [completed]}, workflow_run: {types: [requested]}}\njobs:\n",
            "on: {'push': {}, \"push\": {}}\njobs:\n",
            "on: {push: {}, 'push': {}}\njobs:\n",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                EvidenceError, "duplicate_inline_event_mapping"
            ):
                workflow_run_event(source)

    def test_inline_map_distinct_event_keys_still_classify(self):
        self.assertTrue(workflow_run_event(
            'on: {"workflow_run": {types: [completed]}, push: {}}\njobs:\n'
        ))
        self.assertFalse(workflow_run_event(
            'on: {"push": {types: [completed]}, workflow_dispatch: {}}\njobs:\n'
        ))

    def test_literal_inline_trigger_collections_still_classify(self):
        for source in (
            "on: [workflow_run]\njobs:\n",
            "on: ['push', \"workflow_run\"]\njobs:\n",
            "on: {workflow_run: {types: [completed]}, push: {}}\njobs:\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(workflow_run_event(source))

    def test_run_body_detection(self):
        self.assertTrue(classify(DANGEROUS))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "gh run rerun 123")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "gh api -X POST /repos/Zennay/zCloud/actions/workflows/a.yml/dispatches")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "curl -X POST https://api.github.com/repos/Zennay/zCloud/dispatches")))
        self.assertTrue(classify(TEMPLATE.replace("echo harmless", "octokit.rest.actions.createWorkflowDispatch({})")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "# gh workflow run old.yml\necho safe")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "echo safe\n# gh workflow run old.yml")))
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", "echo safe").replace("on:\n  workflow_run:", "on: [push]")))
        self.assertFalse(classify(TEMPLATE + "\n# gh workflow run docs\n"))

    def test_dispatch_flag_order_and_implicit_post(self):
        commands = (
            "gh api /repos/Zennay/zCloud/actions/workflows/x.yml/dispatches -X POST",
            "gh api /repos/Zennay/zCloud/actions/workflows/x.yml/dispatches -f ref=main",
            "gh api --method=POST /repos/Zennay/zCloud/dispatches",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches -X POST",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches --data '{ }'",
            "gh -R Zennay/zCloud workflow run x.yml",
            "gh --repo=Zennay/zCloud run rerun 123",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))


    def test_rest_workflow_rerun_post_is_recursion_risk(self):
        for command in (
            "gh api -X POST /repos/Zennay/zCloud/actions/runs/123/rerun",
            "gh api /repos/Zennay/zCloud/actions/runs/123/rerun-failed-jobs --method POST",
            "gh api /repos/Zennay/zCloud/actions/runs/123/rerun -f anything=value",
            "curl --request POST https://api.github.com/repos/Zennay/zCloud/actions/runs/123/rerun",
            "curl https://api.github.com/repos/Zennay/zCloud/actions/runs/123/rerun-failed-jobs --data '{}'",
            "octokit.rest.actions.reRunWorkflow({ run_id: 123 })",
            "octokit.rest.actions.reRunWorkflowFailedJobs({run_id: 123})",
            "octokit.rest.actions.reRunJob({job_id: 42})",
        ):
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_multiple_gh_global_flags_deny_recursive_dispatch(self):
        commands = (
            "gh --hostname github.com --repo Zennay/zCloud workflow run backfill.yml",
            "gh -R Zennay/zCloud --hostname github.com run rerun 123",
            "gh --repo=Zennay/zCloud --hostname github.com api -X POST /repos/Zennay/zCloud/dispatches",
            "gh --hostname github.com -R Zennay/zCloud api /repos/Zennay/zCloud/actions/runs/123/rerun -X POST",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_more_than_four_gh_global_flags_cannot_hide_dispatch(self):
        flags = " ".join(["--hostname github.com"] * 5)
        for command in (
            "gh " + flags + " --repo Zennay/zCloud workflow run other.yml",
            "gh " + flags + " api -X POST /repos/Zennay/zCloud/actions/jobs/42/rerun",
        ):
            with self.subTest(command=command[-55:]):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_many_gh_flags_read_only_get_is_not_dispatch(self):
        flags = " ".join(["--hostname github.com"] * 6)
        command = (
            "gh " + flags + " api -X GET "
            "/repos/Zennay/zCloud/actions/jobs/42/rerun"
        )
        self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_multiple_gh_global_flags_get_not_mutation(self):
        source = TEMPLATE.replace(
            "echo harmless",
            "gh --hostname github.com -R Zennay/zCloud api /repos/Zennay/zCloud/actions/runs/123/rerun -X GET",
        )
        self.assertFalse(classify(source))

    def test_global_flags_added_to_legacy_trigger_are_new_debt(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            "gh --hostname github.com --repo Zennay/zCloud api /repos/Zennay/zCloud/dispatches -X POST",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_rest_job_rerun_post_is_recursion_risk(self):
        for command in (
            "gh api -X POST /repos/Zennay/zCloud/actions/jobs/42/rerun",
            "gh api /repos/Zennay/zCloud/actions/jobs/42/rerun --method POST",
            "gh api /repos/Zennay/zCloud/actions/jobs/42/rerun -f enable_debug_logging=true",
            "curl -X POST https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
            "curl https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun -d '{}' ",
        ):
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_long_api_command_cannot_hide_dispatch_past_scan_limit(self):
        padding = "x" * 550
        for command in (
            "gh api --header 'X-Audit-Pad: " + padding
            + "' -X POST /repos/Zennay/zCloud/actions/jobs/42/rerun",
            "curl --header 'X-Audit-Pad: " + padding
            + "' -X POST https://api.github.com/repos/Zennay/zCloud/dispatches",
        ):
            with self.subTest(binary=command[:8]):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_long_read_only_api_command_is_not_dispatch_debt(self):
        padding = "x" * 550
        for command in (
            "gh api --header 'X-Audit-Pad: " + padding
            + "' -X GET /repos/Zennay/zCloud/actions/jobs/42/rerun",
            "curl --header 'X-Audit-Pad: " + padding
            + "' -X GET https://api.github.com/repos/Zennay/zCloud/dispatches",
        ):
            with self.subTest(binary=command[:8]):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_long_dispatch_is_new_finding_in_real_git(self):
        padding = "x" * 550
        command = (
            "gh api --header 'X-Audit-Pad: " + padding
            + "' -X POST /repos/Zennay/zCloud/actions/jobs/42/rerun"
        )
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace("echo harmless", command))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_wget_post_dispatch_and_rerun_denied(self):
        commands = (
            "wget --method=POST https://api.github.com/repos/Zennay/zCloud/dispatches",
            "wget --post-data='{}' https://api.github.com/repos/Zennay/zCloud/actions/workflows/a.yml/dispatches",
            "wget --post-file=request.json https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
            "wget https://api.github.com/repos/Zennay/zCloud/actions/runs/14/rerun --method POST",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_powershell_post_dispatch_and_rerun_denied(self):
        commands = (
            "Invoke-RestMethod -Method POST -Uri https://api.github.com/repos/Zennay/zCloud/dispatches",
            "Invoke-WebRequest -Uri https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun -Method 'POST'",
            'Invoke-RestMethod -Method "POST" -Uri https://api.github.com/repos/Zennay/zCloud/actions/workflows/a.yml/dispatches',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_octokit_generic_post_request_dispatch_risk(self):
        commands = (
            'node -e \'octokit.request("POST /repos/Zennay/zCloud/dispatches", {})\'',
            "node -e 'github.request(\"POST /repos/Zennay/zCloud/actions/jobs/42/rerun\")'",
            "node -e 'octokit.request(\"POST /repos/Zennay/zCloud/actions/workflows/a.yml/dispatches\")'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_octokit_generic_get_request_is_not_dispatch_risk(self):
        commands = (
            'node -e \'octokit.request("GET /repos/Zennay/zCloud/dispatches")\'',
            "node -e 'github.request(\"GET /repos/Zennay/zCloud/actions/jobs/42/rerun\")'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_octokit_generic_post_adds_new_immutable_debt(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            "node -e 'octokit.request(\"POST /repos/Zennay/zCloud/dispatches\")'",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_python_http_sessions_and_clients_dispatch_post(self):
        examples = (
            'session.post("https://api.github.com/repos/Zennay/zCloud/dispatches")',
            'client.post("https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun")',
            'http_client.request("POST", "https://api.github.com/repos/Zennay/zCloud/dispatches")',
            'requests.Session().post("https://api.github.com/repos/Zennay/zCloud/dispatches")',
            'httpx.Client().post("https://api.github.com/repos/Zennay/zCloud/actions/runs/42/rerun")',
            'httpx.AsyncClient().request("POST", "https://api.github.com/repos/Zennay/zCloud/dispatches")',
        )
        for command in examples:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_python_session_read_only_calls_do_not_report_dispatch(self):
        examples = (
            'session.get("https://api.github.com/repos/Zennay/zCloud/dispatches")',
            'client.request("GET", "https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun")',
            'httpx.Client().request("GET", "https://api.github.com/repos/Zennay/zCloud/dispatches")',
        )
        for command in examples:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_http_session_dispatch_in_immutable_git_is_new_debt(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            'session.post("https://api.github.com/repos/Zennay/zCloud/dispatches")',
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_multiline_http_calls_cannot_hide_recursive_post(self):
        multiline = (
            'fetch(\n "https://api.github.com/repos/Zennay/zCloud/dispatches",\n {method:"POST"}\n)',
            'requests.post(\n "https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",\n json={}\n)',
            'httpx.request(\n "POST",\n "https://api.github.com/repos/Zennay/zCloud/actions/runs/42/rerun"\n)',
            'axios.post(\n "https://api.github.com/repos/Zennay/zCloud/dispatches",\n {}\n)',
            'octokit.request(\n "POST /repos/Zennay/zCloud/dispatches",\n {}\n)',
        )
        for code in multiline:
            with self.subTest(code=code.splitlines()[0]):
                source = TEMPLATE.replace("echo harmless", code.replace("\n", "\n          "))
                self.assertTrue(classify(source))
                base = self.commit(TEMPLATE)
                head = self.commit(source)
                self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_multiline_http_get_is_not_recursive_dispatch(self):
        examples = (
            'fetch(\n "https://api.github.com/repos/Zennay/zCloud/dispatches",\n {method:"GET"}\n)',
            'requests.get(\n "https://api.github.com/repos/Zennay/zCloud/dispatches"\n)',
            'octokit.request(\n "GET /repos/Zennay/zCloud/dispatches",\n {}\n)',
        )
        for code in examples:
            with self.subTest(code=code.splitlines()[0]):
                source = TEMPLATE.replace("echo harmless", code.replace("\n", "\n          "))
                self.assertFalse(classify(source))

    def test_adjacent_lines_not_merged_into_single_http_call(self):
        source = TEMPLATE.replace(
            "echo harmless",
            'fetch("https://api.github.com/repos/Zennay/zCloud/dispatches")\n'
            '          echo "method: POST"',
        )
        self.assertFalse(classify(source))

    def test_node_fetch_with_literal_post_dispatch(self):
        commands = (
            'node -e \'fetch("https://api.github.com/repos/Zennay/zCloud/dispatches", {method: "POST"})\'',
            'node -e \'fetch("https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun", {"method": "POST"})\'',
            "node -e 'fetch(\"https://api.github.com/repos/Zennay/zCloud/actions/runs/42/rerun\", {method: \"POST\"})'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_node_fetch_without_mutating_method_is_read_only(self):
        commands = (
            'node -e \'fetch("https://api.github.com/repos/Zennay/zCloud/dispatches")\'',
            'node -e \'fetch("https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun", {method: "GET"})\'',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_axios_post_to_dispatch_endpoint_is_risky(self):
        commands = (
            "node -e 'axios.post(\"https://api.github.com/repos/Zennay/zCloud/dispatches\", {})'",
            "node -e 'axios.post(\"https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun\", {})'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))
        self.assertFalse(classify(TEMPLATE.replace(
            "echo harmless",
            "node -e 'axios.get(\"https://api.github.com/repos/Zennay/zCloud/dispatches\")'",
        )))

    def test_js_http_recursion_adds_new_git_finding(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            "node -e 'axios.post(\"https://api.github.com/repos/Zennay/zCloud/dispatches\", {})'",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_python_http_clients_dispatch_post_is_recursion_risk(self):
        commands = (
            'python -c \'requests.post("https://api.github.com/repos/Zennay/zCloud/dispatches")\'',
            "python -c 'httpx.post(\"https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun\")'",
            'python -c \'requests.request("POST", "https://api.github.com/repos/Zennay/zCloud/actions/workflows/a.yml/dispatches")\'',
            'python -c \'httpx.request("POST", "https://api.github.com/repos/Zennay/zCloud/actions/runs/18/rerun")\'',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_python_http_clients_read_only_get_not_dispatch_risk(self):
        commands = (
            'python -c \'requests.get("https://api.github.com/repos/Zennay/zCloud/dispatches")\'',
            'python -c \'httpx.request("GET", "https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun")\'',
            'python -c \'requests.request("GET", "https://api.github.com/repos/Zennay/zCloud/dispatches")\'',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_python_http_request_post_adds_real_git_debt(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            'python -c \'httpx.request("POST", "https://api.github.com/repos/Zennay/zCloud/dispatches")\'',
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_powershell_http_aliases_post_are_recursive_dispatch(self):
        commands = (
            "irm -Method POST -Uri https://api.github.com/repos/Zennay/zCloud/dispatches",
            "iwr -Uri https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun -Method 'POST'",
            'irm -Method "POST" -Uri https://api.github.com/repos/Zennay/zCloud/actions/workflows/a.yml/dispatches',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(classify(TEMPLATE.replace("echo harmless", command)))

    def test_powershell_http_aliases_get_do_not_raise_debt(self):
        commands = (
            "irm -Method GET -Uri https://api.github.com/repos/Zennay/zCloud/dispatches",
            "iwr https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_alias_post_is_new_immutable_git_finding(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            "iwr -Method POST -Uri https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_wget_and_powershell_get_are_not_mutating_dispatch(self):
        commands = (
            "wget https://api.github.com/repos/Zennay/zCloud/dispatches",
            "wget --method=GET https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
            "Invoke-RestMethod -Method GET -Uri https://api.github.com/repos/Zennay/zCloud/dispatches",
            "Invoke-WebRequest -Uri https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_added_wget_or_powershell_dispatch_is_new_debt(self):
        base = self.commit(TEMPLATE)
        head = self.commit(TEMPLATE.replace(
            "echo harmless",
            "wget --post-data='{}' https://api.github.com/repos/Zennay/zCloud/dispatches",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])
        another = self.commit(TEMPLATE.replace(
            "echo harmless",
            "Invoke-RestMethod -Method POST -Uri https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
        ))
        self.assertEqual(evaluate(self.repo, base, another)["new_findings"], [WORKFLOW])

    def test_rest_job_rerun_get_remains_non_mutating(self):
        for command in (
            "gh api /repos/Zennay/zCloud/actions/jobs/42/rerun",
            "gh api /repos/Zennay/zCloud/actions/jobs/42/rerun -X GET",
            "curl -X GET https://api.github.com/repos/Zennay/zCloud/actions/jobs/42/rerun",
        ):
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_rest_job_rerun_in_existing_workflow_is_new_debt(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace(
            "gh workflow run unsafe.yml",
            "gh workflow run unsafe.yml\n"
            "          gh api -X POST /repos/Zennay/zCloud/actions/jobs/42/rerun",
        ))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_rest_workflow_rerun_get_is_not_a_post(self):
        for command in (
            "gh api /repos/Zennay/zCloud/actions/runs/123/rerun",
            "curl -X GET https://api.github.com/repos/Zennay/zCloud/actions/runs/123/rerun",
        ):
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))

    def test_added_rest_rerun_to_existing_dispatch_workflow_is_new_debt(self):
        base = self.commit(DANGEROUS)
        head = self.commit(DANGEROUS.replace(
            "gh workflow run unsafe.yml",
            "gh workflow run unsafe.yml\n          gh api -X POST /repos/Zennay/zCloud/actions/runs/123/rerun"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_read_only_dispatch_endpoint_is_not_a_post(self):
        for command in (
            "gh api /repos/Zennay/zCloud/dispatches",
            "curl https://api.github.com/repos/Zennay/zCloud/dispatches",
            "gh api -X GET /repos/Zennay/zCloud/dispatches",
            "curl -X GET https://api.github.com/repos/Zennay/zCloud/dispatches",
        ):
            with self.subTest(command=command):
                self.assertFalse(classify(TEMPLATE.replace("echo harmless", command)))


    def test_dispatch_actions_are_counted_as_recursive_sites(self):
        for action in (
            "peter-evans/repository-dispatch@v3",
            "benc-uk/workflow-dispatch@v1",
            "convictional/trigger-workflow-and-wait@v1",
        ):
            with self.subTest(action=action):
                source = TEMPLATE.replace(
                    "      - run: |\n          echo harmless",
                    "      - uses: " + action + "\n        with:\n          event-type: ping",
                )
                self.assertTrue(classify(source))
        self.assertFalse(classify(TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - uses: actions/checkout@v4",
        )))

    def test_dispatch_action_changed_input_is_new_debt(self):
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - name: Release notice\n        uses: peter-evans/repository-dispatch@v3\n        with:\n          event-type: trigger",
        )
        base = self.commit(source)
        head = self.commit(source.replace("event-type: trigger", "event-type: different"))
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_dispatch_action_reordered_step_keys_detected(self):
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - with:\n          event-type: ping\n        uses: peter-evans/repository-dispatch@v3",
        )
        self.assertTrue(classify(source))

    def test_dispatch_action_duplication_is_new_debt(self):
        original_step = "      - uses: peter-evans/repository-dispatch@v3"
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless", original_step)
        base = self.commit(source)
        head = self.commit(source.replace(original_step, original_step+"\n"+original_step))
        report = evaluate(self.repo, base, head)
        self.assertEqual(report["previous_findings"], 1)
        self.assertEqual(report["current_findings"], 2)
        self.assertEqual(report["new_findings"], [WORKFLOW])


    def test_folded_yaml_dispatch_post_infers_single_command(self):
        for command in (
            "gh api\n          --input request.json\n          /repos/Zennay/zCloud/actions/workflows/a.yml/dispatches",
            "curl\n          --form ref=main\n          https://api.github.com/repos/Zennay/zCloud/actions/workflows/a.yml/dispatches",
            "curl\n          -d@request.json\n          https://api.github.com/repos/Zennay/zCloud/dispatches",
        ):
            with self.subTest(command=command):
                source = TEMPLATE.replace("      - run: |", "      - run: >-").replace(
                    "echo harmless", command)
                self.assertTrue(classify(source))

    def test_literal_yaml_multiline_get_remains_read_only(self):
        source = TEMPLATE.replace("      - run: |", "      - run: >-").replace(
            "echo harmless", "curl\n          -X GET\n          https://api.github.com/repos/Zennay/zCloud/dispatches")
        self.assertFalse(classify(source))

    def test_hosted_validation_requires_trusted_first_push(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/zcloud-workflow-run-dispatch-ratchet-20261016-w34.yml")
        source = workflow.read_text("utf-8")
        for expected in (
            "github.event_name == 'push'",
            "github.repository == 'Zennay/zCloud'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "persist-credentials: false",
            "submodules: false",
            "contents: read",
            "runs-on: ubuntu-latest",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)
        self.assertNotIn("runs-on: [self-hosted", source)


    def test_quoted_yaml_step_keys_cannot_hide_dispatch(self):
        for quoted in ("'run'", '"run"'):
            with self.subTest(key=quoted):
                source = TEMPLATE.replace("      - run: |", "      - " + quoted + ": |")
                source = source.replace("echo harmless", "gh workflow run hidden.yml")
                self.assertTrue(classify(source))
                base = self.commit(TEMPLATE)
                head = self.commit(source)
                self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])
        for quoted in ("'uses'", '"uses"'):
            with self.subTest(key=quoted):
                source = TEMPLATE.replace(
                    "      - run: |\n          echo harmless",
                    "      - " + quoted + ": peter-evans/repository-dispatch@v3"
                )
                self.assertTrue(classify(source))
                base = self.commit(TEMPLATE)
                head = self.commit(source)
                self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_quoted_step_keys_with_safe_commands_do_not_raise_debt(self):
        source = TEMPLATE.replace("      - run: |", '      - "run": |')
        self.assertFalse(classify(source))
        source = TEMPLATE.replace(
            "      - run: |\n          echo harmless",
            "      - 'uses': actions/checkout@v4"
        )
        self.assertFalse(classify(source))

    def test_compact_valid_yaml_steps_still_detect_dispatch(self):
        compact_prefix = (
            "on:\n workflow_run:\n  types: [completed]\n"
            "jobs:\n build:\n  runs-on: ubuntu-latest\n  steps:\n"
        )
        steps = (
            "   - run: |\n       gh workflow run other.yml\n",
            "   - uses: peter-evans/repository-dispatch@v3\n"
            "     with:\n      event-type: ping\n",
            "   - name: dispatch\n     with:\n      event-type: ping\n"
            "     uses: peter-evans/repository-dispatch@v3\n",
        )
        for source_step in steps:
            with self.subTest(step=source_step):
                self.assertTrue(classify(compact_prefix + source_step))
                base = self.commit(compact_prefix + "   - run: echo harmless\n")
                head = self.commit(compact_prefix + source_step)
                self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [WORKFLOW])

    def test_compact_action_adjacent_change_is_inventory_only(self):
        source = (
            "on: [workflow_run]\njobs:\n build:\n  steps:\n"
            "   - uses: peter-evans/repository-dispatch@v3\n"
        )
        base = self.commit(source)
        head = self.commit(source + "# documentation only\n")
        self.assertEqual(evaluate(self.repo, base, head)["new_findings"], [])

    def test_indented_workflow_run_triggers_are_not_missed(self):
        for depth in (1, 2, 4, 6):
            source = "name: test\non:\n" + " " * depth + "workflow_run:\n" + " " * (depth + 2) + "types: [completed]\njobs:\n"
            with self.subTest(depth=depth):
                self.assertTrue(workflow_run_event(source))
                self.assertTrue(classify(source + TEMPLATE.split("jobs:\n", 1)[1].replace("echo harmless", "gh workflow run target.yml").join(["jobs:\n", ""])))

    def test_nested_workflow_run_is_not_an_event(self):
        source = "on:\n    push:\n      workflow_run:\n        types: [completed]\njobs:\n"
        self.assertFalse(workflow_run_event(source))

    def test_variable_indentation_duplicate_and_alias_deny(self):
        for source in (
            "on:\n    push:\n    push:\njobs:\n",
            "on:\n     <<: *old\n     push: {}\njobs:\n",
            "on:\n     workflow_run: &old\njobs:\n",
            "on:\njobs:\n",
        ):
            with self.subTest(source=source), self.assertRaises(EvidenceError):
                workflow_run_event(source)

    def test_valid_spaces_before_yaml_on_colon(self):
        variants = (
            "on :\n  workflow_run:\n    types: [completed]\njobs:\n",
            "'on' : [workflow_run]\njobs:\n",
            '"on" : {workflow_run: {types: [completed]}}\njobs:\n',
        )
        for source in variants:
            with self.subTest(source=source):
                self.assertTrue(workflow_run_event(source))
                proof = source + "  proof:\n    runs-on: ubuntu-latest\n    steps:\n      - run: gh workflow run other.yml\n"
                self.assertTrue(classify(proof))
                baseline = self.commit(TEMPLATE)
                candidate = self.commit(proof)
                self.assertEqual(evaluate(self.repo, baseline, candidate)["new_findings"], [WORKFLOW])

    def test_duplicate_on_headers_with_colon_spacing_fail_closed(self):
        for source in (
            "on : [push]\non: [workflow_run]\njobs:\n",
            '"on" : [push]\n\'on\': [workflow_run]\njobs:\n',
        ):
            with self.subTest(source=source), self.assertRaisesRegex(
                EvidenceError, "ambiguous_event_mapping"
            ):
                workflow_run_event(source)

    def test_on_variants(self):
        self.assertTrue(workflow_run_event("on: [push, workflow_run]\njobs:\n"))
        self.assertTrue(workflow_run_event('"on": {workflow_run: {types: [completed]}}\njobs:\n'))
        self.assertFalse(workflow_run_event("on: [push]\njobs:\n"))


if __name__ == "__main__":
    unittest.main()
