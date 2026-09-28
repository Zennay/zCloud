import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import sys
import types
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
enhancements_stub.load_resource_policy = lambda: {}
sys.modules["enhancements"] = enhancements_stub

import server


class TaskClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-claims-")
        self.original_db = server.DB
        self.original_cache = server.CACHE
        server.DB = Path(self.tmp.name) / "history.db"
        server.CACHE = None
        self.original_vps_health = server.coordination_vps_health
        server.coordination_vps_health = lambda: {
            "ok": True,
            "checks": {"zcloud_service": True, "firefox_automation": True, "state_store": True},
        }
        server.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DB = self.original_db
        server.CACHE = self.original_cache
        server.coordination_vps_health = self.original_vps_health
        self.tmp.cleanup()

    def request(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def preflight(self, owner, worker):
        status, body = self.request(
            "/api/worker-preflight",
            {
                "project_id": "cloud",
                "worker_id": worker,
                "owner_id": owner,
                "notion": {
                    "checked": True,
                    "project_ref": "https://app.notion.com/p/3e79e19ac955811d8fd4d35d176bdeb8",
                    "handoff_ref": "https://app.notion.com/p/3e79e19ac955819e9ccee5bec98bbb9c",
                },
                "github": {
                    "checked": True,
                    "repo": "Zennay/zCloud",
                    "main_sha": "abcdef1",
                    "open_prs": [],
                    "branches": [],
                },
            },
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        return body

    def test_claim_is_durable_across_init_db(self):
        result = server.task_claim_acquire("cloud", "notion:task-1", "owner-a", "cloud::w1", 120)
        self.assertTrue(result["acquired"])
        server.init_db()
        claims = server.task_claims("cloud")
        self.assertEqual(1, len(claims))
        self.assertEqual("owner-a", claims[0]["owner_id"])

    def test_two_workers_cannot_claim_same_task(self):
        barrier = threading.Barrier(3)
        results = []
        lock = threading.Lock()

        def worker(owner):
            barrier.wait()
            result = server.task_claim_acquire("cloud", "notion:race", owner, owner, 120)
            with lock:
                results.append((owner, result["acquired"]))

        threads = [threading.Thread(target=worker, args=(owner,)) for owner in ("owner-a", "owner-b")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        winners = [owner for owner, acquired in results if acquired]
        self.assertEqual(1, len(winners), results)
        claims = server.task_claims("cloud")
        race = next(c for c in claims if c["claim_key"] == "notion:race")
        self.assertEqual(winners[0], race["owner_id"])

    def test_stale_claim_is_automatically_recovered(self):
        expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with server.connect() as conn:
            conn.execute(
                """INSERT INTO task_claims(project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("cloud", "notion:stale", "dead-owner", "cloud::w1", old, old, expired, "{}"),
            )
        result = server.task_claim_acquire("cloud", "notion:stale", "new-owner", "cloud::w2", 120)
        self.assertTrue(result["acquired"])
        self.assertEqual("new-owner", result["claim"]["owner_id"])

    def test_different_claim_keys_with_same_capability_conflict(self):
        first = server.task_claim_acquire(
            "cloud", "task:a", "owner-a", "cloud::w1", 120,
            {"conflict_scope": {"capabilities": ["runner-control"], "files": []}},
        )
        self.assertTrue(first["acquired"])
        second = server.task_claim_acquire(
            "cloud", "task:b", "owner-b", "cloud::w2", 120,
            {"conflict_scope": {"capabilities": ["runner-control"], "files": []}},
        )
        self.assertFalse(second["acquired"])
        self.assertEqual("scope_conflict", second["blocked"])
        self.assertEqual("task:a", second["conflict"]["claim_key"])
        self.assertEqual(["runner-control"], second["conflict"]["overlap"]["capabilities"])

    def test_file_scope_prefix_conflicts_but_disjoint_scope_does_not(self):
        first = server.task_claim_acquire(
            "cloud", "task:ui", "owner-a", "cloud::w1", 120,
            {"conflict_scope": {"capabilities": [], "files": ["public/"]}},
        )
        self.assertTrue(first["acquired"])
        blocked = server.task_claim_acquire(
            "cloud", "task:app", "owner-b", "cloud::w2", 120,
            {"conflict_scope": {"capabilities": [], "files": ["public/app.js"]}},
        )
        self.assertFalse(blocked["acquired"])
        self.assertEqual(["public"], blocked["conflict"]["overlap"]["files"])

        allowed = server.task_claim_acquire(
            "cloud", "task:server", "owner-b", "cloud::w2", 120,
            {"conflict_scope": {"capabilities": ["api"], "files": ["server.py"]}},
        )
        self.assertTrue(allowed["acquired"])

    def test_same_owner_may_hold_overlapping_scopes(self):
        scope={"conflict_scope": {"capabilities": ["claims"], "files": ["server.py"]}}
        first=server.task_claim_acquire("cloud","task:first","owner-a","cloud::w1",120,scope)
        second=server.task_claim_acquire("cloud","task:second","owner-a","cloud::w1",120,scope)
        self.assertTrue(first["acquired"])
        self.assertTrue(second["acquired"])

    def test_expired_overlapping_scope_is_ignored(self):
        expired=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat()
        old=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        metadata=json.dumps({"conflict_scope":{"capabilities":["claims"],"files":["server.py"]}})
        with server.connect() as conn:
            conn.execute(
                """INSERT INTO task_claims(project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("cloud","task:old","owner-a","cloud::w1",old,old,expired,metadata),
            )
        result=server.task_claim_acquire(
            "cloud","task:new","owner-b","cloud::w2",120,
            {"conflict_scope":{"capabilities":["claims"],"files":["server.py"]}},
        )
        self.assertTrue(result["acquired"])

    def test_scope_conflict_race_allows_exactly_one_owner(self):
        barrier=threading.Barrier(3)
        results=[]
        lock=threading.Lock()
        metadata={"conflict_scope":{"capabilities":["race-capability"],"files":["server.py"]}}

        def worker(owner,key):
            barrier.wait()
            result=server.task_claim_acquire("cloud",key,owner,owner,120,metadata)
            with lock:
                results.append((owner,result["acquired"],result.get("blocked")))

        threads=[
            threading.Thread(target=worker,args=("owner-a","task:race-a")),
            threading.Thread(target=worker,args=("owner-b","task:race-b")),
        ]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)

        winners=[owner for owner,acquired,_ in results if acquired]
        self.assertEqual(1,len(winners),results)
        losers=[blocked for _,acquired,blocked in results if not acquired]
        self.assertEqual(["scope_conflict"],losers)

    def test_unsafe_conflict_scope_path_is_rejected(self):
        with self.assertRaises(ValueError):
            server.task_claim_acquire(
                "cloud","task:unsafe","owner-a","cloud::w1",120,
                {"conflict_scope":{"files":["../server.py"]}},
            )

    def test_oversized_conflict_scope_metadata_is_rejected_before_persistence(self):
        capabilities=["shared-capability"]+[f"cap-{i:03d}-"+"x"*120 for i in range(45)]
        with self.assertRaisesRegex(ValueError,"claimmetadata is te groot"):
            server.task_claim_acquire(
                "cloud","task:oversized","owner-a","cloud::w1",120,
                {"conflict_scope":{"capabilities":capabilities,"files":["server.py"]}},
            )
        with server.connect() as conn:
            row=conn.execute(
                "SELECT metadata_json FROM task_claims WHERE project_id=? AND claim_key=?",
                ("cloud","task:oversized"),
            ).fetchone()
        self.assertIsNone(row)

    def test_unreadable_existing_claim_metadata_blocks_scoped_claim_fail_closed(self):
        now=datetime.now(timezone.utc)
        with server.connect() as conn:
            conn.execute(
                """INSERT INTO task_claims(project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    "cloud","task:corrupt","owner-a","cloud::w1",
                    now.isoformat(),now.isoformat(),(now+timedelta(minutes=2)).isoformat(),
                    '{"conflict_scope":{"capabilities":["shared-capability"]',
                ),
            )
        result=server.task_claim_acquire(
            "cloud","task:new","owner-b","cloud::w2",120,
            {"conflict_scope":{"capabilities":["shared-capability"],"files":["server.py"]}},
        )
        self.assertFalse(result["acquired"])
        self.assertEqual("scope_conflict",result["blocked"])
        self.assertEqual("unreadable_claim_metadata",result["conflict"]["reason"])
        self.assertIn(
            "unreadable-claim-metadata",
            result["conflict"]["overlap"]["capabilities"],
        )

    def test_conflicted_primary_selects_first_safe_alternative(self):
        blocker=server.task_claim_acquire(
            "cloud","task:blocker","owner-a","cloud::w1",120,
            {"conflict_scope":{"capabilities":["shared"],"files":["server.py"]}},
        )
        self.assertTrue(blocker["acquired"])
        result=server.task_claim_acquire(
            "cloud","task:primary","owner-b","cloud::w2",120,
            {"conflict_scope":{"capabilities":["shared"],"files":["server.py"]}},
            [
                {
                    "claim_key":"task:alt-conflict",
                    "metadata":{"conflict_scope":{"capabilities":["shared"],"files":["other.py"]}},
                },
                {
                    "claim_key":"task:alt-free",
                    "metadata":{"conflict_scope":{"capabilities":["independent"],"files":["tests/free.py"]}},
                },
            ],
        )
        self.assertTrue(result["acquired"],result)
        self.assertEqual("task:alt-free",result["selected_claim_key"])
        self.assertEqual("alternative",result["selected_from"])
        self.assertEqual(1,result["selected_index"])
        self.assertEqual(["task:primary","task:alt-conflict"],[x["claim_key"] for x in result["attempted"]])

    def test_exact_task_conflict_can_fall_back_to_alternative(self):
        first=server.task_claim_acquire(
            "cloud","task:same","owner-a","cloud::w1",120,
            {"conflict_scope":{"capabilities":["alpha"],"files":["a.py"]}},
        )
        self.assertTrue(first["acquired"])
        result=server.task_claim_acquire(
            "cloud","task:same","owner-b","cloud::w2",120,
            {"conflict_scope":{"capabilities":["beta"],"files":["b.py"]}},
            [{
                "claim_key":"task:fallback",
                "metadata":{"conflict_scope":{"capabilities":["gamma"],"files":["c.py"]}},
            }],
        )
        self.assertTrue(result["acquired"],result)
        self.assertEqual("task:fallback",result["selected_claim_key"])
        self.assertEqual("task_conflict",result["attempted"][0]["blocked"])

    def test_no_safe_alternative_fails_closed_without_extra_claim(self):
        blocker=server.task_claim_acquire(
            "cloud","task:blocker","owner-a","cloud::w1",120,
            {"conflict_scope":{"capabilities":["shared"],"files":["server.py"]}},
        )
        self.assertTrue(blocker["acquired"])
        result=server.task_claim_acquire(
            "cloud","task:primary","owner-b","cloud::w2",120,
            {"conflict_scope":{"capabilities":["shared"],"files":["server.py"]}},
            [
                {"claim_key":"task:alt-a","metadata":{"conflict_scope":{"capabilities":["shared"],"files":["a.py"]}}},
                {"claim_key":"task:alt-b","metadata":{"conflict_scope":{"capabilities":["shared"],"files":["b.py"]}}},
            ],
        )
        self.assertFalse(result["acquired"])
        self.assertEqual("no_safe_alternative",result["blocked"])
        self.assertEqual(3,len(result["attempted"]))
        claims=server.task_claims("cloud")
        self.assertEqual(["task:blocker"],[x["claim_key"] for x in claims])

    def test_invalid_alternative_rejects_entire_request_before_primary_write(self):
        oversized=["cap-"+str(i)+"-"+"x"*140 for i in range(45)]
        with self.assertRaises(ValueError):
            server.task_claim_acquire(
                "cloud","task:primary","owner-a","cloud::w1",120,
                {"conflict_scope":{"capabilities":["primary"],"files":["server.py"]}},
                [{
                    "claim_key":"task:huge",
                    "metadata":{"conflict_scope":{"capabilities":oversized,"files":["huge.py"]}},
                }],
            )
        self.assertEqual([],server.task_claims("cloud"))

    def test_alternative_selection_race_assigns_distinct_safe_tasks(self):
        blocker=server.task_claim_acquire(
            "cloud","task:blocker","owner-blocker","cloud::w1",120,
            {"conflict_scope":{"capabilities":["blocked"],"files":["blocked.py"]}},
        )
        self.assertTrue(blocker["acquired"])
        barrier=threading.Barrier(3)
        results=[]
        lock=threading.Lock()
        alternatives=[
            {"claim_key":"task:free-a","metadata":{"conflict_scope":{"capabilities":["free-a"],"files":["free-a.py"]}}},
            {"claim_key":"task:free-b","metadata":{"conflict_scope":{"capabilities":["free-b"],"files":["free-b.py"]}}},
        ]
        def worker(owner):
            barrier.wait()
            result=server.task_claim_acquire(
                "cloud","task:blocked-"+owner,owner,owner,120,
                {"conflict_scope":{"capabilities":["blocked"],"files":["blocked.py"]}},
                alternatives,
            )
            with lock:
                results.append(result)

        threads=[threading.Thread(target=worker,args=("owner-a",)),threading.Thread(target=worker,args=("owner-b",))]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join(timeout=5)
        self.assertTrue(all(x["acquired"] for x in results),results)
        self.assertEqual({"task:free-a","task:free-b"},{x["selected_claim_key"] for x in results})

    def test_heartbeat_requires_current_unexpired_owner(self):
        first = server.task_claim_acquire("cloud", "notion:heartbeat", "owner-a", "cloud::w1", 60)
        before = first["claim"]["lease_until"]
        wrong = server.task_claim_heartbeat("cloud", "notion:heartbeat", "owner-b", 600)
        self.assertFalse(wrong["renewed"])
        renewed = server.task_claim_heartbeat("cloud", "notion:heartbeat", "owner-a", 600)
        self.assertTrue(renewed["renewed"])
        self.assertGreater(renewed["claim"]["lease_until"], before)

    def test_claim_api_conflict_release_and_reacquire(self):
        self.preflight("owner-a", "cloud::w1")
        status, first = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-a", "worker_id": "cloud::w1", "lease_seconds": 120},
        )
        self.assertEqual(200, status, first)

        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='cloud'")
        self.preflight("owner-b", "cloud::w2")
        status, conflict = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-b", "worker_id": "cloud::w2", "lease_seconds": 120},
        )
        self.assertEqual(409, status, conflict)
        self.assertEqual("owner-a", conflict["claim"]["owner_id"])

        status, released = self.request(
            "/api/task-claims",
            {"action": "release", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-a"},
        )
        self.assertEqual(200, status, released)

        # Claim landscape changed after owner-b's prior preflight, so refresh it.
        self.preflight("owner-b", "cloud::w2")
        status, second = self.request(
            "/api/task-claims",
            {"action": "acquire", "project_id": "cloud", "claim_key": "notion:api", "owner_id": "owner-b", "worker_id": "cloud::w2", "lease_seconds": 120},
        )
        self.assertEqual(200, status, second)
        self.assertTrue(second["acquired"])


if __name__ == "__main__":
    unittest.main(verbosity=2)