import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.zcloud_main_protection_audit import (
    ProtectionEvidenceError,
    audit_protection,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_main_protection_audit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-main-protection-audit.yml"


def canonical_protection():
    return {
        "required_status_checks": {
            "strict": True,
            "checks": [
                {"context": "zCloud regression smoke", "app_id": 15368},
            ],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "bypass_pull_request_allowances": {
                "users": [],
                "teams": [],
                "apps": [],
            },
        },
        "required_conversation_resolution": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }


class ZcloudMainProtectionAuditTests(unittest.TestCase):
    def test_unprotected_branch_is_visible_and_never_ok(self):
        result = audit_protection(branch_protected=False, protection=None)
        self.assertFalse(result["ok"])
        self.assertEqual("unprotected", result["status"])
        self.assertFalse(result["checks"]["branch_protected"])
        self.assertEqual(0, result["required_check_count"])
        self.assertFalse(result["mutation_performed"])

    def test_canonical_protection_is_protected(self):
        result = audit_protection(
            branch_protected=True,
            protection=canonical_protection(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("protected", result["status"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(1, result["required_check_count"])
        self.assertFalse(result["mutation_performed"])

    def test_missing_preventive_controls_fail_closed(self):
        keys = [
            "enforce_admins",
            "required_pull_request_reviews",
            "required_conversation_resolution",
            "allow_force_pushes",
            "allow_deletions",
            "required_status_checks",
        ]
        for key in keys:
            with self.subTest(key=key):
                payload = canonical_protection()
                payload.pop(key)
                result = audit_protection(
                    branch_protected=True,
                    protection=payload,
                )
                self.assertFalse(result["ok"])
                self.assertEqual("needs_hardening", result["status"])

    def test_bypass_actor_is_not_canonical(self):
        payload = canonical_protection()
        payload["required_pull_request_reviews"][
            "bypass_pull_request_allowances"
        ]["apps"] = [{"slug": "bypass-bot"}]
        result = audit_protection(
            branch_protected=True,
            protection=payload,
        )
        self.assertFalse(result["checks"]["bypass_allowances_empty"])
        self.assertEqual("needs_hardening", result["status"])

    def test_status_checks_must_be_strict_and_nonempty(self):
        payload = canonical_protection()
        payload["required_status_checks"] = {
            "strict": False,
            "checks": [],
        }
        result = audit_protection(
            branch_protected=True,
            protection=payload,
        )
        self.assertFalse(result["checks"]["strict_status_checks"])
        self.assertFalse(result["checks"]["required_check_count_positive"])
        self.assertEqual("needs_hardening", result["status"])

    def test_legacy_status_contexts_count_as_required_checks(self):
        payload = canonical_protection()
        payload["required_status_checks"] = {
            "strict": True,
            "checks": [],
            "contexts": ["zCloud regression smoke"],
        }
        result = audit_protection(
            branch_protected=True,
            protection=payload,
        )
        self.assertTrue(result["checks"]["required_check_count_positive"])
        self.assertEqual(1, result["required_check_count"])


    def test_unprotected_branch_rejects_conflicting_payload(self):
        with self.assertRaisesRegex(
            ProtectionEvidenceError,
            "unprotected_with_protection_payload",
        ):
            audit_protection(
                branch_protected=False,
                protection=canonical_protection(),
            )

    def test_exact_boolean_type_is_required(self):
        with self.assertRaisesRegex(
            ProtectionEvidenceError,
            "branch_protected_invalid",
        ):
            audit_protection(
                branch_protected=1,
                protection=canonical_protection(),
            )

    def test_output_never_contains_raw_actor_metadata(self):
        payload = canonical_protection()
        payload["restrictions"] = {
            "users": [{"login": "sensitive-user"}],
            "teams": [{"slug": "sensitive-team"}],
            "apps": [{"slug": "sensitive-app"}],
        }
        result = audit_protection(
            branch_protected=True,
            protection=payload,
        )
        encoded = json.dumps(result)
        self.assertNotIn("sensitive-user", encoded)
        self.assertNotIn("sensitive-team", encoded)
        self.assertNotIn("sensitive-app", encoded)

    def test_cli_require_protected_has_distinct_exit_for_unprotected(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--branch-protected",
                "false",
                "--require-protected",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("unprotected", payload["status"])
        self.assertFalse(payload["mutation_performed"])

    def test_cli_rejects_symlink_protection_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "protection.json"
            real.write_text(json.dumps(canonical_protection()), encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(real)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--branch-protected",
                    "true",
                    "--protection-json",
                    str(link),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("incomplete", payload["status"])
        self.assertEqual(
            ["protection_symlink_rejected"],
            payload["errors"],
        )

    def test_workflow_is_exact_read_only_and_guarded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn(
            "gh api repos/Zennay/zCloud/branches/main --jq '.protected // false'",
            text,
        )
        self.assertIn(
            'gh api repos/Zennay/zCloud/branches/main/protection',
            text,
        )
        for forbidden in (
            "contents: write",
            "administration: write",
            "--method PUT",
            "--method PATCH",
            "--method POST",
            "gh api --method",
            "git push",
            "sudo ",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
