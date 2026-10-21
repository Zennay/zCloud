from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_preflight_evidence_coherence as audit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_preflight_evidence_coherence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "zcloud-preflight-evidence-coherence.yml"
SHA = "1" * 40
HEAD_SHA = "3" * 40


def snapshot(now: dt.datetime) -> dict:
    captured = now.isoformat()
    rows = []
    verdicts = {
        "dependency": "ready",
        "owner_coverage": "ready",
        "writer_window": "clear",
        "open_pr_ownership": "clear",
        "unpr_branch_ownership": "clear",
    }
    for kind, verdict in verdicts.items():
        rows.append(
            {
                "kind": kind,
                "policy": audit.REQUIRED_POLICIES[kind],
                "capability_id": "control-plane:test-capability",
                "candidate_head_sha": HEAD_SHA,
                "observed_at": captured,
                "main_sha": SHA,
                "verdict": verdict,
                "complete": True,
            }
        )
    return {
        "schema_version": 2,
        "captured_at": captured,
        "main_sha": SHA,
        "candidate_base_sha": SHA,
        "candidate_head_sha": HEAD_SHA,
        "capability_id": "control-plane:test-capability",
        "evidence": rows,
    }


class PreflightEvidenceCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 7, 9, 20, tzinfo=dt.timezone.utc)

    def test_coherent_clear_transaction_admits(self):
        result = audit.audit_snapshot(snapshot(self.now), now=self.now)
        self.assertTrue(result["ok"])
        self.assertEqual("control-plane-preflight-evidence-coherence-v2", result["policy"])
        self.assertEqual("admit", result["status"])
        self.assertEqual(HEAD_SHA, result["candidate_head_sha"])
        self.assertEqual([], result["blocked_kinds"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(5, len(result["evidence"]))
        self.assertTrue(
            all(
                row["capability_id"] == "control-plane:test-capability"
                and row["candidate_head_sha"] == HEAD_SHA
                for row in result["evidence"]
            )
        )

    def test_legacy_schema_fails_closed(self):
        payload = snapshot(self.now)
        payload["schema_version"] = 1
        with self.assertRaisesRegex(audit.EvidenceError, "schema_version_unsupported"):
            audit.audit_snapshot(payload, now=self.now)

    def test_mixed_main_evidence_fails_closed(self):
        payload = snapshot(self.now)
        payload["evidence"][2]["main_sha"] = "2" * 40
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_main_mismatch"):
            audit.audit_snapshot(payload, now=self.now)

    def test_candidate_base_drift_fails_closed(self):
        payload = snapshot(self.now)
        payload["candidate_base_sha"] = "2" * 40
        with self.assertRaisesRegex(audit.EvidenceError, "candidate_base_mismatch"):
            audit.audit_snapshot(payload, now=self.now)

    def test_candidate_head_binding_fails_closed(self):
        payload = snapshot(self.now)
        payload["evidence"][1]["candidate_head_sha"] = "4" * 40
        with self.assertRaisesRegex(
            audit.EvidenceError, "evidence_candidate_head_mismatch"
        ):
            audit.audit_snapshot(payload, now=self.now)

        with self.assertRaisesRegex(audit.EvidenceError, "candidate_head_mismatch"):
            audit.audit_snapshot(
                snapshot(self.now),
                now=self.now,
                expected_candidate_head_sha="5" * 40,
            )

    def test_duplicate_or_missing_evidence_is_rejected(self):
        payload = snapshot(self.now)
        payload["evidence"][-1] = dict(payload["evidence"][0])
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_kind_duplicate"):
            audit.audit_snapshot(payload, now=self.now)

        payload = snapshot(self.now)
        payload["evidence"].pop()
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_count_invalid"):
            audit.audit_snapshot(payload, now=self.now)

    def test_wrong_classifier_identity_fails_closed(self):
        payload = snapshot(self.now)
        payload["evidence"][0]["policy"] = "generic-green-label-v1"
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_policy_mismatch"):
            audit.audit_snapshot(payload, now=self.now)

    def test_mixed_capability_evidence_fails_closed(self):
        payload = snapshot(self.now)
        payload["evidence"][3]["capability_id"] = "control-plane:other-capability"
        with self.assertRaisesRegex(
            audit.EvidenceError, "evidence_capability_mismatch"
        ):
            audit.audit_snapshot(payload, now=self.now)

    def test_incomplete_evidence_is_never_approval(self):
        payload = snapshot(self.now)
        payload["evidence"][0]["complete"] = False
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_incomplete"):
            audit.audit_snapshot(payload, now=self.now)

    def test_stale_and_future_evidence_fail_closed(self):
        payload = snapshot(self.now)
        payload["evidence"][0]["observed_at"] = (
            self.now - dt.timedelta(seconds=61)
        ).isoformat()
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_stale"):
            audit.audit_snapshot(payload, now=self.now)

        payload = snapshot(self.now)
        payload["evidence"][0]["observed_at"] = (
            self.now + dt.timedelta(seconds=1)
        ).isoformat()
        with self.assertRaisesRegex(audit.EvidenceError, "evidence_after_capture"):
            audit.audit_snapshot(payload, now=self.now)

    def test_nonclear_source_blocks_without_becoming_incomplete(self):
        payload = snapshot(self.now)
        for row in payload["evidence"]:
            if row["kind"] == "writer_window":
                row["verdict"] = "blocked"
        result = audit.audit_snapshot(payload, now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(["writer_window"], result["blocked_kinds"])

    def test_snapshot_file_rejects_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(audit.EvidenceError, "snapshot_symlink_rejected"):
                audit.load_snapshot(link)

            large = root / "large.json"
            large.write_text("x" * (audit.MAX_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(audit.EvidenceError, "snapshot_too_large"):
                audit.load_snapshot(large)

    def test_cli_strict_mode_accepts_only_admit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            current = dt.datetime.now(dt.timezone.utc)
            payload = snapshot(current)
            path.write_text(json.dumps(payload), encoding="utf-8")
            missing_expected = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), "--require-admit"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, missing_expected.returncode, missing_expected.stderr)
            self.assertEqual(
                ["expected_candidate_head_required"],
                json.loads(missing_expected.stdout)["errors"],
            )

            ok = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(path),
                    "--expected-candidate-head-sha",
                    HEAD_SHA,
                    "--require-admit",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, ok.returncode, ok.stderr)
            out = json.loads(ok.stdout)
            self.assertEqual("admit", out["status"])
            self.assertFalse(out["mutation_performed"])

            payload["evidence"][0]["verdict"] = "blocked"
            path.write_text(json.dumps(payload), encoding="utf-8")
            blocked = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(path),
                    "--expected-candidate-head-sha",
                    HEAD_SHA,
                    "--require-admit",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, blocked.returncode, blocked.stderr)
            self.assertEqual("blocked", json.loads(blocked.stdout)["status"])

    def test_output_is_bounded_and_does_not_echo_unknown_payload(self):
        result = audit.audit_snapshot(snapshot(self.now), now=self.now)
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("conversation", encoded)
        self.assertNotIn("prompt", encoded)
        self.assertEqual(
            {
                "ok",
                "policy",
                "status",
                "main_sha",
                "candidate_head_sha",
                "capability_id",
                "required_kinds",
                "blocked_kinds",
                "evidence_span_seconds",
                "evidence",
                "mutation_performed",
            },
            set(result),
        )

    def test_workflow_uses_exact_permanent_vps_runner_read_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("timeout-minutes: 5", text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            text,
        )
        self.assertIn("persist-credentials: false", text)
        self.assertIn("github.actor == 'Zennay'", text)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            text,
        )
        self.assertIn('test "$(id -un)" = "ubuntu"', text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("--require-admit", text)
        self.assertIn("retention-days: 14", text)
        for forbidden in (
            "contents: write",
            "actions: write",
            "sudo ",
            "systemctl ",
            "git push",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
