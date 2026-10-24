"""Deny-first event admission for the VM metadata-only hosted source job.

This suite checks the *actual workflow expression* offline against a
synthetic event matrix. No GitHub credentials, Firefox, VPS, or network.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/zcloud-v2-violentmonkey-actor-probe.yml"

FIELD = re.compile(r"(?<![A-Za-z0-9_'])github(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
SAFE_EXPR = re.compile(r"[A-Za-z0-9_.'\s()&|=!/-]+")


def hosted_job(source: str) -> str:
    return source.split("  hosted-proof:\n", 1)[1].split("  inspect:\n", 1)[0]


def hosted_condition(source: str) -> str:
    job = hosted_job(source)
    return job.split("    if: >-\n", 1)[1].split("    runs-on:", 1)[0].strip()


def evaluate(expr: str, *, event: str, repo: str = "Zennay/zCloud",
             actor: str = "Zennay", triggering: str = "Zennay",
             head_repo: str = "Zennay/zCloud", base_ref: str = "main",
             ref: str = "refs/heads/main") -> bool:
    """Evaluate only boolean ==/and/or/constant nodes from the source policy."""
    if not SAFE_EXPR.fullmatch(expr):
        raise ValueError("unsafe_test_expression")
    values = {
        "github.event_name": event,
        "github.repository": repo,
        "github.actor": actor,
        "github.triggering_actor": triggering,
        "github.event.pull_request.head.repo.full_name": head_repo,
        "github.event.pull_request.base.ref": base_ref,
        "github.ref": ref,
    }
    normalized = FIELD.sub(lambda m: repr(values[m.group()]), expr)
    python_expr = normalized.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(" ".join(python_expr.split()), mode="eval")

    def eval_safe(node):
        if isinstance(node, ast.Expression):
            return eval_safe(node.body)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            parts = [bool(eval_safe(x)) for x in node.values]
            return all(parts) if isinstance(node.op, ast.And) else any(parts)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return eval_safe(node.left) == eval_safe(node.comparators[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        raise ValueError("unsupported_test_expression")
    return bool(eval_safe(tree))


class VmMetadataHostedAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.job = hosted_job(cls.source)
        cls.condition = hosted_condition(cls.source)

    def test_owner_same_repo_pull_request_allowed(self):
        self.assertTrue(evaluate(self.condition, event="pull_request"))

    def test_forked_source_not_executed(self):
        self.assertFalse(evaluate(
            self.condition, event="pull_request", head_repo="attacker/repo"
        ))

    def test_wrong_repo_not_executed(self):
        self.assertFalse(evaluate(
            self.condition, event="pull_request", repo="attacker/repo",
            head_repo="attacker/repo",
        ))

    def test_foreign_pr_actor_denied(self):
        self.assertFalse(evaluate(self.condition, event="pull_request", actor="attacker"))

    def test_foreign_rerun_actor_denied(self):
        self.assertFalse(evaluate(
            self.condition, event="pull_request", triggering="attacker"
        ))

    def test_pr_to_unintended_base_denied(self):
        self.assertFalse(evaluate(
            self.condition, event="pull_request", base_ref="release-test"
        ))

    def test_approved_main_push_remains_hosted(self):
        self.assertTrue(evaluate(self.condition, event="push"))

    def test_main_push_by_existing_automation_preserved(self):
        self.assertTrue(evaluate(
            self.condition, event="push", actor="github-actions[bot]",
            triggering="github-actions[bot]"
        ))

    def test_nonmain_push_denied(self):
        self.assertFalse(evaluate(
            self.condition, event="push", ref="refs/heads/feature-untrusted"
        ))

    def test_foreign_repo_push_denied(self):
        self.assertFalse(evaluate(
            self.condition, event="push", repo="attacker/repo"
        ))

    def test_workflow_dispatch_not_hosted(self):
        self.assertFalse(evaluate(
            self.condition, event="workflow_dispatch"
        ))

    def test_selfhosted_inspect_remains_manual_owner_sha(self):
        inspect = self.source.split("  inspect:\n", 1)[1]
        prefix = inspect.split("    runs-on:", 1)[0]
        for token in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.confirm_sha == github.sha",
        ):
            with self.subTest(token=token):
                self.assertIn(token, prefix)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", inspect)
        self.assertIn("cancel-in-progress: false", self.source)

    def test_workflow_source_is_hosted_only_for_pr_and_push(self):
        self.assertEqual(self.job.count("runs-on: ubuntu-latest"), 1)
        self.assertNotIn("self-hosted", self.job)
        self.assertIn("persist-credentials: false", self.job)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", self.job)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", self.job)
        self.assertNotIn("systemctl", self.job)

    def test_new_test_file_keeps_changed_workflow_gate_active(self):
        self.assertIn(
            "      - tests/test_zcloud_vm_probe_hosted_admission_20261015_w10.py",
            self.source.split("permissions:", 1)[0],
        )
        self.assertIn(
            "tests.test_zcloud_vm_probe_hosted_admission_20261015_w10",
            self.job,
        )
        self.assertIn("  pull_request:", self.source)
        self.assertIn("  push:", self.source)
        self.assertNotIn("pull_request_target:", self.source)


if __name__ == "__main__":
    unittest.main()
