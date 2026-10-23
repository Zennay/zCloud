"""Adversarial, offline real-file tests for SHA-bound writer owner-review claims."""
from __future__ import annotations

import contextlib
import csv
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_main_writer_review_admission_20261016_w12 as admission

MAIN = "a" * 40
HASH = "b" * 64
PATH = ".github/workflows/ftmo-example.yml"
REVIEW_URL = "https://github.com/Zennay/zCloud/issues/922#issuecomment-12345"


def inventory():
    return {
        "schema": "zcloud-main-writer-compat-v1",
        "main_sha": MAIN,
        "static_scan_complete": True,
        "workflows_scanned": 2,
        "candidate_count": 1,
        "direct_writer_candidate_count": 1,
        "no_static_signal_count": 1,
        "candidates": [{
            "path": PATH, "source_sha256": HASH,
            "classification": "direct_writer_candidate",
            "signals": [{"line": 7, "kind": "direct_main_ref"}],
            "disposition": "manual_review_required",
        }],
        "historical_activity_verified": False,
        "github_branch_protection_verified": False,
        "pr_branch_ownership_verified": False,
        "safe_to_enable_branch_protection": False,
        "production_mutation_authorized": False,
    }


def review():
    return {
        "schema": "zcloud-main-writer-review-v1",
        "main_sha": MAIN,
        "decisions": [{
            "path": PATH, "source_sha256": HASH, "owner": "runner-owner",
            "reviewed_by": "independent-reviewer", "decision": "migrate",
            "evidence_url": REVIEW_URL,
        }],
    }


class OfflineWriterReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.inventory_file = self.directory / "inventory.json"
        self.review_file = self.directory / "reviews.json"
        self.store(inventory(), review())

    def store(self, i, r):
        self.inventory_file.write_text(json.dumps(i), encoding="utf-8")
        self.review_file.write_text(json.dumps(r), encoding="utf-8")

    def csv_queue(self, overrides=None, rows=None):
        fields = admission.CSV_HEADERS
        row = {
            "main_sha": MAIN, "workflow_path": PATH,
            "source_sha256": HASH, "signal_lines_and_kinds": "7:direct_main_ref",
            "priority": "P1", "owner": "", "reviewed_by": "",
            "decision": "", "evidence_url": "",
            "review_verified": "false", "branch_protection_authorized": "false",
        }
        row.update(overrides or {})
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(fields)
        for record in rows if rows is not None else [row]:
            writer.writerow([record.get(field, "") for field in fields])
        path = self.directory / "editable-review-queue.csv"
        path.write_text(stream.getvalue(), encoding="utf-8")
        return path

    def capture_csv(self, path):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = admission.main(
                ["--inventory", str(self.inventory_file),
                 "--reviews-csv", str(path), "--expected-main-sha", MAIN])
        return status, json.loads(output.getvalue())

    def denied_csv(self, updates, code):
        status, receipt = self.capture_csv(self.csv_queue(overrides=updates))
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], code)
        self.assertFalse(receipt["safe_to_enable_branch_protection"])
        self.assertFalse(receipt["production_mutation_authorized"])

    def capture(self, fmt="json"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = admission.main(
                ["--inventory", str(self.inventory_file),
                 "--reviews", str(self.review_file),
                 "--expected-main-sha", MAIN, "--format", fmt])
        return status, out.getvalue()

    def rejected(self, i=None, r=None, code=None):
        self.store(inventory() if i is None else i, review() if r is None else r)
        status, stdout = self.capture()
        parsed = json.loads(stdout)
        self.assertEqual(status, 2)
        self.assertIs(parsed["safe_to_enable_branch_protection"], False)
        self.assertIs(parsed["production_mutation_authorized"], False)
        self.assertIs(parsed["review_claims_independently_verified"], False)
        if code is not None:
            self.assertEqual(parsed["error"], code)

    def test_editable_csv_empty_review_queue_keeps_all_candidates_pending(self):
        status, receipt = self.capture_csv(self.csv_queue())
        self.assertEqual(status, 0)
        self.assertEqual(receipt["candidate_count"], 1)
        self.assertEqual(receipt["review_claim_count"], 0)
        self.assertEqual(receipt["pending_owner_review_count"], 1)
        self.assertFalse(receipt["safe_to_enable_branch_protection"])

    def test_filled_csv_review_syntactically_valid_but_not_authority(self):
        status, receipt = self.capture_csv(self.csv_queue(overrides={
            "owner": "workflow-owner", "reviewed_by": "independent-reviewer",
            "decision": "retire", "evidence_url": REVIEW_URL,
        }))
        self.assertEqual(status, 0)
        self.assertEqual(receipt["review_claim_count"], 1)
        self.assertTrue(receipt["offline_review_claims_complete"])
        self.assertIs(receipt["review_claims_independently_verified"], False)
        self.assertIs(receipt["safe_to_enable_branch_protection"], False)
        self.assertIs(receipt["production_mutation_authorized"], False)

    def test_incomplete_csv_review_denied(self):
        self.denied_csv({"owner": "workflow-owner"}, "incomplete_csv_review")

    def test_changed_csv_main_denied(self):
        self.denied_csv({"main_sha": "c" * 40}, "tampered_csv_provenance")

    def test_changed_csv_blob_hash_denied(self):
        self.denied_csv({"source_sha256": "c" * 64}, "tampered_csv_provenance")

    def test_changed_csv_signals_denied(self):
        self.denied_csv({"signal_lines_and_kinds": "99:direct_main_ref"},
                        "tampered_csv_provenance")

    def test_changed_csv_priority_denied(self):
        self.denied_csv({"priority": "P2"}, "tampered_csv_provenance")

    def test_forged_csv_review_verified_denied(self):
        self.denied_csv({"review_verified": "true"}, "forged_csv_authorization")

    def test_forged_csv_protection_authorized_denied(self):
        self.denied_csv({"branch_protection_authorized": "true"},
                        "forged_csv_authorization")

    def test_duplicate_csv_row_denied(self):
        p = self.csv_queue()
        contents = p.read_text()
        header, row = contents.split("\n", 1)
        p.write_text(header + "\n" + row + row, encoding="utf-8")
        status, receipt = self.capture_csv(p)
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], "duplicate_or_unknown_csv_candidate")

    def test_missing_csv_row_denied(self):
        p = self.csv_queue(rows=[])
        status, receipt = self.capture_csv(p)
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], "missing_csv_candidates")

    def test_unknown_csv_candidate_denied(self):
        self.denied_csv({"workflow_path": ".github/workflows/unknown.yml"},
                        "duplicate_or_unknown_csv_candidate")

    def test_csv_formula_injection_denied(self):
        self.denied_csv({"owner": "=HYPERLINK(A1)", "reviewed_by": "reviewer",
                         "decision": "migrate", "evidence_url": REVIEW_URL},
                        "invalid_review_identity")

    def test_csv_duplicate_header_denied(self):
        p = self.csv_queue()
        raw = p.read_text()
        p.write_text(raw.replace("source_sha256", "workflow_path", 1),
                     encoding="utf-8")
        status, receipt = self.capture_csv(p)
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], "invalid_csv_header_or_size")

    def test_csv_hidden_bidi_control_denied(self):
        p = self.csv_queue()
        p.write_text(p.read_text().replace("false", "fal\u202ese", 1),
                     encoding="utf-8")
        status, receipt = self.capture_csv(p)
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], "unsafe_csv_controls")

    def test_csv_symlink_parent_denied(self):
        real = self.directory / "evidence"
        real.mkdir()
        (real / "queue.csv").write_text(self.csv_queue().read_text(), encoding="utf-8")
        link = self.directory / "shortcut"
        link.symlink_to(real, target_is_directory=True)
        status, receipt = self.capture_csv(link / "queue.csv")
        self.assertEqual(status, 2)
        self.assertEqual(receipt["error"], "unreadable_file")

    def test_csv_unknown_decision_denied(self):
        self.denied_csv({"owner": "workflow-owner", "reviewed_by": "reviewer",
                         "decision": "approved", "evidence_url": REVIEW_URL},
                        "invalid_review_decision")

    def test_csv_forged_external_evidence_denied(self):
        self.denied_csv({"owner": "workflow-owner", "reviewed_by": "reviewer",
                         "decision": "retire", "evidence_url": "https://evil.example"},
                        "invalid_review_evidence")

    def test_one_complete_review_is_still_never_authority(self):
        status, stdout = self.capture()
        result = json.loads(stdout)
        self.assertEqual(status, 0)
        self.assertEqual(result["review_claim_count"], 1)
        self.assertEqual(result["pending_owner_review_count"], 0)
        self.assertTrue(result["offline_review_claims_complete"])
        self.assertIs(result["review_claims_independently_verified"], False)
        self.assertIs(result["safe_to_enable_branch_protection"], False)
        self.assertIs(result["production_mutation_authorized"], False)

    def test_no_reviews_is_allowed_as_progress_only(self):
        r = review()
        r["decisions"] = []
        self.store(inventory(), r)
        status, stdout = self.capture()
        parsed = json.loads(stdout)
        self.assertEqual(status, 0)
        self.assertEqual(parsed["unreviewed_paths"], [PATH])
        self.assertEqual(parsed["pending_owner_review_count"], 1)
        self.assertFalse(parsed["offline_review_claims_complete"])

    def test_readable_dutch_review_summary(self):
        status, stdout = self.capture("markdown")
        self.assertEqual(status, 0)
        self.assertIn("GEEN VRIJGAVE", stdout)
        self.assertIn("Ingediende claims: 1", stdout)
        self.assertNotIn("runner-owner", stdout)
        self.assertNotIn(REVIEW_URL, stdout)

    def test_unknown_review_path_denied(self):
        r = review()
        r["decisions"][0]["path"] = ".github/workflows/unrelated.yml"
        self.rejected(r=r, code="stale_duplicate_or_unknown_review")

    def test_sha_hash_drift_denied(self):
        r = review()
        r["decisions"][0]["source_sha256"] = "c" * 64
        self.rejected(r=r, code="stale_duplicate_or_unknown_review")

    def test_main_sha_drift_denied(self):
        r = review()
        r["main_sha"] = "c" * 40
        self.rejected(r=r, code="stale_or_invalid_reviews")

    def test_duplicate_review_path_denied(self):
        r = review()
        r["decisions"].append(copy.deepcopy(r["decisions"][0]))
        self.rejected(r=r, code="stale_duplicate_or_unknown_review")

    def test_duplicate_inventory_path_denied(self):
        i = inventory()
        i["candidates"].append(copy.deepcopy(i["candidates"][0]))
        i["candidate_count"] = 2
        i["workflows_scanned"] = 3
        i["direct_writer_candidate_count"] = 2
        self.rejected(i=i, code="invalid_inventory_candidates")

    def test_unknown_review_field_denied(self):
        r = review()
        r["decisions"][0]["authorized"] = True
        self.rejected(r=r, code="invalid_schema")

    def test_unknown_inventory_field_denied(self):
        i = inventory()
        i["live_sources_verified"] = True
        self.rejected(i=i, code="invalid_schema")

    def test_decision_must_be_known(self):
        r = review()
        r["decisions"][0]["decision"] = "already_safe"
        self.rejected(r=r, code="invalid_review_decision")

    def test_review_identity_not_authenticated_but_must_be_distinct(self):
        r = review()
        r["decisions"][0]["reviewed_by"] = "RUNNER-OWNER"
        self.rejected(r=r, code="invalid_review_identity")

    def test_unsafe_identity_denied(self):
        r = review()
        r["decisions"][0]["owner"] = "../../secret"
        self.rejected(r=r, code="invalid_review_identity")

    def test_untrusted_arbitrary_evidence_url_denied(self):
        r = review()
        r["decisions"][0]["evidence_url"] = "https://evil.example/approved"
        self.rejected(r=r, code="invalid_review_evidence")

    def test_owner_evidence_must_be_specific(self):
        r = review()
        r["decisions"][0]["evidence_url"] = "https://github.com/Zennay/zCloud"
        self.rejected(r=r, code="invalid_review_evidence")

    def test_false_positive_still_not_authority(self):
        r = review()
        r["decisions"][0]["decision"] = "false_positive"
        self.store(inventory(), r)
        status, stdout = self.capture()
        result = json.loads(stdout)
        self.assertEqual(status, 0)
        self.assertEqual(result["review_claims_by_disposition"]["false_positive"], 1)
        self.assertFalse(result["safe_to_enable_branch_protection"])

    def test_inventory_forged_live_claim_denied(self):
        i = inventory()
        i["production_mutation_authorized"] = True
        self.rejected(i=i, code="untrusted_inventory_claim")

    def test_inventory_bool_count_denied(self):
        i = inventory()
        i["workflows_scanned"] = True
        self.rejected(i=i, code="invalid_inventory_counts")

    def test_inventory_count_mismatch_denied(self):
        i = inventory()
        i["no_static_signal_count"] = 0
        self.rejected(i=i, code="invalid_inventory_counts")

    def test_contradictory_classification_denied(self):
        i = inventory()
        i["candidates"][0]["classification"] = "capability_or_indirect_writer_candidate"
        self.rejected(i=i, code="contradictory_inventory_classification")

    def test_duplicated_signal_denied(self):
        i = inventory()
        i["candidates"][0]["signals"].append({"line": 7, "kind": "direct_main_ref"})
        self.rejected(i=i, code="invalid_inventory_signal")

    def test_unrecognized_signal_denied(self):
        i = inventory()
        i["candidates"][0]["signals"][0]["kind"] = "trust_me"
        self.rejected(i=i, code="invalid_inventory_signal")

    def test_duplicate_json_keys_denied(self):
        self.review_file.write_text('{"schema":"x","schema":"zcloud-main-writer-review-v1"}')
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "duplicate_json_key")

    def test_nonfinite_json_denied(self):
        self.review_file.write_text('{"pi":NaN}')
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsupported_number")

    def test_float_json_denied(self):
        self.review_file.write_text('{"pi":1e309}')
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsupported_number")

    def test_escaped_surrogate_denied(self):
        self.review_file.write_text('{"value":"\\ud800"}')
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsafe_unicode")

    def test_hidden_bidi_mark_denied(self):
        self.review_file.write_text('{"value":"\\u202e"}')
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsafe_unicode")

    def test_deep_json_denied(self):
        self.review_file.write_text("[" * 30 + "0" + "]" * 30)
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "nested_json")

    def test_symlink_leaf_denied(self):
        replacement = self.directory / "real.json"
        replacement.write_text(self.review_file.read_text())
        self.review_file.unlink()
        self.review_file.symlink_to(replacement)
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unreadable_file")

    def test_symlinked_parent_directory_denied(self):
        real = self.directory / "actual"
        real.mkdir()
        (real / "review.json").write_text(self.review_file.read_text(), encoding="utf-8")
        (self.directory / "linked-parent").symlink_to(real, target_is_directory=True)
        status, stdout = self.capture_special(self.directory / "linked-parent" / "review.json")
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unreadable_file")

    def test_parent_traversal_denied(self):
        status, stdout = self.capture_special(self.directory / ".." /
                                              self.directory.name / "reviews.json")
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsafe_path")

    def test_oversized_input_denied(self):
        self.review_file.write_bytes(b" " * (admission.MAX_REVIEWS_BYTES + 1))
        status, stdout = self.capture()
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsafe_or_oversized_file")

    def test_fifo_denied_without_hanging(self):
        fifo = self.directory / "pipe"
        os.mkfifo(fifo)
        status, stdout = self.capture_special(fifo)
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout)["error"], "unsafe_or_oversized_file")

    def capture_special(self, reviews):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = admission.main([
                "--inventory", str(self.inventory_file), "--reviews", str(reviews),
                "--expected-main-sha", MAIN])
        return status, out.getvalue()


if __name__ == "__main__":
    unittest.main()
