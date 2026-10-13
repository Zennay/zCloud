"""Offline regression matrix for zCloud missing-status retry admission; never contacts VPS."""
import concurrent.futures
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_recovery_retry_policy_20261013.py"
spec = importlib.util.spec_from_file_location("retry_policy", SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
S = "a" * 40
T = "b" * 40


def item(num, sha=S, event="push", status="completed", conclusion="success", branch="main", attempt=1):
    return dict(id=num, run_attempt=attempt, head_sha=sha, head_branch=branch,
                event=event, status=status, conclusion=conclusion)


def evidence(sha=S):
    def page(workflow, rows):
        return dict(complete=True, has_next_page=False, sha=sha, workflow=workflow, runs=rows)
    return dict(schema=policy.SCHEMA, repository=policy.REPO, main_sha=sha,
                production_status=dict(complete=True, sha=sha, statuses=[]),
                deploy=page(policy.WORKFLOWS["deploy"], []),
                regression=page(policy.WORKFLOWS["regression"], [item(1, sha)]))


class RetryPolicyTests(unittest.TestCase):
    def check(self, snapshot, expected, reason=None):
        result = policy.classify(snapshot)
        self.assertEqual(result["decision"], expected, result)
        self.assertIs(result["authorized"], False)
        if reason:
            self.assertEqual(result["reason"], reason)
        return result

    def test_noop_for_every_existing_production_context(self):
        for state in ("success", "failure", "pending", "error"):
            obj = evidence()
            obj["production_status"]["statuses"] = [dict(context="zcloud/vps-production", state=state)]
            observed = self.check(obj, "NOOP", "production_status_present")
            self.assertEqual(observed["production_state"], state)

    def test_conflicting_production_states_never_appear_green(self):
        obj = evidence()
        obj["production_status"]["statuses"] = [
            {"context": "zcloud/vps-production", "state": "failure"},
            {"context": "zcloud/vps-production", "state": "success"},
        ]
        self.check(obj, "DENY", "conflicting_production_status")

    def test_noop_on_failed_state_without_run_history(self):
        obj = evidence()
        del obj["deploy"]
        del obj["regression"]
        obj["production_status"]["statuses"] = [
            {"context": "zcloud/vps-production", "state": "failure"}
        ]
        observed = self.check(obj, "NOOP", "production_status_present")
        self.assertEqual(observed["production_state"], "failure")

    def test_green_is_not_release_authority(self):
        self.check(evidence(), "READY_FOR_EXCLUSIVE_RESERVATION", "candidate_only")

    def test_pending_deploy_never_restarts_regression(self):
        for state in policy.ACTIVE:
            obj = evidence()
            obj["deploy"]["runs"] = [item(2, event="workflow_run", status=state, conclusion=None)]
            self.check(obj, "WAIT", "deploy_active")

    def test_terminal_deploy_blocks_same_sha_regression_rearm(self):
        for conclusion in policy.TERMINAL:
            obj = evidence()
            obj["deploy"]["runs"] = [item(2, event="workflow_run", conclusion=conclusion)]
            self.check(obj, "DENY", "terminal_deploy_missing_status")

    def test_active_regression_waits(self):
        for state in policy.ACTIVE:
            obj = evidence()
            obj["regression"]["runs"].append(item(2, status=state, conclusion=None))
            self.check(obj, "WAIT", "regression_active")

    def test_no_successful_allowed_regression_waits(self):
        for items in ([], [item(2, conclusion="failure")], [item(2, event="workflow_run")]):
            obj = evidence()
            obj["regression"]["runs"] = items
            self.check(obj, "WAIT", "no_trusted_regression")

    def test_changed_sha_is_new_candidate_not_automatic_retry(self):
        self.check(evidence(T), "READY_FOR_EXCLUSIVE_RESERVATION")

    def test_status_sha_mismatch_or_missing_denies(self):
        obj = evidence(); obj["production_status"]["sha"] = T
        self.check(obj, "DENY", "missing_or_stale_status")
        obj = evidence(); obj["production_status"]["complete"] = False
        self.check(obj, "DENY", "missing_or_stale_status")

    def test_missing_page_or_unfinished_pagination_denies(self):
        for key in ("deploy", "regression"):
            for name, value in (("complete", False), ("has_next_page", True),
                                ("sha", T), ("runs", None), ("workflow", "other")):
                obj = evidence(); obj[key][name] = value
                self.check(obj, "DENY")

    def test_invalid_event_identity_and_duplicates_denied(self):
        obj = evidence(); obj["regression"]["runs"].append(item(1))
        self.check(obj, "DENY", "regression_ambiguous_attempt")
        obj = evidence(); obj["regression"]["runs"][0]["run_attempt"] = True
        self.check(obj, "DENY", "regression_ambiguous_attempt")
        obj = evidence(); obj["regression"]["runs"][0]["head_sha"] = "invalid"
        self.check(obj, "DENY", "regression_invalid_sha")

    def test_wrong_branch_or_deploy_source_denied(self):
        obj = evidence(); obj["regression"]["runs"][0]["head_branch"] = "feature"
        self.check(obj, "DENY", "regression_wrong_branch")
        obj = evidence(); obj["deploy"]["runs"] = [item(2, event="push")]
        self.check(obj, "DENY", "untrusted_deploy_origin")

    def test_contradictory_outcomes_denied(self):
        obj = evidence(); obj["regression"]["runs"][0]["status"] = "queued"
        self.check(obj, "DENY", "regression_contradictory_outcome")

    def test_non_scalar_evidence_is_denied_without_exception(self):
        for field, value in (("event", {}), ("status", []), ("conclusion", [])):
            obj = evidence()
            obj["regression"]["runs"][0][field] = value
            self.check(obj, "DENY", "malformed_evidence_types")

    def test_wrong_repository_and_schema_denied(self):
        for field, value in (("repository", "someone/else"), ("schema", "old"), ("main_sha", "bad")):
            obj = evidence(); obj[field] = value
            self.check(obj, "DENY")

    def test_one_reservation_then_never_retry_on_unknown_dispatch_result(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            candidate = policy.classify(evidence())
            self.assertTrue(policy.reserve_reference(ledger, candidate))
            self.assertFalse(policy.reserve_reference(ledger, candidate))
            with sqlite3.connect(ledger) as conn:
                self.assertEqual(conn.execute("SELECT state FROM recovery_retry_reference").fetchone()[0], "reserved")

    def test_new_sha_does_not_rearm_previous_sha(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            self.assertTrue(policy.reserve_reference(ledger, policy.classify(evidence())))
            self.assertTrue(policy.reserve_reference(ledger, policy.classify(evidence(T))))
            self.assertFalse(policy.reserve_reference(ledger, policy.classify(evidence())))

    def test_concurrent_tick_reservation_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            candidate = policy.classify(evidence())
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: policy.reserve_reference(ledger, candidate), range(16)))
            self.assertEqual(sum(results), 1, results)

    def test_manual_approval_not_implicitly_supported(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            self.assertFalse(policy.reserve_reference(ledger, policy.classify(evidence()), intent="manual_nonce"))
            self.assertFalse(policy.reserve_reference(ledger, dict(decision="READY_FOR_EXCLUSIVE_RESERVATION", sha=S, authorized=True)))
            self.assertFalse(ledger.exists())

    def test_cli_duplicate_keys_denied_without_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td) / "evidence.json"
            fixture.write_text(json.dumps(evidence()))
            proc = subprocess.run([sys.executable, str(SCRIPT), str(fixture)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(json.loads(proc.stdout)["authorized"])
            fixture.write_text('{"schema":"old","schema":"new"}')
            proc = subprocess.run([sys.executable, str(SCRIPT), str(fixture)], capture_output=True, text=True)
            self.assertEqual(json.loads(proc.stdout)["reason"], "ambiguous_or_unreadable_json")

    def test_no_live_api_or_privileged_mutations(self):
        code = SCRIPT.read_text()
        for forbidden in ("urllib.request", "requests.post", "/dispatches", "systemctl", "history.db"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
