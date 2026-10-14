"""Offline adversarial checks for manual self-hosted dispatch trust ratchet."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import zcloud_manual_vps_dispatch_ratchet_20261014 as guard

SRC = Path(__file__).resolve().parents[1] / "scripts/zcloud_manual_vps_dispatch_ratchet_20261014.py"
WF = ".github/workflows/sample.yml"
SAFE_IF = (
    "github.repository == 'Zennay/zCloud' && "
    "github.ref == 'refs/heads/main' && "
    "github.actor == 'Zennay' && "
    "github.triggering_actor == 'Zennay' && "
    "github.run_attempt == 1"
)

def workflow(*, if_statement: str = "", event: str = "workflow_dispatch:",
             runner: str = "[self-hosted, zcloud, vps]", job: str = "prove") -> bytes:
    condition = f"    if: {if_statement}\n" if if_statement else ""
    return (f"name: proof\non:\n  {event}\npermissions:\n  contents: read\n"
            f"jobs:\n  {job}:\n{condition}    runs-on: {runner}\n"
            f"    steps:\n      - run: echo evidence-only\n").encode()

def commit(repo: Path, text: bytes | None) -> str:
    wf = repo / WF
    if text is None:
        wf.unlink(missing_ok=True)
    else:
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_bytes(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Proof",
                    "-c", "user.email=proof@example.invalid",
                    "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

class ManualDispatchPolicy(unittest.TestCase):
    def assertDenied(self, body: bytes, expected: int = 1) -> None:
        out = guard.evaluate({}, {WF: body})
        self.assertFalse(out["ok"])
        self.assertEqual(expected, out["new_debt_count"])
        self.assertFalse(out["mutation_performed"])
        self.assertFalse(out["production_mutation_authorized"])

    def test_new_untrusted_manual_vps_job_is_denied(self):
        self.assertDenied(workflow())

    def test_valid_identity_guard_is_permitted(self):
        self.assertTrue(guard.evaluate({}, {WF: workflow(if_statement=SAFE_IF)})["ok"])

    def test_missing_each_admission_dimension_denied(self):
        pieces = SAFE_IF.split(" && ")
        for part in pieces:
            with self.subTest(removed=part):
                self.assertDenied(workflow(if_statement=" && ".join(p for p in pieces if p != part)))

    def test_separate_step_condition_does_not_count_as_job_if(self):
        body = workflow().decode().replace("      - run:", f"      - if: {SAFE_IF}\n        run:")
        self.assertDenied(body.encode())

    def test_hosted_job_does_not_incur_vps_debt(self):
        out = guard.evaluate({}, {WF: workflow(runner="ubuntu-latest")})
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["candidate_debt_count"])

    def test_no_dispatch_event_is_not_in_scope(self):
        self.assertTrue(guard.evaluate({}, {WF: workflow(event="push:")})["ok"])

    def test_flow_dispatch_event_detected(self):
        body = workflow().decode().replace("on:\n  workflow_dispatch:", "on: [workflow_dispatch]")
        self.assertDenied(body.encode())

    def test_dynamic_runner_fails_closed(self):
        text = "$" + "{{ fromJSON(vars.RUNNER) }}"
        self.assertDenied(workflow(runner=text))

    def test_reusable_job_without_job_level_trust_fails_closed(self):
        body = ("on:\n  workflow_dispatch:\njobs:\n  reuse:\n"
                "    uses: Zennay/zCloud/.github/workflows/proof.yml@main\n").encode()
        self.assertDenied(body)

    def test_legacy_unsafe_debt_grandfathered_and_new_job_denied(self):
        old = workflow()
        self.assertTrue(guard.evaluate({WF: old}, {WF: old})["ok"])
        new = workflow(job="another")
        self.assertDenied(new)

    def test_fixing_legacy_debt_is_permitted(self):
        old = workflow()
        new = workflow(if_statement=SAFE_IF)
        out = guard.evaluate({WF: old}, {WF: new})
        self.assertTrue(out["ok"])
        self.assertEqual(0, out["candidate_debt_count"])

    def test_multiple_new_risky_jobs_counted(self):
        body = workflow().decode().replace("    steps:", "  other:\n    runs-on: [self-hosted, zcloud, vps]\n    steps:", 1)
        self.assertDenied(body.encode(), expected=2)

    def test_unsupported_event_alias_denied(self):
        body = workflow().replace(b"  workflow_dispatch:", b"  workflow_dispatch: &hidden")
        with self.assertRaises(guard.RefError):
            guard.risky_manual_jobs(WF, body)

    def test_readonly_immutable_ref_git_cli_red_green(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            base = commit(root, workflow(if_statement=SAFE_IF))
            head = commit(root, workflow())
            proc = subprocess.run([sys.executable, str(SRC), "--repo-root", str(root),
                                   "--base-sha", base, "--head-sha", head],
                                  text=True, capture_output=True, timeout=20, check=False)
            self.assertEqual(1, proc.returncode, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(1, result["new_debt_count"])
            self.assertFalse(result["production_mutation_authorized"])
            self.assertEqual({WF: workflow(if_statement=SAFE_IF)}, guard.manifest(root, base))
            denied = subprocess.run([sys.executable, str(SRC), "--repo-root", str(root),
                                     "--base-sha", "main", "--head-sha", head],
                                    text=True, capture_output=True, timeout=20, check=False)
            self.assertEqual("ref_must_be_immutable_sha", json.loads(denied.stdout)["reason"])

    def test_symlinked_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            wf = root / WF
            wf.parent.mkdir(parents=True)
            wf.symlink_to("/etc/passwd")
            commit_sha = commit_symlink(root)
            with self.assertRaises(guard.RefError):
                guard.manifest(root, commit_sha)

def commit_symlink(repo: Path) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Proof",
                    "-c", "user.email=proof@example.invalid",
                    "commit", "-qm", "symlink"], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

if __name__ == "__main__":
    unittest.main()
