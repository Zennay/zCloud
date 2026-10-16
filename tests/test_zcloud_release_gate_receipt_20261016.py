"""Adversarial offline zCloud deploy release receipt tests (no GitHub/VPS writes)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertFalse(result["offline_claims_complete"])
        self.assertFalse(result["production_mutation_authorized"])
        self.assertFalse(result["live_sources_verified"])
        self.assertIn(code, result["reason_codes"])

    def test_complete_claim_still_never_authorizes_production(self):
        result = self.verdict(receipt())
        self.assertTrue(result["offline_claims_complete"])
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
                      "2026-10-16T01:19:30+01:00", "20261016T001930Z",
                      "2026-10-16 00:19:30Z", "2026-10-16T00:19Z",
                      "2026-W42-5T00:19:30Z", "n/a", True, None):
            with self.subTest(stamp=stamp):
                self.deny(["observed_at"], stamp, "stale_or_invalid_observation")
        self.assertTrue(self.verdict(receipt())["offline_claims_complete"])
        data = receipt()
        data["observed_at"] = "2026-10-16T00:10:00Z"
        self.assertTrue(self.verdict(data)["offline_claims_complete"])

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
            call = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path),
                                   "--exit-zero-on-offline-claims",
                                   "--expected-main-sha", SHA],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(0, call.returncode, call.stderr)
            result = json.loads(call.stdout)
            self.assertTrue(result["offline_claims_complete"])
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


    def test_cli_invalid_json_always_reports_unverified_live_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            for bad in (b"{", b'{"a":NaN}', b"[]" * 20000):
                with self.subTest(length=len(bad)):
                    path.write_bytes(bad)
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(path)],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(2, call.returncode, call.stderr)
                    result = json.loads(call.stdout)
                    self.assertIs(result["live_sources_verified"], False)
                    self.assertIs(result["offline_claims_complete"], False)
                    self.assertIs(result["production_mutation_authorized"], False)

    def test_unicode_timestamp_digits_are_denied(self):
        data = receipt()
        data["observed_at"] = "٢٠٢٦-10-16T00:19:30Z"
        self.assertIn("stale_or_invalid_observation",
                      self.verdict(data)["reason_codes"])

    def test_markdown_uses_plain_dutch_and_never_grants_release(self):
        report = gate.render_markdown(self.verdict(receipt()))
        self.assertIn("GEEN PRODUCTIEVRIJGAVE", report)
        self.assertIn("Aangeleverd als compleet, niet geverifieerd", report)
        self.assertIn("production_mutation_authorized=false", report)
        self.assertIn("Bescherming hoofdbranch", report)
        self.assertIn("Werkende VPS-runner", report)
        self.assertEqual(8, report.count("Aangeleverd als compleet, niet geverifieerd"))

    def test_markdown_shows_distinct_unresolved_gate_causes(self):
        data = receipt()
        data["protection"]["enforced"] = False
        data["runner"]["runner_id"] = 0
        data["locks"]["all_serialized"] = False
        data["release"]["owner_approved"] = False
        report = gate.render_markdown(self.verdict(data))
        self.assertEqual(4, report.count("Claim ontbreekt of is ongeldig"))
        self.assertIn("#922", report)
        self.assertIn("#1326", report)
        self.assertIn("#1447", report)
        self.assertIn("#580", report)
        self.assertNotIn(SHA, report)

    def test_markdown_invalid_schema_does_not_claim_any_gate_complete(self):
        report = gate.render_markdown(self.verdict({"secret": "injected"}))
        self.assertEqual(8, report.count("Niet beoordeelbaar"))
        self.assertNotIn("injected", report)

    def test_markdown_renderer_fails_closed_for_unknown_reason_and_flags(self):
        for data in (
            {"reason_codes": ["not_known"], "live_sources_verified": False,
             "production_mutation_authorized": False},
            {"reason_codes": [], "live_sources_verified": True,
             "production_mutation_authorized": False},
            {"reason_codes": [], "live_sources_verified": False,
             "production_mutation_authorized": True},
        ):
            with self.subTest(data=data):
                report = gate.render_markdown(data)
                self.assertEqual(8, report.count("Niet beoordeelbaar"))
                self.assertNotIn("Aangeleverd als compleet", report)

    def test_cli_markdown_failure_is_redacted_and_stays_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.json"
            data = receipt()
            data["review"]["approved"] = False
            data["runner"]["labels"] = ["my-token-secret"]
            raw = json.dumps(data).encode()
            path.write_bytes(raw)
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(path),
                 "--format", "markdown"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(2, call.returncode, call.stderr)
            self.assertIn("Onafhankelijke review", call.stdout)
            self.assertIn("Werkende VPS-runner", call.stdout)
            self.assertIn("GEEN PRODUCTIEVRIJGAVE", call.stdout)
            self.assertNotIn("my-token-secret", call.stdout + call.stderr)
            self.assertEqual(raw, path.read_bytes())

    def test_cli_markdown_positive_claim_still_denies_production(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            path.write_text(json.dumps(data), encoding="utf-8")
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(path),
                 "--format", "markdown", "--exit-zero-on-offline-claims",
                 "--expected-main-sha", SHA], capture_output=True,
                text=True, timeout=5,
            )
            self.assertEqual(0, call.returncode, call.stderr)
            self.assertIn("GEEN PRODUCTIEVRIJGAVE", call.stdout)
            self.assertEqual(8, call.stdout.count(
                "Aangeleverd als compleet, niet geverifieerd"
            ))

    def test_cli_symlinked_parent_directory_is_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()
            path = real / "proof.json"
            path.write_text(json.dumps(receipt()), encoding="utf-8")
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input",
                 str(alias / "proof.json")],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(2, call.returncode, call.stderr)
            result = json.loads(call.stdout)
            self.assertEqual(["unsafe_or_missing_receipt"], result["reason_codes"])
            self.assertFalse(result["production_mutation_authorized"])
            self.assertFalse(result["live_sources_verified"])



    def test_json_exponential_overflow_denied_not_silently_infinite(self):
        for raw in (b'{"num":1e309}', b'{"num":-1e999999}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "nonfinite_json"):
                    gate.decode_receipt(raw)

    def test_json_overdeep_structure_returns_stable_redacted_error(self):
        raw = b'[' * 1200 + b'0' + b']' * 1200
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(raw)

    def test_json_integer_digit_overflow_returns_stable_error(self):
        raw = b'{"number":' + b'9' * 10000 + b'}'
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(raw)

    def test_no_follow_reader_allows_regular_nested_absolute_and_relative_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "nested" / "inner"
            nested.mkdir(parents=True)
            proof = nested / "proof.json"
            expected = json.dumps(receipt()).encode()
            proof.write_bytes(expected)
            self.assertEqual(expected, gate.read_receipt(proof))
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                self.assertEqual(expected, gate.read_receipt(
                    Path("nested/inner/proof.json")
                ))
            finally:
                os.chdir(old_cwd)

    def test_no_follow_reader_denies_dotdot_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "good.json").write_text(json.dumps(receipt()))
            for filename in (root / ".." / root.name / "good.json",
                             root / "missing.json", root):
                with self.subTest(filename=str(filename)):
                    with self.assertRaisesRegex(ValueError,
                                                "unsafe_or_missing_receipt"):
                        gate.read_receipt(filename)

    def test_no_follow_reader_denies_regular_file_with_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            (target / "proof.json").write_text(json.dumps(receipt()))
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unsafe_or_missing_receipt"):
                gate.read_receipt(alias / "proof.json")

    def test_no_follow_reader_denies_nonregular_fifo_without_hanging(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO required")
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / "wait-for-reader.fifo"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "unsafe_or_missing_receipt"):
                gate.read_receipt(fifo)

    def test_directory_swap_race_cannot_redirect_evidence_to_attacker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trusted = root / "trusted"
            trusted.mkdir()
            moved = root / "moved"
            attacker = root / "attacker"
            attacker.mkdir()
            authentic = b'{"trusted":true}'
            forged = b'{"attacker":true}'
            (trusted / "receipt.json").write_bytes(authentic)
            (attacker / "receipt.json").write_bytes(forged)
            original_open = os.open
            swapped = [False]

            def racing_open(name, flags, *, dir_fd=None):
                if name == "receipt.json" and dir_fd is not None and not swapped[0]:
                    swapped[0] = True
                    trusted.rename(moved)
                    trusted.symlink_to(attacker, target_is_directory=True)
                return original_open(name, flags, dir_fd=dir_fd)

            with patch.object(gate.os, "open", side_effect=racing_open):
                value = gate.read_receipt(trusted / "receipt.json")
            self.assertTrue(swapped[0])
            self.assertEqual(authentic, value)
            self.assertNotEqual(forged, value)

    def test_cli_json_overflow_has_no_traceback_and_no_release(self):
        with tempfile.TemporaryDirectory() as temp:
            proof = Path(temp) / "overflow.json"
            for raw in (b'{"num":1e4000}',
                        b'[' * 1300 + b'0' + b']' * 1300):
                with self.subTest(length=len(raw)):
                    proof.write_bytes(raw)
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(proof)],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(2, call.returncode, call.stderr)
                    self.assertNotIn("Traceback", call.stdout + call.stderr)
                    evidence = json.loads(call.stdout)
                    self.assertFalse(evidence["live_sources_verified"])
                    self.assertFalse(evidence["production_mutation_authorized"])
                    self.assertTrue(evidence["reason_codes"])



    def test_json_nesting_explicit_boundary_is_platform_independent(self):
        for depth in (1, 16, gate.MAX_JSON_DEPTH):
            with self.subTest(depth=depth):
                gate._check_json_nesting(b"[" * depth + b"0" + b"]" * depth)
        for depth in (gate.MAX_JSON_DEPTH + 1, 1200):
            with self.subTest(depth=depth):
                with self.assertRaisesRegex(ValueError, "invalid_json"):
                    gate._check_json_nesting(b"[" * depth + b"0" + b"]" * depth)

    def test_json_nesting_ignores_quoted_and_escaped_structural_brackets(self):
        raw = json.dumps({
            "payload": '{"[ ] escaped \\" braces {" : [ repeated "' * 100,
        }).encode("utf-8")
        gate._check_json_nesting(raw)
        self.assertEqual(1, len(gate.decode_receipt(raw)))

    def test_json_nesting_rejects_unbalanced_close_without_traceback(self):
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(b'{"foo":0}}')



    def test_json_integer_boundary_consistent_across_python_interpreters(self):
        inside = b'{"integer":' + b'9' * gate.MAX_INTEGER_DIGITS + b'}'
        outside = b'{"integer":' + b'9' * (gate.MAX_INTEGER_DIGITS + 1) + b'}'
        self.assertIn("integer", gate.decode_receipt(inside))
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(outside)
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(b'{"integer":-' +
                                b'9' * (gate.MAX_INTEGER_DIGITS + 1) + b'}')



    def test_json_escaped_surrogate_values_rejected(self):
        for raw in (b'{"field":"\ud800"}', b'{"field":"\udfff"}',
                    b'{"field":["ok", {"nested":"\ud800"}]}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "invalid_json"):
                    gate.decode_receipt(raw)

    def test_json_escaped_surrogate_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_json"):
            gate.decode_receipt(b'{"good":{"bad\ud800key":"value"}}')

    def test_json_invisible_control_and_direction_override_rejected(self):
        for escape in (rb"\u0000", rb"\u000a", rb"\u007f",
                       rb"\u202e", rb"\u202a", rb"\u200d",
                       rb"\ufeff", rb"\u2066"):
            raw = b'{"x":"' + escape + b'"}'
            with self.subTest(escape=escape):
                with self.assertRaisesRegex(ValueError, "invalid_json"):
                    gate.decode_receipt(raw)

    def test_json_valid_nonascii_text_and_surrogate_pairs_are_allowed(self):
        raw = b'{"text":"caf\xc3\xa9 \ud83d\ude80"}'
        self.assertEqual("café 🚀", gate.decode_receipt(raw)["text"])
        self.assertEqual("é", gate.decode_receipt(
            json.dumps({"text": "é"}).encode("utf-8")
        )["text"])

    def test_cli_invisible_unicode_rejected_without_reflecting_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "malformed.json"
            data = json.dumps(receipt()).replace(
                '"zcloud-release-evidence-v1"',
                '"zcloud-release-evidence-v1\\u202e"',
                1,
            )
            path.write_bytes(data.encode("utf-8"))
            for output_format in ("json", "markdown"):
                with self.subTest(output_format=output_format):
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(path),
                         "--format", output_format],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(2, call.returncode, call.stderr)
                    self.assertNotIn(r"\u202e", call.stdout + call.stderr)
                    self.assertIn("GEEN PRODUCTIEVRIJGAVE" if output_format ==
                                  "markdown" else "invalid_json", call.stdout)
                    self.assertNotIn("Traceback", call.stdout + call.stderr)



    def test_markdown_rejects_forged_complete_status_or_provenance(self):
        valid = self.verdict(receipt())
        forged = (
            {"offline_claims_complete": False},
            {"contract": "untrusted-contract"},
            {"evidence_source": "live_verified"},
            {"live_sources_verified": True},
            {"production_mutation_authorized": True},
            {"unexpected": "invisible"},  # extra, unsupported field
        )
        for mutation in forged:
            with self.subTest(mutation=mutation):
                result = deepcopy(valid)
                result.update(mutation)
                report = gate.render_markdown(result)
                self.assertEqual(8, report.count("Niet beoordeelbaar"))
                self.assertNotIn("Aangeleverd als compleet", report)
                self.assertIn("GEEN PRODUCTIEVRIJGAVE", report)

    def test_markdown_rejects_missing_fields_duplicate_codes_and_inconsistent_denials(self):
        base = self.verdict(receipt())
        samples = []
        for key in ("evidence_source", "contract", "offline_claims_complete",
                    "live_sources_verified"):
            data = deepcopy(base)
            data.pop(key)
            samples.append(data)
        duplicated = deepcopy(base)
        duplicated["reason_codes"] = ["invalid_json", "invalid_json"]
        duplicated["offline_claims_complete"] = False
        samples.append(duplicated)
        dishonest = deepcopy(base)
        dishonest["reason_codes"] = ["main_protection_missing"]
        samples.append(dishonest)
        for result in samples:
            with self.subTest(result=result):
                report = gate.render_markdown(result)
                self.assertEqual(8, report.count("Niet beoordeelbaar"))
                self.assertNotIn("Aangeleverd als compleet", report)

    def test_markdown_preserves_honest_missing_gate_and_complete_claim_status(self):
        full_report = gate.render_markdown(self.verdict(receipt()))
        self.assertEqual(8, full_report.count(
            "Aangeleverd als compleet, niet geverifieerd"
        ))
        data = receipt()
        data["runner"]["runner_id"] = 0
        result = self.verdict(data)
        self.assertFalse(result["offline_claims_complete"])
        report = gate.render_markdown(result)
        self.assertEqual(1, report.count("Claim ontbreekt of is ongeldig"))
        self.assertEqual(7, report.count(
            "Aangeleverd als compleet, niet geverifieerd"
        ))
        self.assertIn("GEEN PRODUCTIEVRIJGAVE", report)



    def test_dutch_audit_maps_every_offline_input_error_to_safe_next_step(self):
        for code, (message, action) in gate.INPUT_PROBLEMS.items():
            with self.subTest(code=code):
                data = self.verdict(receipt())
                data["reason_codes"] = [code]
                data["offline_claims_complete"] = False
                report = gate.render_markdown(data)
                self.assertIn("Invoerprobleem", report)
                self.assertIn(message, report)
                self.assertIn(action, report)
                self.assertIn("GEEN PRODUCTIEVRIJGAVE", report)
                self.assertEqual(8, report.count("Niet beoordeelbaar"))
                self.assertNotIn(SHA, report)

    def test_dutch_audit_forged_inconsistent_status_explains_invalid_schema(self):
        data = self.verdict(receipt())
        data["production_mutation_authorized"] = True
        report = gate.render_markdown(data)
        self.assertIn(gate.INPUT_PROBLEMS["invalid_schema"][0], report)
        self.assertNotIn("Aangeleverd als compleet", report)
        self.assertEqual(8, report.count("Niet beoordeelbaar"))

    def test_cli_dutch_invalid_json_explains_safe_local_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            proof = Path(temp) / "receipt.json"
            proof.write_bytes(b'{"secret":"do-not-echo",')
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(proof),
                 "--format", "markdown"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(2, call.returncode, call.stderr)
            self.assertIn(gate.INPUT_PROBLEMS["invalid_json"][0], call.stdout)
            self.assertIn(gate.INPUT_PROBLEMS["invalid_json"][1], call.stdout)
            self.assertNotIn("do-not-echo", call.stdout + call.stderr)
            self.assertNotIn("Traceback", call.stdout + call.stderr)
            self.assertIn("GEEN PRODUCTIEVRIJGAVE", call.stdout)

    def test_cli_dutch_symlink_error_explains_local_fix_without_echo(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "original.json"
            target.write_text(json.dumps(receipt()), encoding="utf-8")
            alias = Path(temp) / "hidden-credential-name.json"
            alias.symlink_to(target)
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(alias),
                 "--format", "markdown"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(2, call.returncode, call.stderr)
            self.assertIn(gate.INPUT_PROBLEMS["unsafe_or_missing_receipt"][0],
                          call.stdout)
            self.assertNotIn("hidden-credential-name", call.stdout + call.stderr)
            self.assertNotIn(str(alias), call.stdout + call.stderr)



    def test_positive_forged_receipt_default_cli_exits_nonzero_both_formats(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "forged.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            path.write_text(json.dumps(data), encoding="utf-8")
            original = path.read_bytes()
            for option in ("json", "markdown"):
                with self.subTest(option=option):
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(path),
                         "--format", option],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(2, call.returncode, call.stderr)
                    if option == "json":
                        verdict = json.loads(call.stdout)
                        self.assertTrue(verdict["offline_claims_complete"])
                        self.assertIs(verdict["production_mutation_authorized"], False)
                        self.assertIs(verdict["live_sources_verified"], False)
                    else:
                        self.assertIn("GEEN PRODUCTIEVRIJGAVE", call.stdout)
                        self.assertIn("niet geverifieerd", call.stdout)
                    self.assertEqual(original, path.read_bytes())

    def test_opt_in_cannot_override_missing_gate_or_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "denied.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            data["runner"]["runner_id"] = 0
            for payload in (json.dumps(data).encode(), b'{"secret":NaN}'):
                with self.subTest(payload=payload[:40]):
                    path.write_bytes(payload)
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(path),
                         "--exit-zero-on-offline-claims"],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(2, call.returncode, call.stderr)
                    result = json.loads(call.stdout)
                    self.assertIs(result["production_mutation_authorized"], False)
                    self.assertIs(result["live_sources_verified"], False)

    def test_default_status_blocks_shell_gating_even_for_claimed_green(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "forged.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            path.write_text(json.dumps(data), encoding="utf-8")
            call = subprocess.run(
                ["bash", "-e", "-c",
                 '"$1" "$2" --input "$3" >/dev/null; echo SHOULD_NOT_DEPLOY',
                 "--", sys.executable, str(SCRIPT), str(path)],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(2, call.returncode, call.stderr)
            self.assertNotIn("SHOULD_NOT_DEPLOY", call.stdout + call.stderr)



    def test_expected_main_sha_binds_all_positive_offline_claims(self):
        for mismatch in (OTHER_SHA, "A" * 40, "a" * 39, "", "bad", "0" * 40):
            with self.subTest(mismatch=mismatch):
                result = gate.evaluate(receipt(), now=NOW,
                                       expected_main_sha=mismatch)
                self.assertFalse(result["offline_claims_complete"])
                self.assertIn("candidate_not_exact_current_main",
                              result["reason_codes"])
                self.assertIs(result["production_mutation_authorized"], False)
                self.assertIs(result["live_sources_verified"], False)
        result = gate.evaluate(receipt(), now=NOW, expected_main_sha=SHA)
        self.assertTrue(result["offline_claims_complete"])
        self.assertFalse(result["production_mutation_authorized"])

    def test_opt_in_requires_explicit_exact_main_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claimed.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            path.write_text(json.dumps(data), encoding="utf-8")
            cases = ([], ["--expected-main-sha", OTHER_SHA],
                     ["--expected-main-sha", "A" * 40],
                     ["--expected-main-sha", ""],
                     ["--expected-main-sha", "0" * 40])
            for extra in cases:
                with self.subTest(extra=extra):
                    call = subprocess.run(
                        [sys.executable, str(SCRIPT), "--input", str(path),
                         "--exit-zero-on-offline-claims", *extra],
                        capture_output=True, text=True, timeout=5)
                    self.assertEqual(2, call.returncode, call.stderr)
                    result = json.loads(call.stdout)
                    self.assertFalse(result["offline_claims_complete"])
                    self.assertIn("candidate_not_exact_current_main",
                                  result["reason_codes"])
                    self.assertFalse(result["production_mutation_authorized"])
                    self.assertFalse(result["live_sources_verified"])
            call = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(path),
                 "--expected-main-sha", SHA, "--exit-zero-on-offline-claims"],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(0, call.returncode, call.stderr)
            result = json.loads(call.stdout)
            self.assertTrue(result["offline_claims_complete"])
            self.assertFalse(result["production_mutation_authorized"])
            self.assertFalse(result["live_sources_verified"])

    def test_expected_main_mismatch_refused_in_default_cli_both_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claimed.json"
            data = receipt()
            data["observed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            path.write_text(json.dumps(data), encoding="utf-8")
            for fmt in ("json", "markdown"):
                with self.subTest(fmt=fmt):
                    cmd = [sys.executable, str(SCRIPT), "--input", str(path),
                           "--expected-main-sha", OTHER_SHA, "--format", fmt]
                    call = subprocess.run(cmd, capture_output=True,
                                          text=True, timeout=5)
                    self.assertEqual(2, call.returncode, call.stderr)
                    if fmt == "json":
                        self.assertIn("candidate_not_exact_current_main",
                                      json.loads(call.stdout)["reason_codes"])
                    else:
                        self.assertIn("Claim ontbreekt of is ongeldig",
                                      call.stdout)
                        self.assertIn("GEEN PRODUCTIEVRIJGAVE", call.stdout)
                    self.assertNotIn(SHA, call.stdout + call.stderr)
                    self.assertNotIn(OTHER_SHA, call.stdout + call.stderr)

    def test_expected_main_sha_must_match_canonical_not_only_candidate(self):
        data = receipt()
        data["candidate_sha"] = OTHER_SHA
        result = gate.evaluate(data, now=NOW, expected_main_sha=OTHER_SHA)
        self.assertFalse(result["offline_claims_complete"])
        self.assertIn("candidate_not_exact_current_main",
                      result["reason_codes"])
        self.assertIn("ci_not_exact_head_success", result["reason_codes"])
        self.assertIn("independent_review_missing", result["reason_codes"])
        self.assertIs(result["production_mutation_authorized"], False)



if __name__ == "__main__":
    unittest.main()
