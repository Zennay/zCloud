"""Offline-deny-first tests for a historical cross-repo RaiseAI queue mutation workflow."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/raiseai-gateway-deploy-queue-cleanup.yml"
ZCLOUD_MAIN = "a" * 40
RAISE_MAIN = "b" * 40
STALE_SHA = "c" * 40
FAKE_GH = r"""#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if len(args) >= 2 and args[0] == "api":
    target = args[1]
    if target == "--method":
        if args[2] != "POST" or not args[3].endswith("/cancel"):
            sys.exit(2)
        with Path(os.environ["GH_LOG"]).open("a") as log:
            log.write(args[3] + "\n")
        sys.exit(0)
    if target == "repos/Zennay/zCloud/commits/main":
        print(os.environ.get("REMOTE_ZCLOUD_MAIN", "a" * 40))
        sys.exit(0)
    if target == "repos/Zennay/RaiseAI/commits/main":
        print(os.environ.get("REMOTE_RAISE_MAIN", "b" * 40))
        sys.exit(0)
    if "actions/runs?per_page=100" in target and "--paginate" in args:
        if os.environ.get("FAIL_LIST") == "1":
            sys.exit(70)
        print(os.environ.get("STALE_ROWS", ""), end="")
        sys.exit(0)
    if "actions/runs?per_page=100" in target:
        print("{}")
        sys.exit(0)
    if target.startswith("repos/Zennay/RaiseAI/actions/runs/"):
        run_id = target.rsplit("/", 1)[-1]
        print("\t".join([run_id, os.environ.get("RUN_NAME", "Raise gateway deploy + live smoke"),
                         os.environ.get("RUN_SHA", "c" * 40),
                         os.environ.get("RUN_STATUS", "queued")]))
        sys.exit(0)
sys.exit(68)
"""


def workflow_script():
    source = WF.read_text(encoding="utf-8")
    anchor = "        run: |\n"
    if anchor not in source:
        raise AssertionError("no Bash run step")
    # The workflow has a single shell block, extending to end of file.
    return textwrap.dedent(source.split(anchor, 1)[1])


class RaiseAiGatewayCleanupGuardTests(unittest.TestCase):
    def run_offline(self, **changes):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "gh"
            exe.write_text(FAKE_GH, encoding="utf-8")
            exe.chmod(0o755)
            log = Path(tmp) / "posted.txt"
            log.write_text("")
            env = dict(os.environ)
            env.update({
                "PATH": tmp + os.pathsep + os.environ.get("PATH", ""),
                "GH_LOG": str(log),
                "RAISE_REPO": "Zennay/RaiseAI",
                "GITHUB_REPOSITORY": "Zennay/zCloud",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_ACTOR": "Zennay",
                "GITHUB_TRIGGERING_ACTOR": "Zennay",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SHA": ZCLOUD_MAIN,
                "ZCLOUD_EXPECTED_SHA": ZCLOUD_MAIN,
                "REMOTE_ZCLOUD_MAIN": ZCLOUD_MAIN,
                "REMOTE_RAISE_MAIN": RAISE_MAIN,
                "STALE_ROWS": "",
            })
            env.update(changes)
            proc = subprocess.run(
                ["bash", "-c", workflow_script()], env=env, capture_output=True,
                text=True, timeout=12, check=False,
            )
            return proc, log.read_text()

    def test_workflow_has_no_automatic_mutation_and_is_manual_trusted_main_only(self):
        wf = WF.read_text(encoding="utf-8")
        self.assertIn("  workflow_dispatch:", wf)
        self.assertNotIn("\n  push:", wf)
        self.assertNotIn("\n  workflow_run:", wf)
        self.assertNotIn("\n  pull_request:", wf)
        self.assertIn("default: false", wf)
        self.assertIn("inputs.perform_cancellations == true", wf)
        self.assertIn("github.actor == 'Zennay'", wf)
        self.assertIn("github.triggering_actor == 'Zennay'", wf)
        self.assertIn("github.run_attempt == 1", wf)
        self.assertIn("github.ref == 'refs/heads/main'", wf)
        self.assertIn("cancel-in-progress: false", wf)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", wf)
        self.assertIn("permissions:\n  contents: read", wf)
        self.assertNotIn("actions: write", wf)
        self.assertEqual(wf.count("gh api --method POST"), 1)

    def test_actual_run_block_is_valid_bash(self):
        proc = subprocess.run(
            ["bash", "-n"], input=workflow_script(), capture_output=True,
            text=True, check=False, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_zero_candidates_produces_zero_posts(self):
        proc, log = self.run_offline()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(log, "")

    def test_mutation_only_after_exact_manual_main_and_target_check(self):
        proc, log = self.run_offline(STALE_ROWS=f"123\t{STALE_SHA}\tqueued\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(log.strip(), "repos/Zennay/RaiseAI/actions/runs/123/cancel")

    def test_untrusted_actor_attempt_event_ref_sha_and_live_main_are_denied(self):
        failures = [
            {"GITHUB_REPOSITORY": "someone/zCloud"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_ACTOR": "other"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_SHA": "d" * 40},
            {"ZCLOUD_EXPECTED_SHA": "bad"},
            {"REMOTE_ZCLOUD_MAIN": "e" * 40},
            {"REMOTE_RAISE_MAIN": "f" * 40},
        ]
        for patch in failures:
            with self.subTest(patch=patch):
                proc, log = self.run_offline(
                    STALE_ROWS=f"123\t{STALE_SHA}\tqueued\n", **patch,
                )
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(log, "")

    def test_failed_page_fetch_aborts_without_cancellation(self):
        proc, log = self.run_offline(
            STALE_ROWS=f"123\t{STALE_SHA}\tqueued\n", FAIL_LIST="1",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(log, "")

    def test_bulk_cancel_and_malformed_ids_fail_before_any_post(self):
        for rows in (
            "".join(f"{n}\t{STALE_SHA}\tqueued\n" for n in range(1, 7)),
            f"123\t{STALE_SHA}\tqueued\n123\t{STALE_SHA}\tqueued\n",
            f"123\t{STALE_SHA}\tqueued\nnot-an-id\t{STALE_SHA}\tqueued\n",
            f"123\tbroken\tqueued\n",
            f"123\t{STALE_SHA}\tin_progress\n",
        ):
            with self.subTest(rows=rows):
                proc, log = self.run_offline(STALE_ROWS=rows)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(log, "")

    def test_stale_list_must_match_fresh_run_identity_and_state(self):
        for patch in (
            {"RUN_NAME": "Other workflow"},
            {"RUN_SHA": "f" * 40},
            {"RUN_STATUS": "in_progress"},
        ):
            with self.subTest(patch=patch):
                proc, log = self.run_offline(
                    STALE_ROWS=f"123\t{STALE_SHA}\tqueued\n", **patch,
                )
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(log, "")


if __name__ == "__main__":
    unittest.main()
