"""Isolated synthetic tests for the read-only resource-lease snapshot auditor."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.zcloud_resource_lease_snapshot_audit import (
    _strict_json,
    audit_snapshot,
    main,
)


NOW = "2026-10-09T17:00:00+00:00"


def fixture():
    contracts = {
        "schema_version": 1,
        "projects": {
            "cloud": {"compute": {"pool": "protected"}},
        },
        "resource_pools": {
            "protected": {"slots": 2},
            "disabled": {"slots": 0},
        },
    }
    lease = {
        "project_id": "cloud", "owner_id": "worker-one",
        "pool": "protected", "workload_class": "control-plane",
        "cpu_soft_cores": 1.0, "memory_soft_mb": 512,
        "acquired_at": "2026-10-09T16:55:00+00:00",
        "lease_until": "2026-10-09T17:25:00+00:00",
        "metadata": {"token": "never-print-private-metadata"},
    }
    snapshot = {
        "time": "2026-10-09T16:59:00+00:00",
        "pools": {
            "protected": {"capacity": 2, "used": 1, "available": 1, "holders": [deepcopy(lease)]},
            "disabled": {"capacity": 0, "used": 0, "available": 0, "holders": []},
        },
        "leases": [deepcopy(lease)],
    }
    return snapshot, contracts


class LeaseSnapshotAuditTests(unittest.TestCase):
    def audit(self, change=None):
        snapshot, contracts = fixture()
        if change is not None:
            change(snapshot, contracts)
        return audit_snapshot(snapshot, contracts, now=NOW)

    def denied(self, result, reason):
        self.assertFalse(result["ready_for_review"])
        self.assertFalse(result["safe_to_act"])
        self.assertFalse(result["mutation_performed"])
        self.assertIn(reason, result["errors"])

    def test_valid_snapshot_is_still_non_authorizing(self):
        result = self.audit()
        self.assertTrue(result["ready_for_review"])
        self.assertFalse(result["safe_to_act"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual([], result["errors"])
        self.assertEqual(1, result["lease_count"])

    def test_rejects_capacity_overstatement(self):
        def change(s, c):
            s["pools"]["protected"]["capacity"] = 999
        self.denied(self.audit(change), "capacity_contract_mismatch")

    def test_rejects_oversubscribed_pool(self):
        def change(s, c):
            s["pools"]["protected"].update(capacity=2, used=3, available=0)
        self.denied(self.audit(change), "invalid_pool_capacity_arithmetic")

    def test_rejects_lease_from_unregistered_project(self):
        def change(s, c):
            s["leases"][0]["project_id"] = "phantom"
        self.denied(self.audit(change), "unknown_lease_project")

    def test_rejects_lease_in_wrong_contract_pool(self):
        def change(s, c):
            c["projects"]["cloud"]["compute"]["pool"] = "disabled"
        self.denied(self.audit(change), "lease_pool_contract_mismatch")

    def test_rejects_missing_project_registry(self):
        def change(s, c):
            c.pop("projects")
        self.denied(self.audit(change), "invalid_project_registry")

    def test_rejects_boolean_counters(self):
        def change(s, c):
            s["pools"]["protected"]["used"] = True
        self.denied(self.audit(change), "invalid_pool_counters")

    def test_rejects_boolean_contract_capacity(self):
        def change(s, c):
            c["resource_pools"]["protected"]["slots"] = True
        self.denied(self.audit(change), "invalid_contract_capacity")

    def test_rejects_missing_pool(self):
        def change(s, c):
            s["pools"].pop("disabled")
        self.denied(self.audit(change), "pool_registry_mismatch")

    def test_rejects_unknown_pool_from_lease(self):
        def change(s, c):
            s["leases"][0]["pool"] = "untrusted"
        self.denied(self.audit(change), "unknown_lease_pool")

    def test_rejects_missing_holder(self):
        def change(s, c):
            s["pools"]["protected"]["holders"] = []
        self.denied(self.audit(change), "holder_count_or_set_mismatch")

    def test_rejects_duplicated_holder(self):
        def change(s, c):
            s["pools"]["protected"]["holders"].append(deepcopy(s["pools"]["protected"]["holders"][0]))
        self.denied(self.audit(change), "duplicate_holder")

    def test_rejects_mismatched_holder_identity(self):
        def change(s, c):
            s["pools"]["protected"]["holders"][0]["owner_id"] = "other-worker"
        self.denied(self.audit(change), "holder_missing_from_leases")

    def test_rejects_duplicate_lease(self):
        def change(s, c):
            s["leases"].append(deepcopy(s["leases"][0]))
        self.denied(self.audit(change), "duplicate_lease")

    def test_rejects_owner_at_truncation_boundary(self):
        def change(s, c):
            owner = "x" * 200
            s["leases"][0]["owner_id"] = owner
            s["pools"]["protected"]["holders"][0]["owner_id"] = owner
        self.denied(self.audit(change), "ambiguous_owner_storage_boundary")

    def test_rejects_future_snapshot(self):
        def change(s, c):
            s["time"] = "2026-10-09T17:00:00.000001+00:00"
        self.denied(self.audit(change), "future_snapshot")

    def test_rejects_stale_snapshot(self):
        def change(s, c):
            s["time"] = "2026-10-09T16:54:59+00:00"
        self.denied(self.audit(change), "stale_snapshot")

    def test_rejects_naive_snapshot_clock(self):
        def change(s, c):
            s["time"] = "2026-10-09T16:59:00"
        self.denied(self.audit(change), "invalid_snapshot_timestamp")

    def test_rejects_expired_lease_at_snapshot_time(self):
        def change(s, c):
            s["leases"][0]["lease_until"] = "2026-10-09T16:58:59+00:00"
        self.denied(self.audit(change), "invalid_lease_interval")

    def test_rejects_future_acquisition(self):
        def change(s, c):
            s["leases"][0]["acquired_at"] = "2026-10-09T17:01:00+00:00"
        self.denied(self.audit(change), "invalid_lease_interval")

    def test_rejects_negative_cpu(self):
        def change(s, c):
            s["leases"][0]["cpu_soft_cores"] = -1
        self.denied(self.audit(change), "invalid_lease_cpu")

    def test_rejects_non_integer_memory(self):
        def change(s, c):
            s["leases"][0]["memory_soft_mb"] = 512.5
        self.denied(self.audit(change), "invalid_lease_memory")

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
            _strict_json('{"pools":{},"pools":{"protected":{}}}')

    def test_rejects_nonfinite_cpu_observation(self):
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(cpu=repr(invalid)):
                def change(s, c):
                    s["leases"][0]["cpu_soft_cores"] = invalid
                self.denied(self.audit(change), "invalid_lease_cpu")

    def test_rejects_nonfinite_json_constants(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(ValueError, "nonfinite_json_number"):
                    _strict_json('{"cpu":' + token + '}')

    def test_rejects_bool_schema_version(self):
        def change(s, c):
            c["schema_version"] = True
        self.denied(self.audit(change), "invalid_contracts")

    def test_owner_cannot_be_leased_in_two_pools(self):
        def change(s, c):
            c["resource_pools"]["other"] = {"slots": 1}
            other = deepcopy(s["leases"][0])
            other["pool"] = "other"
            s["leases"].append(other)
            s["pools"]["other"] = {"capacity": 1, "used": 1, "available": 0, "holders": [deepcopy(other)]}
        self.denied(self.audit(change), "owner_assigned_multiple_pools")

    def test_output_does_not_expose_identity_or_metadata(self):
        result = self.audit()
        encoded = json.dumps(result)
        self.assertNotIn("worker-one", encoded)
        self.assertNotIn("never-print-private-metadata", encoded)

    def test_cli_loads_only_given_local_snapshots_and_emits_json(self):
        snapshot, contracts = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sp = root / "snapshot.json"
            cp = root / "contracts.json"
            sp.write_text(json.dumps(snapshot), encoding="utf-8")
            cp.write_text(json.dumps(contracts), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(["--snapshot", str(sp), "--contracts", str(cp), "--now", NOW])
        result = json.loads(stream.getvalue())
        self.assertEqual(0, status)
        self.assertTrue(result["ready_for_review"])
        self.assertFalse(result["safe_to_act"])

    def test_cli_rejects_oversized_input_before_parsing(self):
        from scripts.zcloud_resource_lease_snapshot_audit import MAX_INPUT_BYTES
        _, contracts = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sp = root / "snapshot.json"
            cp = root / "contracts.json"
            sp.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
            cp.write_text(json.dumps(contracts), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(["--snapshot", str(sp), "--contracts", str(cp), "--now", NOW])
        self.assertEqual(1, status)
        self.assertEqual(["input_unreadable_or_ambiguous"], json.loads(stream.getvalue())["errors"])

    def test_cli_fails_closed_on_ambiguous_input_json(self):
        _, contracts = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sp = root / "snapshot.json"
            cp = root / "contracts.json"
            sp.write_text('{"time":"unused","time":"duplicate"}', encoding="utf-8")
            cp.write_text(json.dumps(contracts), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(["--snapshot", str(sp), "--contracts", str(cp), "--now", NOW])
        self.assertEqual(1, status)
        self.assertEqual(["input_unreadable_or_ambiguous"], json.loads(stream.getvalue())["errors"])


if __name__ == "__main__":
    unittest.main()
