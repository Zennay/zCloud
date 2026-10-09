"""Isolated real task-claim concurrency checks; never opens production history.db.

Run: python3 -m unittest tests.test_task_claims_isolated_races_20261009 -v
"""
import concurrent.futures
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zcloud_claims_under_test", ROOT / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class TaskClaimsIsolatedRaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-claims-test-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "claims-fixture.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE task_claims(
                project_id TEXT NOT NULL, claim_key TEXT NOT NULL,
                owner_id TEXT NOT NULL, worker_id TEXT NOT NULL DEFAULT '',
                acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
                lease_until TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(project_id,claim_key))""")
        self.patch_db = patch.object(server, "DB", self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)

    def acquire(self, owner, project="fixture", key="same"):
        return server.task_claim_acquire(project, key, owner, lease_seconds=60)

    def test_same_key_has_one_owner_under_concurrency(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(self.acquire, [f"owner-{i}" for i in range(8)]))
        self.assertEqual(sum(bool(x["acquired"]) for x in outcomes), 1)
        winner = next(i for i, x in enumerate(outcomes) if x["acquired"])
        self.assertEqual(outcomes[winner]["claim"]["owner_id"], f"owner-{winner}")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0], 1)

    def test_wrong_owner_cannot_renew_or_release(self):
        self.assertTrue(self.acquire("owner-A")["acquired"])
        self.assertFalse(server.task_claim_heartbeat("fixture", "same", "owner-B")["renewed"])
        self.assertFalse(server.task_claim_release("fixture", "same", "owner-B")["released"])
        self.assertEqual(server.task_claims("fixture")[0]["owner_id"], "owner-A")

    def test_wrong_project_or_key_cannot_renew_claim(self):
        self.assertTrue(self.acquire("owner-A")["acquired"])
        original = server.task_claims("fixture")[0]["lease_until"]
        self.assertFalse(server.task_claim_heartbeat("other-project", "same", "owner-A")["renewed"])
        self.assertFalse(server.task_claim_heartbeat("fixture", "other-key", "owner-A")["renewed"])
        self.assertFalse(server.task_claim_release("other-project", "same", "owner-A")["released"])
        self.assertFalse(server.task_claim_release("fixture", "other-key", "owner-A")["released"])
        self.assertEqual(server.task_claims("fixture")[0]["lease_until"], original)

    def test_lease_boundaries_are_bounded_in_current_api(self):
        # Current API clamps to [15,3600] rather than rejecting out-of-range
        # requests. This documents the current behavior; #1207 tracks
        # whether strict HTTP rejection should replace that contract.
        self.assertEqual(server._claim_lease_seconds(1), 15)
        self.assertEqual(server._claim_lease_seconds(15), 15)
        self.assertEqual(server._claim_lease_seconds(3600), 3600)
        self.assertEqual(server._claim_lease_seconds(99999), 3600)

    def test_expired_owner_cannot_renew_or_delete_reclaimed_claim(self):
        self.assertTrue(self.acquire("owner-A")["acquired"])
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE task_claims SET lease_until='2000-01-01T00:00:00+00:00'")
        self.assertFalse(server.task_claim_heartbeat("fixture", "same", "owner-A")["renewed"])
        self.assertTrue(self.acquire("owner-B")["acquired"])
        self.assertFalse(server.task_claim_release("fixture", "same", "owner-A")["released"])
        self.assertEqual(server.task_claims("fixture")[0]["owner_id"], "owner-B")

    def test_distinct_keys_and_projects_do_not_collide(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(self.acquire, "a", "alpha", "x"),
                pool.submit(self.acquire, "b", "alpha", "y"),
                pool.submit(self.acquire, "c", "beta", "x"),
                pool.submit(self.acquire, "d", "beta", "y"),
            ]
            self.assertTrue(all(f.result()["acquired"] for f in futures))
        self.assertEqual(len(server.task_claims()), 4)

    def test_same_owner_reacquire_preserves_original_acquisition(self):
        first = self.acquire("owner-A")
        self.assertTrue(first["acquired"])
        second = self.acquire("owner-A")
        self.assertTrue(second["acquired"])
        self.assertEqual(first["claim"]["acquired_at"], second["claim"]["acquired_at"])
        self.assertEqual(server.task_claims("fixture")[0]["owner_id"], "owner-A")

    def test_foreign_owner_cannot_reacquire_existing_unexpired_claim(self):
        self.assertTrue(self.acquire("owner-A")["acquired"])
        result = self.acquire("owner-B")
        self.assertFalse(result["acquired"])
        self.assertEqual(server.task_claims("fixture")[0]["owner_id"], "owner-A")

    def test_prune_expired_only_in_temporary_fixture(self):
        self.assertTrue(self.acquire("owner-A", "fixture", "expired")["acquired"])
        self.assertTrue(self.acquire("owner-B", "fixture", "active")["acquired"])
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE task_claims SET lease_until='2000-01-01T00:00:00+00:00' WHERE claim_key='expired'")
        self.assertEqual([row["claim_key"] for row in server.task_claims("fixture")], ["active"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0], 1)

    def test_missing_identity_cannot_create_claim(self):
        for project, key, owner in [("", "valid", "owner"), ("fixture", "", "owner"), ("fixture", "key", "")]:
            with self.assertRaises(ValueError):
                server.task_claim_acquire(project, key, owner)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM task_claims").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
