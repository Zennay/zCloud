"""Adversarial zSentinel QA contract: identity isolation, strict schema and conflicting receipts."""
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "zsentinel_diagnose_contract", ROOT / "scripts" / "zsentinel_diagnose.py"
)
sentinel = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sentinel)

NOW = datetime(2026, 10, 16, 22, 0, tzinfo=timezone.utc)
HEAD = "b" * 40


def timestamp(minutes: int = 0) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def snapshot(project: dict, **overrides: object) -> dict:
    return {"schema_version": 1, "observed_at": timestamp(-1),
            "projects": [project], **overrides}


def queued(**changes: object) -> dict:
    evidence = {"job_status": "queued", "observed_at": timestamp(-1),
                "queued_at": timestamp(-45), "runner_assigned": False,
                "head_sha": HEAD}
    evidence.update(changes)
    return {"id": "cloud", "github": evidence}


def failed(**changes: object) -> dict:
    evidence = {"job_status": "completed", "observed_at": timestamp(-1),
                "conclusion": "failure", "head_sha": HEAD}
    evidence.update(changes)
    return {"id": "cloud", "github": evidence}


def inspect(project: dict) -> dict:
    return sentinel.diagnose(snapshot(project), NOW)


class ZSentinelEvidenceContractTests(unittest.TestCase):
    def test_boolean_schema_version_not_alias_of_integer_one(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            sentinel.diagnose(snapshot(queued(), schema_version=True), NOW)

    def test_non_hashable_project_id_rejected_as_input_error(self):
        for ident in ([], {}, {"nested": True}, 17, None):
            with self.subTest(ident=repr(ident)):
                with self.assertRaisesRegex(ValueError, "project identifiers"):
                    sentinel.diagnose(snapshot({"id": ident}), NOW)

    def test_project_identifier_is_ascii_slug_only(self):
        for ident in ("clоud", "clöud", "Cloud", "-cloud", "cloud-", "cloud__x"):
            with self.subTest(ident=ident):
                with self.assertRaisesRegex(ValueError, "bounded slug"):
                    inspect({"id": ident})
        self.assertEqual("cloud-v2_qa", inspect({"id": "cloud-v2_qa"})["projects"][0]["project_id"])

    def test_same_job_receipt_yields_stable_fingerprint(self):
        one = inspect(queued(run_id=178, job_id=309))["incidents"][0]["incident_id"]
        two = inspect(queued(run_id=178, job_id=309,
                             observed_at=timestamp(-2)))["incidents"][0]["incident_id"]
        self.assertEqual(one, two)

    def test_distinct_run_or_job_do_not_suppress_each_other(self):
        same_head_different_run = [
            inspect(queued(run_id=100, job_id=300))["incidents"][0]["incident_id"],
            inspect(queued(run_id=101, job_id=300))["incidents"][0]["incident_id"],
            inspect(queued(run_id=100, job_id=301))["incidents"][0]["incident_id"],
        ]
        self.assertEqual(len(same_head_different_run), len(set(same_head_different_run)))

    def test_distinct_failed_workflows_same_sha_not_deduplicated(self):
        a = inspect(failed(run_id=200))["incidents"][0]["incident_id"]
        b = inspect(failed(run_id=201))["incidents"][0]["incident_id"]
        self.assertNotEqual(a, b)
        self.assertEqual("github_workflow_failed", inspect(failed(run_id=200))["incidents"][0]["reason"])

    def test_legacy_incident_fingerprint_unchanged_without_job_identity(self):
        actual = inspect(queued())["incidents"][0]["incident_id"]
        self.assertEqual(sentinel.incident_id("cloud", "github-unassigned", HEAD), actual)

    def test_ids_fail_closed_for_boolean_negative_float_string_and_overflow(self):
        for field in ("run_id", "job_id"):
            for bad in (True, False, 0, -1, 1.0, "222", 2**63):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaisesRegex(ValueError, field):
                        inspect(queued(**{field: bad}))

    def test_terminal_status_cannot_disagree_with_conclusion(self):
        for status, conclusion in (("success", "failure"), ("failure", "success"),
                                   ("cancelled", "failure")):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "contradictory terminal status"):
                    inspect(failed(job_status=status, conclusion=conclusion))

    def test_matching_legacy_terminal_status_still_classifies(self):
        for status in ("failure", "success", "cancelled"):
            item = inspect(failed(job_status=status, conclusion=status))["projects"][0]
            self.assertEqual("stalled" if status == "failure" else "unknown", item["state"])

    def test_new_identity_not_exposed_as_raw_payload(self):
        item = inspect(queued(run_id=222, job_id=333))
        blob = str(item)
        self.assertNotIn("run_id", blob)
        self.assertNotIn("job_id", blob)
        self.assertFalse(item["mutations_performed"])
        self.assertFalse(item["deploy_authorized"])

    def test_wait_and_owner_gates_take_precedence_over_incident(self):
        guarded = queued(run_id=900)
        guarded["notion"] = {"human_gate": True}
        self.assertEqual([], inspect(guarded)["incidents"])
        guarded["notion"] = {}
        guarded["github"]["active_owner_pr"] = True
        self.assertEqual("owner_held", inspect(guarded)["projects"][0]["state"])


if __name__ == "__main__":
    unittest.main()
