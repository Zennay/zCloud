import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "zcloud_iteration_hypothesis_audit.py"
spec = importlib.util.spec_from_file_location("hypothesis_audit", MODULE_PATH)
audit_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit_module)


class IterationHypothesisAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "history.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE project_state_receipts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    evidence_json TEXT NOT NULL
                )"""
            )

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, project, source, evidence):
        raw = evidence if isinstance(evidence, str) else json.dumps(evidence)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO project_state_receipts(project_id,source,evidence_json) VALUES(?,?,?)",
                (project, source, raw),
            )

    @staticmethod
    def hypothesis(**overrides):
        payload = {
            "schema_version": 1,
            "problem": "control-plane risk",
            "change": "add bounded audit",
            "expected_effect": "risk becomes measurable",
            "validation": "exact-head tests and live read-only proof",
        }
        payload.update(overrides)
        return {"iteration_hypothesis": payload}

    def test_complete_contract_is_counted_without_exposing_payload_values(self):
        secret = "SENSITIVE-HYPOTHESIS-CONTENT"
        evidence = self.hypothesis(problem=secret)
        self.insert("cloud", "portfolio_queue:pwq-test", evidence)
        before = self.db.stat()

        result = audit_module.audit(self.db)

        after = self.db.stat()
        self.assertEqual("complete", result["coverage"]["status"])
        self.assertEqual(1, result["coverage"]["complete"])
        self.assertTrue(result["database_immutable"])
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))

    def test_missing_and_incomplete_contracts_report_only_field_counts(self):
        self.insert("cloud", "portfolio_queue:missing", {"result": "DONE"})
        self.insert(
            "cloud",
            "portfolio_queue:incomplete",
            self.hypothesis(validation=""),
        )

        result = audit_module.audit(self.db)

        self.assertEqual("incomplete", result["coverage"]["status"])
        self.assertEqual(0, result["coverage"]["complete"])
        self.assertEqual(1, result["coverage"]["counts"]["missing_contract"])
        self.assertEqual(1, result["coverage"]["counts"]["incomplete_contract"])
        self.assertEqual(2, result["coverage"]["missing_fields"]["validation"])
        self.assertEqual(1, result["coverage"]["missing_fields"]["problem"])

    def test_malformed_and_wrong_version_fail_coverage_without_leaking_raw_json(self):
        self.insert("cloud", "portfolio_queue:bad-json", "{definitely-not-json")
        self.insert(
            "cloud",
            "portfolio_queue:wrong-version",
            self.hypothesis(schema_version=99),
        )

        result = audit_module.audit(self.db)

        self.assertEqual(1, result["coverage"]["counts"]["malformed_evidence"])
        self.assertEqual(1, result["coverage"]["counts"]["invalid_contract"])
        self.assertNotIn("definitely-not-json", json.dumps(result))

    def test_only_selected_project_queue_receipts_are_in_scope(self):
        self.insert("ftmo", "portfolio_queue:other-project", {"result": "DONE"})
        self.insert("cloud", "manual:note", {"result": "DONE"})
        self.insert("cloud", "portfolio_queue:cloud", self.hypothesis())

        result = audit_module.audit(self.db, project_id="cloud")

        self.assertEqual(1, result["scope"]["receipts_observed"])
        self.assertEqual("complete", result["coverage"]["status"])

    def test_empty_scope_is_explicit_no_receipts(self):
        result = audit_module.audit(self.db)
        self.assertEqual("no_receipts", result["coverage"]["status"])
        self.assertEqual(0, result["scope"]["receipts_observed"])

    def test_symlink_database_is_rejected(self):
        link = Path(self.tmp.name) / "linked.db"
        os.symlink(self.db, link)
        with self.assertRaisesRegex(
            audit_module.HypothesisAuditError, "symlink"
        ):
            audit_module.audit(link)

    def test_missing_required_schema_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        with sqlite3.connect(broken) as conn:
            conn.execute("CREATE TABLE project_state_receipts(id INTEGER)")
        with self.assertRaisesRegex(
            audit_module.HypothesisAuditError, "missing required columns"
        ):
            audit_module.audit(broken)

    def test_limit_is_bounded(self):
        for bad in (0, 501, True):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    audit_module.HypothesisAuditError, "limit"
                ):
                    audit_module.audit(self.db, limit=bad)


if __name__ == "__main__":
    unittest.main()
