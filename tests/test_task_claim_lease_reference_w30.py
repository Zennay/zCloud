"""Offline task-claim lease concurrency contract; never opens production history.db.

Reference behavior only: runtime/API conformance still requires separate integration tests.
"""
import concurrent.futures
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path


class ClaimStore:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE claims (
                project TEXT NOT NULL, claim_key TEXT NOT NULL,
                owner TEXT NOT NULL, expires REAL NOT NULL,
                PRIMARY KEY(project, claim_key))""")

    def act(self, action, project, key, owner, lease=15, now=None):
        if not all(isinstance(x, str) and x.strip() for x in (project, key, owner)):
            return False
        if type(lease) is not int or not 15 <= lease <= 3600:
            return False
        now = time.time() if now is None else now
        with sqlite3.connect(self.path, timeout=10, isolation_level=None) as db:
            db.execute("BEGIN IMMEDIATE")
            if action == "acquire":
                db.execute("""INSERT INTO claims VALUES (?,?,?,?)
                    ON CONFLICT(project,claim_key) DO UPDATE SET
                    owner=excluded.owner, expires=excluded.expires
                    WHERE claims.expires <= ?""",
                    (project, key, owner, now + lease, now))
                changed = db.execute("SELECT changes()").fetchone()[0]
            elif action == "heartbeat":
                db.execute("""UPDATE claims SET expires=?
                    WHERE project=? AND claim_key=? AND owner=? AND expires>?""",
                    (now + lease, project, key, owner, now))
                changed = db.execute("SELECT changes()").fetchone()[0]
            elif action == "release":
                db.execute("""DELETE FROM claims WHERE project=? AND claim_key=?
                    AND owner=? AND expires>?""",
                    (project, key, owner, now))
                changed = db.execute("SELECT changes()").fetchone()[0]
            else:
                changed = 0
            db.commit()
            return changed == 1


class ClaimLeaseMatrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "claims.sqlite"
        self.store = ClaimStore(self.db)

    def rows(self):
        with sqlite3.connect(self.db) as db:
            return db.execute("SELECT project,claim_key,owner,expires FROM claims").fetchall()

    def test_simultaneous_contenders_single_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            wins = list(pool.map(
                lambda i: self.store.act("acquire", "cloud", "queue", str(i), now=100),
                range(8)))
        self.assertEqual(sum(wins), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_nonowner_and_expired_heartbeat_denied(self):
        self.assertTrue(self.store.act("acquire", "cloud", "x", "a", now=100))
        for project, key, owner, now in [
            ("cloud", "x", "b", 101), ("wrong", "x", "a", 101),
            ("cloud", "wrong", "a", 101), ("cloud", "x", "a", 115)]:
            self.assertFalse(self.store.act("heartbeat", project, key, owner, now=now))
        self.assertEqual(self.rows()[0][3], 115)

    def test_expired_reclaim_fences_old_owner(self):
        self.assertTrue(self.store.act("acquire", "cloud", "x", "old", now=100))
        self.assertTrue(self.store.act("acquire", "cloud", "x", "new", now=115))
        self.assertFalse(self.store.act("release", "cloud", "x", "old", now=116))
        self.assertFalse(self.store.act("heartbeat", "cloud", "x", "old", now=116))
        self.assertEqual(self.rows()[0][2], "new")

    def test_boundaries_and_invalid_identity(self):
        for lease in (0, 14, 3601, True, "15"):
            self.assertFalse(self.store.act("acquire", "p", "k", "a", lease, now=100))
        for project, key, owner in (("", "k", "a"), ("p", "", "a"), ("p", "k", None)):
            self.assertFalse(self.store.act("acquire", project, key, owner, now=100))
        self.assertEqual(self.rows(), [])
        self.assertTrue(self.store.act("acquire", "p", "k", "a", 15, now=100))
        self.assertTrue(self.store.act("acquire", "q", "k", "a", 3600, now=100))

    def test_different_keys_independent(self):
        self.assertTrue(self.store.act("acquire", "cloud", "a", "one", now=100))
        self.assertTrue(self.store.act("acquire", "cloud", "b", "two", now=100))
        self.assertTrue(self.store.act("acquire", "other", "a", "three", now=100))
        self.assertFalse(self.store.act("release", "cloud", "a", "two", now=101))
        self.assertEqual(len(self.rows()), 3)


if __name__ == "__main__":
    unittest.main()
