"""Adversarial offline zCloud deploy release receipt tests (no GitHub/VPS writes)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import zcloud_release_gate_receipt_20261016 as gate


NOW = datetime(2026, 10, 16, 0, 20, tzinfo=timezone.utc)
SHA = "a" * 40
OTHER_SHA = "b" * 40
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_release_gate_receipt_20261016.py"


def receipt():
    return {
        "schema": "zcloud-release-evidence-v1",
        "candidate_sha": SHA,
        "canonical_main_sha": SHA,
        "observed_at": "2026-10-16T00:19:30Z",
        "ci": {"head_sha": SHA, "conclusion": "success",
               "run_url": "https://github.com/Zennay/zCloud/actions/runs/345"},
        "review": {"head_sha": SHA, "approved": True, "independent": True},
        "protection": {"main_sha": SHA, "enforced": True, "reviews_required": True},
        "runner": {"main_sha": SHA, "runner_id": 12,
                   "labels": ["self-hosted", "zcloud", "vps"],
                   "job_url": "https://github.com/Zennay/zCloud/actions/runs/345/job/678"},
        "locks": {"main_sha": SHA, "production_writer_jobs": 7,
                  "all_serialized": True},
        "release": {"main_sha": SHA, "owner_approved": True,
                    "exclusive_window": True},
    }


class ReleaseReceiptTests(unittest.TestCase):
    def verdict(self, data):
        return gate.evaluate(data, now=NOW)

    def deny(self, key, value, code):
        data = receipt()
        node = data
        for fragment in key[:-1]:
            node = node[fragment]
        node[key[-1]] = value
        result = self.verdict(data)
        self.assertFalse(result["operational_gates_observed"])
        self.assertFalse(result["production_mutation_authorized"])
        self.assertIn(code, result["reason_codes"])

    def test_complete_claim_still_never_authorizes_production(self):
        result = self.verdict(receipt())
        self.assertTrue(result["operational_gates_observed"])
        self.assertFalse(result["production_mutation_authorized"])
        self.assertEqual("untrusted_offline_receipt", result["evidence_source"])
        self.assertEqual([], result["reason_codes"])

    def test_missing_or_unexpected_schema_fields_denied(self):
        for mutation in (
            lambda d: d.pop("ci"),
            lambda d: d.update({"token": "must_not_echo"}),
            lambda d: d["runner"].pop("labels"),
            lambda d: d["ci"].update({"unsupported": 1}),
            lambda d: d.update({"schema": "v2"}),
        ):
            with self.subTest(mutation=mutation):
                data = receipt()
                mutation(data)
                result = self.verdict(data)
                self.assertEqual(["invalid_schema"], result["reason_codes"])
                self.assertNotIn("must_not_echo", json.dumps(result))

    def test_candidate_must_be_valid_immutable_main_sha(self):
        for value in (OTHER_SHA, "", "A" * 40, "a" * 39, 42, None, True):
            with self.subTest(value=value):
                self.deny(["candidate_sha"], value, "candidate_not_exact_current_main")
        self.deny(["canonical_main_sha"], OTHER_SHA, "candidate_not_exact_current_main")

    def test_ci_must_match_exact_head_and_real_repo_run_url(self):
        self.deny(["ci", "head_sha"], OTHER_SHA, "ci_not_exact_head_success")
        for conclusion in ("skipped", "failure", "neutral", "pending", None, True):
            self.deny(["ci", "conclusion"], conclusion, "ci_not_exact_head_success")
        for url in ("http://github.com/Zennay/zCloud/actions/runs/345",
                    "https://github.com/Other/zCloud/actions/runs/345",
                    "https://github.com/Zennay/zCloud/actions/runs/345?token=fake",
                    "https://evil.tld/github.com/Zennay/zCloud/actions/runs/345",
                    "https://github.com/Zennay/zCloud/actions/runs/0"):
            with self.subTest(url=url):
                self.deny(["ci", "run_url"], url, "ci_not_exact_head_success")

    def test_review_requires_independent_approval_on_same_sha(self):
        self.deny(["review", "head_sha"], OTHER_SHA, "independent_review_missing")
        for key in ("approved", "independent"):
            for value in (False, 1, "true", None):
                with self.subTest(key=key, value=value):
                    self.deny(["review", key], value, "independent_review_missing")

    def test_branch_protection_enforced_with_required_reviews(self):
        self.deny(["protection", "main_sha"], OTHER_SHA, "main_protection_missing")
        for key in ("enforced", "reviews_required"):
            for value in (False, "true", 1):
                self.deny(["protection", key], value, "main_protection_missing")

    def test_runner_must_be_exact_main_assigned_with_vps_labels(self):
        self.deny(["runner", "main_sha"], OTHER_SHA, "vps_runner_assignment_unproven")
        for value in (0, -1, None, True, "12", 3.1):
            self.deny(["runner", "runner_id"], value, "vps_runner_assignment_unproven")
        for labels in ([], ["self-hosted"], ["ubuntu-latest", "zcloud", "vps"],
                       "self-hosted,zcloud,vps", [[], "zcloud", "vps"],
                       ["self-hosted", "zcloud", "VPS"]):
            self.deny(["runner", "labels"], labels, "vps_runner_assignment_unproven")
        for url in ("https://github.com/Zennay/zCloud/actions/runs/345",
                    "https://github.com/Zennay/zCloud/actions/runs/345/job/0",
                    "https://example.com/Zennay/zCloud/actions/runs/345/job/678"):
            self.deny(["runner", "job_url"], url, "vps_runner_assignment_unproven")

    def test_all_seven_prod_writers_must_have_verified_serialization(self):
        self.deny(["locks", "main_sha"], OTHER_SHA, "production_writer_lock_incomplete")
        for jobs in (0, 3, 6, 8, True, 7.0, "7"):
            self.deny(["locks", "production_writer_jobs"], jobs,
                      "production_writer_lock_incomplete")
        for value in (False, 1, "yes"):
            self.deny(["locks", "all_serialized"], value,
                      "production_writer_lock_incomplete")

    def test_release_requires_explicit_owner_and_exclusive_window(self):
        self.deny(["release", "main_sha"], OTHER_SHA,
                  "serialized_release_window_unapproved")
        for key in ("owner_approved", "exclusive_window"):
            for value in (False, 1, None):
                self.deny(["release", key], value,
                          "serialized_release_window_unapproved")

    def test_future_old_and_non_utc_timestamps_fail_closed(self):
        for stamp in ("2026-10-16T00:21:00Z", "2026-10-16T00:09:59Z",
                      "2026-10-16T00:20:00+00:00",
                      "2026-10-16T01:19:30+01:00", "n/a", True, None):
            with self.subTest(stamp=stamp):
                self.deny(["observed_at"], stamp, "stale_or_invalid_observation")
        self.assertTrue(self.verdict(receipt())["operational_gates_observed"])
        data = receipt()
        data["observed_at"] = "2026-10-16T00:10:00Z"
        self.assertTrue(self.verdict(data)["operational_gates_observed"])

    def test_now_must_be_timezone_aware(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            gate.evaluate(receipt(), now=datetime(2026, 10, 16))

    def test_duplicate_keys_nested_and_root_denied(self):
        for raw in (
            b'{"schema":"v1","schema":"v2"}',
            b'{"ci":{"run_url":"one","run_url":"two"}}',
        ):
            with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
                gate.decode_receipt(raw)

    def test_nonfinite_json_and_large_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonfinite_json"):
            gate.decode_receipt(b'{"x":NaN}')
        with self.assertRaisesRegex(ValueError, "nonfinite_json"):
            gate.decode_receipt(b'{"x":Infinity}')
        with self.assertRaisesRegex(ValueError, "receipt_too_large"):
            gate.decode_receipt(b"x" * (gate.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(b"\xff")
        with self.assertRaisesRegex(ValueError, "invalid_schema"):
            gate.decode_receipt(b"[]")

    def test_many_denials_still_bounded_and_redacted(self):
        data = receipt()
        data["ci"]["head_sha"] = OTHER_SHA
        data["release"]["owner_approved"] = False
        data["runner"]["runner_id"] = 0
        data["protection"]["enforced"] = False
        result = self.verdict(data)
        self.assertEqual(
            ["ci_not_exact_head_success", "main_protection_missing",
             "vps_runner_assignment_unproven", "serialized_release_window_unapproved"],
            result["reason_codes"],
        )
        self.assertNotIn("candidate_sha", result)

    def test_cli_deny_and_no_input_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            data = receipt()
            data["release"]["owner_approved"] = False
            original = json.dumps(data).encode()
            path.write_bytes(original)
            call = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(2, call.returncode, call.stderr)
            result = json.loads(call.stdout)
            self.assertFalse(result["production_mutation_authorized"])
            self.assertIn("serialized_release_window_unapproved", result["reason_codes"])
            self.assertEqual(original, path.read_bytes())

    def test_cli_success_claim_does_not_authorize_live_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            path.write_text(json.dumps(data), encoding="utf-8")
            call = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(0, call.returncode, call.stderr)
            result = json.loads(call.stdout)
            self.assertTrue(result["operational_gates_observed"])
            self.assertFalse(result["production_mutation_authorized"])

    def test_cli_duplicate_keys_fail_without_spilling_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"
            path.write_text('{"secret":"dont-print","secret":"dont-print"}')
            call = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(2, call.returncode)
            self.assertIn("duplicate_json_key", call.stdout)
            self.assertNotIn("dont-print", call.stdout + call.stderr)

    def test_cli_rejects_symlink_input(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path(temp) / "original.json"
            original.write_text(json.dumps(receipt()))
            linked = Path(temp) / "linked.json"
            linked.symlink_to(original)
            call = subprocess.run([sys.executable, str(SCRIPT), "--input", str(linked)],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(2, call.returncode)
            self.assertIn("unsafe_or_missing_receipt", call.stdout)


if __name__ == "__main__":
    unittest.main()
