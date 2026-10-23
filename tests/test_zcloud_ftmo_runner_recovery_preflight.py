from copy import deepcopy
from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_ftmo_runner_recovery_preflight.py"
SPEC = importlib.util.spec_from_file_location("ftmo_recovery_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def snapshot(status="orphaned_processes", *, active="inactive", listeners=1, workers=0):
    advice = {
        "orphaned_processes": "reconcile_orphans_before_service_start",
        "offline": "service_start_candidate",
        "busy": "leave_inflight_work_untouched",
        "idle": "runner_ready",
        "degraded": "inspect_listener_state",
    }[status]
    candidate = {
        "unit": MODULE.EXPECTED_UNIT,
        "active_state": active,
        "sub_state": "dead" if active != "active" else "running",
        "exec_main_status": "0",
        "restart_count": 0,
        "listener_count": listeners,
        "worker_count": workers,
        "status": status,
        "recovery_advice": advice,
    }
    return {
        "policy": MODULE.DIAGNOSTIC_POLICY,
        "mutation_performed": False,
        "runner_service_count": 5,
        "candidate_count": 1,
        "status": status,
        "candidates": [candidate],
    }


class FtmoRunnerRecoveryPreflightTests(unittest.TestCase):
    def test_live_orphan_state_is_denied(self):
        result = MODULE.classify(snapshot(), same_job_live_evidence=True)
        self.assertEqual("deny_orphaned_processes", result["decision"])
        self.assertFalse(result["mutation_authorized"])
        self.assertEqual("reconcile_orphans_first", result["writer_action_candidate"])

    def test_clean_offline_state_is_only_a_start_candidate(self):
        result = MODULE.classify(
            snapshot("offline", active="inactive", listeners=0, workers=0),
            same_job_live_evidence=True,
        )
        self.assertEqual("start_candidate", result["decision"])
        self.assertFalse(result["mutation_authorized"])
        self.assertEqual("guarded_service_start", result["writer_action_candidate"])

    def test_busy_runner_preserves_inflight_work(self):
        result = MODULE.classify(
            snapshot("busy", active="active", listeners=1, workers=1),
            same_job_live_evidence=True,
        )
        self.assertEqual("deny_inflight_work", result["decision"])
        self.assertEqual("leave_runner_untouched", result["writer_action_candidate"])

    def test_idle_runner_needs_no_recovery(self):
        result = MODULE.classify(
            snapshot("idle", active="active", listeners=1, workers=0),
            same_job_live_evidence=True,
        )
        self.assertEqual("no_recovery_needed", result["decision"])

    def test_degraded_runner_is_denied(self):
        result = MODULE.classify(
            snapshot("degraded", active="active", listeners=0, workers=0),
            same_job_live_evidence=True,
        )
        self.assertEqual("deny_degraded", result["decision"])

    def test_busy_advice_mismatch_fails_closed(self):
        payload = snapshot("busy", active="active", listeners=1, workers=1)
        payload["candidates"][0]["recovery_advice"] = "runner_ready"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("busy_recovery_advice_mismatch", result["reason"])

    def test_idle_advice_mismatch_fails_closed(self):
        payload = snapshot("idle", active="active", listeners=1, workers=0)
        payload["candidates"][0]["recovery_advice"] = "inspect_listener_state"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("idle_recovery_advice_mismatch", result["reason"])

    def test_degraded_advice_mismatch_fails_closed(self):
        payload = snapshot("degraded", active="active", listeners=0, workers=0)
        payload["candidates"][0]["recovery_advice"] = "runner_ready"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("degraded_recovery_advice_mismatch", result["reason"])

    def test_degraded_status_with_healthy_listener_fails_closed(self):
        payload = snapshot("degraded", active="active", listeners=1, workers=0)
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("degraded_classification_incoherent", result["reason"])

    def test_stored_snapshot_without_same_job_proof_fails_closed(self):
        result = MODULE.classify(snapshot(), same_job_live_evidence=False)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("fresh_live_evidence_not_proven", result["reason"])

    def test_mixed_aggregate_status_fails_closed(self):
        payload = snapshot()
        payload["status"] = "offline"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("aggregate_status_mismatch", result["reason"])

    def test_wrong_unit_fails_closed(self):
        payload = snapshot()
        payload["candidates"][0]["unit"] = "actions.runner.other.service"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("candidate_unit_mismatch", result["reason"])

    def test_multiple_candidates_fail_closed(self):
        payload = snapshot()
        payload["candidates"].append(deepcopy(payload["candidates"][0]))
        payload["candidate_count"] = 2
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("candidate_identity_not_unique", result["reason"])

    def test_orphan_status_without_residual_process_fails_closed(self):
        payload = snapshot(listeners=0, workers=0)
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("orphan_classification_incoherent", result["reason"])

    def test_unexpected_payload_key_fails_closed(self):
        payload = snapshot()
        payload["raw_log"] = "must never be accepted"
        result = MODULE.classify(payload, same_job_live_evidence=True)
        self.assertEqual("deny_incomplete", result["decision"])
        self.assertEqual("unexpected_top_level_schema", result["reason"])


if __name__ == "__main__":
    unittest.main()
