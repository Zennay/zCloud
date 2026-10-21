"""Additional offline SQLite lease transition cases for PR #1219.

Runs against the reference implementation only, never against production.
"""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_task_claim_lease_reference_w30 import ClaimStore


class ClaimLeaseTransitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'claims.sqlite'
        self.store = ClaimStore(self.db)

    def rows(self):
        with sqlite3.connect(self.db) as connection:
            return connection.execute('SELECT project,claim_key,owner,expires FROM claims').fetchall()

    def test_owner_release_allows_immediate_new_claim(self):
        self.assertTrue(self.store.act("acquire", "cloud", "lane", "worker-a", now=100))
        self.assertTrue(self.store.act("release", "cloud", "lane", "worker-a", now=101))
        self.assertTrue(self.store.act("acquire", "cloud", "lane", "worker-b", now=101))
        self.assertEqual(self.rows()[0][2], "worker-b")

    def test_heartbeat_extends_lease_without_changing_owner(self):
        self.assertTrue(self.store.act("acquire", "cloud", "lane", "worker-a", now=100))
        self.assertTrue(self.store.act("heartbeat", "cloud", "lane", "worker-a", now=114))
        self.assertFalse(self.store.act("acquire", "cloud", "lane", "worker-b", now=115))
        self.assertEqual(self.rows()[0][3], 129)

    def test_expired_release_cannot_erase_claim(self):
        self.assertTrue(self.store.act("acquire", "cloud", "lane", "old", now=100))
        self.assertFalse(self.store.act("release", "cloud", "lane", "old", now=115))
        self.assertTrue(self.store.act("acquire", "cloud", "lane", "new", now=115))
        self.assertEqual(self.rows()[0][2], "new")

    def test_unknown_action_has_no_side_effect(self):
        self.assertFalse(self.store.act("promote", "cloud", "lane", "actor", now=100))
        self.assertEqual(self.rows(), [])

    def test_parallel_distinct_claims_are_independent(self):
        keys = [(f"project-{i % 3}", f"task-{i}") for i in range(24)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(
                lambda item: self.store.act("acquire", item[0], item[1], "worker", now=100),
                keys))
        self.assertTrue(all(outcomes))
        self.assertEqual(len(self.rows()), len(keys))


if __name__ == "__main__":
    unittest.main()
