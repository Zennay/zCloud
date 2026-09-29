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


class WorkerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-preflight-")
        self.original_db = server.DB
        # These tests intentionally simulate the legacy multi-worker pool; production remains one slot.
        self.original_global_limit = server.GLOBAL_CHATGPT_WORKER_LIMIT
        self.original_max_workers = server.MAX_CHATGPT_WORKERS
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 2
        server.MAX_CHATGPT_WORKERS = 2
        self.original_cache = server.CACHE
        self.original_vps_health = server.coordination_vps_health
        server.DB = Path(self.tmp.name) / "history.db"
        server.CACHE = None
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
        server.GLOBAL_CHATGPT_WORKER_LIMIT = self.original_global_limit
        server.MAX_CHATGPT_WORKERS = self.original_max_workers
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

    def payload(self, owner="owner-a", worker="cloud::w1"):
        return {
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
                "main_sha": "1234567abcdef",
                "open_prs": [],
                "branches": ["main"],
            },
        }

    def test_worker_claim_without_preflight_is_blocked_and_manual_override_is_explicit(self):
        status, blocked = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "notion:blocked",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
            },
        )
        self.assertEqual(428, status, blocked)
        self.assertEqual("preflight_required", blocked["blocked"])

        status, implicit = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "manual:implicit",
                "owner_id": "human-operator",
            },
        )
        self.assertEqual(428, status, implicit)
        self.assertEqual("worker_identity_required", implicit["blocked"])

        status, missing_reason = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "manual:no-reason",
                "owner_id": "human-operator",
                "manual_override": True,
            },
        )
        self.assertEqual(400, status, missing_reason)

        status, manual = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "manual:recovery",
                "owner_id": "human-operator",
                "manual_override": True,
                "override_reason": "operator recovery",
            },
        )
        self.assertEqual(200, status, manual)
        self.assertTrue(manual["acquired"])
        self.assertTrue(manual["claim"]["metadata"]["manual_override"])
        self.assertEqual("operator recovery", manual["claim"]["metadata"]["manual_override_reason"])

    def test_zcloud_self_improvement_claim_requires_conflict_scope(self):
        status, _ = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status)
        status, blocked = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:no-scope",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {"loop": "self_improvement"},
            },
        )
        self.assertEqual(428, status, blocked)
        self.assertEqual("conflict_scope_required", blocked["blocked"])

        status, allowed = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:scoped",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {
                    "loop": "self_improvement",
                    "conflict_scope": {
                        "capabilities": ["claim-admission"],
                        "files": ["server.py"],
                    },
                },
            },
        )
        self.assertEqual(200, status, allowed)
        self.assertTrue(allowed["acquired"])
        self.assertEqual(
            ["claim-admission"],
            allowed["claim"]["metadata"]["conflict_scope"]["capabilities"],
        )

    def test_scope_conflict_blocks_different_claim_key_after_fresh_preflight(self):
        status, _ = self.request("/api/worker-preflight", self.payload("owner-a", "cloud::w1"))
        self.assertEqual(200, status)
        status, first = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:first",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {
                    "loop": "self_improvement",
                    "conflict_scope": {
                        "capabilities": ["shared-feature"],
                        "files": ["server.py"],
                    },
                },
            },
        )
        self.assertEqual(200, status, first)

        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='cloud'")
        status, preflight = self.request("/api/worker-preflight", self.payload("owner-b", "cloud::w2"))
        self.assertEqual(200, status, preflight)
        self.assertEqual("backlog:first", preflight["claims"][0]["claim_key"])

        status, conflict = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:second",
                "owner_id": "owner-b",
                "worker_id": "cloud::w2",
                "metadata": {
                    "loop": "self_improvement",
                    "conflict_scope": {
                        "capabilities": ["shared-feature"],
                        "files": ["tests/other.py"],
                    },
                },
            },
        )
        self.assertEqual(409, status, conflict)
        self.assertEqual("scope_conflict", conflict["blocked"])
        self.assertEqual("backlog:first", conflict["conflict"]["claim_key"])

    def test_api_selects_first_safe_self_improvement_alternative(self):
        status, _ = self.request("/api/worker-preflight", self.payload("owner-a", "cloud::w1"))
        self.assertEqual(200, status)
        status, blocker = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:blocker",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {
                    "loop": "self_improvement",
                    "task": "blocking task",
                    "conflict_scope": {"capabilities": ["shared"], "files": ["server.py"]},
                },
            },
        )
        self.assertEqual(200, status, blocker)

        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='cloud'")
        status, preflight = self.request("/api/worker-preflight", self.payload("owner-b", "cloud::w2"))
        self.assertEqual(200, status, preflight)
        self.assertEqual("backlog:blocker", preflight["claims"][0]["claim_key"])

        status, selected = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:primary",
                "owner_id": "owner-b",
                "worker_id": "cloud::w2",
                "metadata": {
                    "loop": "self_improvement",
                    "task": "primary task",
                    "conflict_scope": {"capabilities": ["shared"], "files": ["server.py"]},
                },
                "alternatives": [
                    {
                        "claim_key": "backlog:alt-conflict",
                        "metadata": {
                            "loop": "self_improvement",
                            "task": "conflicting fallback",
                            "conflict_scope": {"capabilities": ["shared"], "files": ["other.py"]},
                        },
                    },
                    {
                        "claim_key": "backlog:alt-free",
                        "metadata": {
                            "loop": "self_improvement",
                            "task": "safe fallback",
                            "conflict_scope": {"capabilities": ["independent"], "files": ["tests/free.py"]},
                        },
                    },
                ],
            },
        )
        self.assertEqual(200, status, selected)
        self.assertTrue(selected["acquired"])
        self.assertEqual("backlog:alt-free", selected["selected_claim_key"])
        self.assertEqual("alternative", selected["selected_from"])
        self.assertEqual(1, selected["selected_index"])

    def test_api_invalid_alternative_rejects_before_primary_claim(self):
        status, _ = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status)
        status, invalid = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:primary-free",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {
                    "loop": "self_improvement",
                    "task": "primary task",
                    "conflict_scope": {"capabilities": ["primary"], "files": ["server.py"]},
                },
                "alternatives": [
                    {
                        "claim_key": "backlog:bad-alt",
                        "metadata": {
                            "loop": "self_improvement",
                            "task": "missing scope",
                        },
                    }
                ],
            },
        )
        self.assertEqual(400, status, invalid)
        claims = server.task_claims("cloud")
        self.assertEqual([], claims)

    def test_api_all_conflicted_alternatives_return_no_safe_alternative(self):
        status, _ = self.request("/api/worker-preflight", self.payload("owner-a", "cloud::w1"))
        self.assertEqual(200, status)
        status, blocker = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:block-all",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
                "metadata": {
                    "loop": "self_improvement",
                    "task": "blocking task",
                    "conflict_scope": {"capabilities": ["shared"], "files": ["server.py"]},
                },
            },
        )
        self.assertEqual(200, status, blocker)

        with server.connect() as conn:
            conn.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='cloud'")
        status, _ = self.request("/api/worker-preflight", self.payload("owner-b", "cloud::w2"))
        self.assertEqual(200, status)
        status, blocked = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "backlog:primary-blocked",
                "owner_id": "owner-b",
                "worker_id": "cloud::w2",
                "metadata": {
                    "loop": "self_improvement",
                    "task": "primary blocked",
                    "conflict_scope": {"capabilities": ["shared"], "files": ["server.py"]},
                },
                "alternatives": [
                    {
                        "claim_key": "backlog:alt-blocked",
                        "metadata": {
                            "loop": "self_improvement",
                            "task": "also blocked",
                            "conflict_scope": {"capabilities": ["shared"], "files": ["elsewhere.py"]},
                        },
                    }
                ],
            },
        )
        self.assertEqual(409, status, blocked)
        self.assertEqual("no_safe_alternative", blocked["blocked"])
        self.assertEqual(2, len(blocked["attempted"]))

    def test_claim_landscape_change_between_api_preflight_and_admission_is_blocked_atomically(self):
        status, initial = self.request("/api/worker-preflight", self.payload("owner-a", "cloud::w1"))
        self.assertEqual(200, status, initial)
        self.assertTrue(initial["ok"])

        original = server.worker_preflight_state
        injected = {"done": False}

        def race_preflight(project_id, worker_id, owner_id):
            result = original(project_id, worker_id, owner_id)
            if owner_id == "owner-a" and result.get("ok") and not injected["done"]:
                injected["done"] = True
                other = server.task_claim_acquire(
                    "cloud",
                    "backlog:interloper",
                    "owner-b",
                    "cloud::w2",
                    120,
                    {
                        "conflict_scope": {
                            "capabilities": ["unrelated-capability"],
                            "files": ["unrelated.py"],
                        }
                    },
                )
                self.assertTrue(other["acquired"])
            return result

        server.worker_preflight_state = race_preflight
        try:
            status, blocked = self.request(
                "/api/task-claims",
                {
                    "action": "acquire",
                    "project_id": "cloud",
                    "claim_key": "backlog:primary-after-race",
                    "owner_id": "owner-a",
                    "worker_id": "cloud::w1",
                    "metadata": {
                        "loop": "self_improvement",
                        "task": "primary after race",
                        "conflict_scope": {
                            "capabilities": ["primary-capability"],
                            "files": ["server.py"],
                        },
                    },
                    "alternatives": [
                        {
                            "claim_key": "backlog:alternative-after-race",
                            "metadata": {
                                "loop": "self_improvement",
                                "task": "alternative after race",
                                "conflict_scope": {
                                    "capabilities": ["alternative-capability"],
                                    "files": ["tests/free.py"],
                                },
                            },
                        }
                    ],
                },
            )
        finally:
            server.worker_preflight_state = original

        self.assertTrue(injected["done"])
        self.assertEqual(428, status, blocked)
        self.assertEqual("claim_landscape_changed", blocked["blocked"])
        claims = server.task_claims("cloud")
        self.assertEqual(["backlog:interloper"], [x["claim_key"] for x in claims])

    def test_valid_preflight_allows_claim_and_exposes_read_only_status(self):
        status, preflight = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status, preflight)
        self.assertTrue(preflight["ok"])
        self.assertEqual([], preflight["claims"])
        self.assertTrue(preflight["vps"]["ok"])

        status, claim = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "notion:allowed",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
            },
        )
        self.assertEqual(200, status, claim)
        self.assertTrue(claim["acquired"])

        status, state = self.request(
            "/api/worker-preflight?project=cloud&worker=cloud::w1&owner=owner-a"
        )
        self.assertEqual(200, status, state)
        self.assertTrue(state["ok"], state)

    def test_other_owner_claim_change_invalidates_receipt(self):
        status, preflight = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status, preflight)
        server.task_claim_acquire(
            "cloud", "notion:parallel", "owner-b", "cloud::w2", 120
        )
        status, blocked = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "notion:mine",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
            },
        )
        self.assertEqual(428, status, blocked)
        self.assertEqual("claim_landscape_changed", blocked["blocked"])

    def test_own_claim_does_not_invalidate_same_owner_preflight(self):
        status, _ = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status)
        for key in ("notion:first", "notion:second"):
            status, claim = self.request(
                "/api/task-claims",
                {
                    "action": "acquire",
                    "project_id": "cloud",
                    "claim_key": key,
                    "owner_id": "owner-a",
                    "worker_id": "cloud::w1",
                },
            )
            self.assertEqual(200, status, claim)
            self.assertTrue(claim["acquired"])

    def test_expired_preflight_blocks_claim(self):
        status, _ = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(200, status)
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with server.connect() as conn:
            conn.execute(
                "UPDATE worker_preflights SET expires_at=? WHERE project_id=? AND worker_id=? AND owner_id=?",
                (expired, "cloud", "cloud::w1", "owner-a"),
            )
        status, blocked = self.request(
            "/api/task-claims",
            {
                "action": "acquire",
                "project_id": "cloud",
                "claim_key": "notion:expired",
                "owner_id": "owner-a",
                "worker_id": "cloud::w1",
            },
        )
        self.assertEqual(428, status, blocked)
        self.assertEqual("preflight_expired", blocked["blocked"])

    def test_unhealthy_vps_and_incomplete_external_evidence_do_not_create_receipt(self):
        server.coordination_vps_health = lambda: {
            "ok": False,
            "checks": {"zcloud_service": True, "firefox_automation": False, "state_store": True},
        }
        status, blocked = self.request("/api/worker-preflight", self.payload())
        self.assertEqual(409, status, blocked)
        self.assertEqual("vps_unhealthy", blocked["blocked"])

        server.coordination_vps_health = self.original_vps_health
        bad = self.payload()
        bad["github"]["open_prs"] = "not-a-list"
        status, invalid = self.request("/api/worker-preflight", bad)
        self.assertEqual(400, status, invalid)
        self.assertIn("open_prs", invalid["error"])

        with server.connect() as conn:
            count = conn.execute("SELECT COUNT(*) n FROM worker_preflights").fetchone()["n"]
        self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
