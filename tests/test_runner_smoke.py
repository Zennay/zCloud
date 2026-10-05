import json
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
import sys
import types
from http.server import ThreadingHTTPServer
from pathlib import Path

# Runner-control smoke tests deliberately isolate unrelated VPS telemetry/resource helpers.
# The production enhancements module has host-specific import side effects, so use the
# smallest stub needed by server.init_db() in this portable test process.
enhancements_stub = types.ModuleType("enhancements")
enhancements_stub.init_db = lambda conn: None
enhancements_stub.load_resource_policy = lambda: {}
sys.modules["enhancements"] = enhancements_stub

import server


class RunnerSmokeTests(unittest.TestCase):
    """Critical runner flows against an isolated temporary zCloud state DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-smoke-")
        self.original_db = server.DB
        # These tests intentionally simulate the legacy multi-worker pool; production remains one slot.
        self.original_global_limit = server.GLOBAL_CHATGPT_WORKER_LIMIT
        self.original_max_workers = server.MAX_CHATGPT_WORKERS
        self.original_worker_memory_status = server.worker_memory_status
        server.worker_memory_status = lambda *args, **kwargs: {
            "available_mb": 16384, "total_mb": 32768,
            "swap_total_mb": 4096, "swap_free_mb": 4096,
            "headroom_mb": 2048, "effective_headroom_mb": 2048,
            "per_new_slot_mb": 1536, "new_worker_capacity": 8,
            "pressure": "ok", "healthy_for_new_worker": True, "swap_healthy": True,
        }
        server.GLOBAL_CHATGPT_WORKER_LIMIT = 2
        server.MAX_CHATGPT_WORKERS = 2
        self.original_cache = server.CACHE
        self.original_layout_file = server.LAYOUT_FILE
        server.DB = Path(self.tmp.name) / "history.db"
        server.LAYOUT_FILE = Path(self.tmp.name) / "project-layout.json"
        server.CACHE = None
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
        server.worker_memory_status = self.original_worker_memory_status
        server.DB = self.original_db
        server.CACHE = self.original_cache
        server.LAYOUT_FILE = self.original_layout_file
        self.tmp.cleanup()

    def request(self, path, payload=None, extra_headers=None):
        data = None
        headers = dict(extra_headers or {})
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

    def active(self, project_id="cloud"):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT active FROM runner_targets WHERE project_id=?", (project_id,)
            ).fetchone()
        return bool(row["active"])

    def test_cold_start_creates_runner_state(self):
        with server.connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            cloud = conn.execute(
                "SELECT project_id, active, worker_count FROM runner_targets WHERE project_id='cloud'"
            ).fetchone()
        self.assertIn("runner_targets", tables)
        self.assertIn("runner_workers", tables)
        self.assertIsNotNone(cloud)
        self.assertFalse(bool(cloud["active"]))
        self.assertGreaterEqual(int(cloud["worker_count"]), 1)

    def test_start_push_pause_flow(self):
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        self.assertTrue(self.active())

        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(200, status, body)
        self.assertEqual("pending", body["status"])

        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "pause"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        self.assertFalse(self.active())

    def test_push_requires_started_project(self):
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(409, status)
        self.assertIn("Start dit project eerst", body["error"])

    def test_repeated_start_reuses_pending_command(self):
        first_status, first = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        second_status, second = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, first_status, first)
        self.assertEqual(200, second_status, second)
        self.assertEqual(first["command_id"], second["command_id"])
        self.assertTrue(second.get("deduplicated"))

    def test_recently_completed_start_is_rate_limited(self):
        first_status, first = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, first_status, first)
        result_status, result = self.request(
            "/api/runner-command-result",
            {"command_id": first["command_id"], "status": "completed", "result": "started"},
        )
        self.assertEqual(200, result_status, result)
        second_status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(429, second_status)
        self.assertIn("net al een actie", body["error"])

    def test_stale_pending_retry_supersedes_global_old_commands_before_dedupe(self):
        self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        first_status, first = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(200, first_status, first)
        with server.connect() as conn:
            conn.execute(
                "UPDATE runner_commands SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (first["command_id"],),
            )
            stale_other = conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                ("haxlab", "start", "pending", "2000-01-01T00:00:00+00:00", server.now()),
            ).lastrowid
            fresh_ts = server.now()
            fresh_other = conn.execute(
                "INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                ("supa", "start", "pending", fresh_ts, fresh_ts),
            ).lastrowid

        second_status, second = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(200, second_status, second)
        self.assertNotEqual(first["command_id"], second["command_id"])
        self.assertFalse(second.get("deduplicated"))
        with server.connect() as conn:
            old = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?",
                (first["command_id"],),
            ).fetchone()
            unrelated_old = conn.execute(
                "SELECT status,result FROM runner_commands WHERE id=?",
                (stale_other,),
            ).fetchone()
            unrelated_fresh = conn.execute(
                "SELECT status FROM runner_commands WHERE id=?",
                (fresh_other,),
            ).fetchone()
            pending = conn.execute(
                "SELECT id FROM runner_commands "
                "WHERE project_id='cloud' AND action='push' AND status='pending'"
            ).fetchall()
        self.assertEqual("failed", old["status"])
        self.assertIn("superseded stale pending command", old["result"])
        self.assertEqual("failed", unrelated_old["status"])
        self.assertIn("superseded stale pending command", unrelated_old["result"])
        self.assertEqual("pending", unrelated_fresh["status"])
        self.assertEqual([second["command_id"]], [row["id"] for row in pending])

    def test_concurrent_duplicate_push_creates_one_pending_command(self):
        self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def send_push():
            try:
                barrier.wait(timeout=3)
                results.append(
                    self.request(
                        "/api/runner-control",
                        {"project_id": "cloud", "action": "push"},
                    )
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=send_push) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(errors, errors)
        self.assertEqual(2, len(results))
        self.assertEqual([200, 200], sorted(status for status, _ in results))
        command_ids = {body["command_id"] for _, body in results}
        self.assertEqual(1, len(command_ids), results)
        self.assertTrue(any(body.get("deduplicated") for _, body in results))
        with server.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) n FROM runner_commands "
                "WHERE project_id='cloud' AND action='push' AND status='pending'"
            ).fetchone()["n"]
        self.assertEqual(1, count)

    def test_new_intent_after_opposite_pending_action_is_not_deduplicated(self):
        first_status, first = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        pause_status, pause = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "pause"}
        )
        second_status, second = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "start"}
        )
        self.assertEqual(200, first_status, first)
        self.assertEqual(200, pause_status, pause)
        self.assertEqual(200, second_status, second)
        self.assertNotEqual(first["command_id"], second["command_id"])
        self.assertFalse(second.get("deduplicated"))
        self.assertGreater(second["command_id"], pause["command_id"])

    def test_config_audit_worker_count_records_actor_and_noop(self):
        status, body = self.request(
            "/api/runner-workers",
            {"project_id": "cloud", "worker_count": 2},
            {"X-ZCloud-Actor": "dashboard-test"},
        )
        self.assertEqual(200, status, body)
        status, audit = self.request(
            "/api/config-audit?key=runner.worker_count&target=cloud"
        )
        self.assertEqual(200, status, audit)
        item = audit["items"][0]
        self.assertEqual("runner.worker_count", item["config_key"])
        self.assertEqual("cloud", item["target"])
        self.assertEqual(1, item["old_value"])
        self.assertEqual(2, item["new_value"])
        self.assertEqual("succeeded", item["result"])
        self.assertTrue(item["actor"].startswith("dashboard-test@"))

        status, body = self.request(
            "/api/runner-workers",
            {"project_id": "cloud", "worker_count": 2},
            {"X-ZCloud-Actor": "dashboard-test"},
        )
        self.assertEqual(200, status, body)
        _, audit = self.request(
            "/api/config-audit?key=runner.worker_count&target=cloud"
        )
        self.assertEqual("no_change", audit["items"][0]["result"])
        self.assertEqual(2, audit["items"][0]["old_value"])
        self.assertEqual(2, audit["items"][0]["new_value"])

    def test_config_audit_resource_priority_success_and_rejection(self):
        policy = {"cloud": {"priority": "normal"}}

        def load_policy():
            return {key: dict(value) for key, value in policy.items()}

        def set_priority(project, priority):
            if priority not in ("normal", "high"):
                raise ValueError("Ongeldige prioriteit")
            old = policy.setdefault(project, {"priority": "normal"})
            old["priority"] = priority
            return {"project": project, "priority": priority, "weight": 1}

        server.enhancements.load_resource_policy = load_policy
        server.enhancements.set_priority = set_priority
        server.CACHE = {
            "projects": [
                {"id": "cloud", "resource": {"priority": "normal", "weight": 400}},
            ]
        }

        status, body = self.request(
            "/api/resource-priority",
            {"project": "cloud", "priority": "high"},
            {"X-ZCloud-Actor": "resource-test"},
        )
        self.assertEqual(200, status, body)
        self.assertEqual("high", server.CACHE["projects"][0]["resource"]["priority"])
        self.assertEqual(1, server.CACHE["projects"][0]["resource"]["weight"])
        _, audit = self.request(
            "/api/config-audit?key=resource.priority&target=cloud"
        )
        self.assertEqual("normal", audit["items"][0]["old_value"])
        self.assertEqual("high", audit["items"][0]["new_value"])
        self.assertEqual("succeeded", audit["items"][0]["result"])

        status, body = self.request(
            "/api/resource-priority",
            {"project": "cloud", "priority": "warp"},
            {"X-ZCloud-Actor": "resource-test"},
        )
        self.assertEqual(400, status, body)
        _, audit = self.request(
            "/api/config-audit?key=resource.priority&target=cloud"
        )
        self.assertEqual("rejected", audit["items"][0]["result"])
        self.assertEqual("high", audit["items"][0]["old_value"])
        self.assertEqual("warp", audit["items"][0]["new_value"])
        self.assertIn("Ongeldige", audit["items"][0]["detail"])

    def test_config_audit_project_layout_and_filters(self):
        server.CACHE = {
            "projects": [
                {"id": "cloud", "name": "zCloud"},
                {"id": "ftmo", "name": "FTMO"},
            ]
        }
        status, body = self.request(
            "/api/project-layout",
            {"order": ["ftmo", "cloud"], "archived": ["ftmo"]},
            {"X-ZCloud-Actor": "layout-test"},
        )
        self.assertEqual(200, status, body)
        status, audit = self.request(
            "/api/config-audit?key=project.layout&target=portfolio&limit=1"
        )
        self.assertEqual(200, status, audit)
        self.assertEqual(1, len(audit["items"]))
        item = audit["items"][0]
        self.assertEqual("project.layout", item["config_key"])
        self.assertEqual("portfolio", item["target"])
        self.assertEqual(
            {"order": ["cloud", "ftmo"], "archived": []},
            item["old_value"],
        )
        self.assertEqual(
            {"order": ["ftmo", "cloud"], "archived": ["ftmo"]},
            item["new_value"],
        )
        self.assertEqual("succeeded", item["result"])
        self.assertTrue(item["actor"].startswith("layout-test@"))

    def test_config_audit_table_exists_on_cold_start(self):
        with server.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='config_audit'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_high_blast_feature_flag_defaults_off_and_is_ttl_bounded(self):
        status, flags = self.request("/api/feature-flags")
        self.assertEqual(200, status, flags)
        flag = next(item for item in flags["items"] if item["name"] == "high_blast_radius_promotion")
        self.assertFalse(flag["effective"])
        self.assertFalse(flag["enabled"])

        status, body = self.request(
            "/api/feature-flags",
            {
                "name": "high_blast_radius_promotion",
                "enabled": True,
                "ttl_seconds": 120,
            },
            {"X-ZCloud-Actor": "flag-test"},
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["feature_flag"]["effective"])
        self.assertIsNotNone(body["feature_flag"]["expires_at"])
        self.assertTrue(body["feature_flag"]["actor"].startswith("flag-test@"))

        _, audit = self.request(
            "/api/config-audit?key=feature.flag&target=high_blast_radius_promotion&limit=1"
        )
        self.assertEqual("succeeded", audit["items"][0]["result"])
        self.assertFalse(audit["items"][0]["old_value"]["enabled"])
        self.assertTrue(audit["items"][0]["new_value"]["enabled"])

        status, body = self.request(
            "/api/feature-flags",
            {"name": "high_blast_radius_promotion", "enabled": False},
            {"X-ZCloud-Actor": "flag-test"},
        )
        self.assertEqual(200, status, body)
        self.assertFalse(body["feature_flag"]["effective"])
        self.assertIsNone(body["feature_flag"]["expires_at"])

    def test_high_blast_feature_flag_rejects_unsafe_ttl_and_unknown_flag(self):
        status, body = self.request(
            "/api/feature-flags",
            {
                "name": "high_blast_radius_promotion",
                "enabled": True,
                "ttl_seconds": 7200,
            },
        )
        self.assertEqual(400, status, body)
        self.assertIn("ttl_seconds", body["error"])

        status, body = self.request(
            "/api/feature-flags",
            {"name": "unknown_flag", "enabled": True, "ttl_seconds": 120},
        )
        self.assertEqual(400, status, body)
        self.assertIn("Onbekende", body["error"])

    def test_runner_live_is_available_before_telemetry_cache(self):
        self.assertIsNone(server.CACHE)
        status, body = self.request("/api/runner-live")
        self.assertEqual(200, status, body)
        self.assertIn("chatgpt_runners", body)
        self.assertIn("chatgpt_firefox", body)
        self.assertIn("cloud", body["chatgpt_runners"])

    def test_runner_statuses_reuses_one_coherent_target_snapshot(self):
        original_targets = server.runner_targets
        original_worker_targets = server.runner_worker_targets
        with mock.patch.object(server, "runner_targets", wraps=original_targets) as target_mock, \
             mock.patch.object(server, "runner_worker_targets", wraps=original_worker_targets) as worker_target_mock:
            statuses = server.runner_statuses()

        self.assertIn("cloud", statuses)
        self.assertEqual(1, target_mock.call_count)
        self.assertEqual(1, worker_target_mock.call_count)
        self.assertIsNotNone(worker_target_mock.call_args.kwargs.get("base"))

    def test_worker_count_change_updates_worker_targets(self):
        status, body = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": 2}
        )
        self.assertEqual(200, status, body)
        self.assertEqual(2, body["worker_count"])

        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        cloud_workers = sorted(
            key for key in targets["projects"] if key.startswith("cloud::w")
        )
        self.assertEqual(["cloud::w1", "cloud::w2"], cloud_workers)
        self.assertTrue(all(targets["projects"][key]["worker_count"] == 2 for key in cloud_workers))
        self.assertEqual(3, targets["max_workers"])

    def test_worker_count_rejects_unsafe_value(self):
        status, body = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": server.MAX_CHATGPT_WORKERS + 1}
        )
        self.assertEqual(400, status)
        self.assertIn("ChatGPT-tabs", body["error"])

    def test_reconnect_adopts_and_persists_conversation(self):
        status, _ = self.request(
            "/api/runner-workers", {"project_id": "cloud", "worker_count": 2}
        )
        self.assertEqual(200, status)
        conversation_id = "12345678-1234-1234-1234-123456789abc"
        status, body = self.request(
            "/api/runner-status",
            {
                "event": "conversation-adopted",
                "projectId": "cloud::w2",
                "baseProjectId": "cloud",
                "workerSlot": 2,
                "target": f"https://chatgpt.com/c/{conversation_id}",
                "title": "zCloud · worker 2/2",
            },
        )
        self.assertEqual(200, status, body)

        # Model a zCloud/service restart: schema/bootstrap must preserve the adopted mapping.
        server.init_db()
        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        self.assertEqual(conversation_id, targets["projects"]["cloud::w2"]["conversation_id"])
        self.assertEqual(
            f"https://chatgpt.com/c/{conversation_id}",
            targets["projects"]["cloud::w2"]["url"],
        )

    def test_worker_start_auto_activates_parent_project(self):
        self.assertFalse(self.active("cloud"))
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud::w1", "action": "start"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(body["ok"])
        self.assertTrue(self.active("cloud"))
        with server.connect() as conn:
            desired = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=1"
            ).fetchone()["desired_state"]
            reason = conn.execute(
                "SELECT last_reason FROM autonomy_runtime WHERE project_id='cloud'"
            ).fetchone()["last_reason"]
        self.assertEqual("running", desired)
        self.assertEqual("worker_auto_resume", reason)

    def test_worker_pause_is_individual_and_persistent(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "pause"}
        )
        self.assertEqual(200, status, body)
        self.assertTrue(self.active("cloud"))
        with server.connect() as conn:
            row = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()
        self.assertEqual("paused", row["desired_state"])
        status, targets = self.request("/api/runner-targets")
        self.assertEqual(200, status)
        self.assertTrue(targets["projects"]["cloud::w1"]["active"])
        self.assertFalse(targets["projects"]["cloud::w2"]["active"])
        server.init_db()
        with server.connect() as conn:
            row = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()
        self.assertEqual("paused", row["desired_state"])

    def test_worker_drain_finishes_into_paused_state(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "drain"}
        )
        self.assertEqual(200, status, body)
        self.assertEqual("draining", body["desired_state"])
        status, targets = self.request("/api/runner-targets")
        self.assertTrue(targets["projects"]["cloud::w2"]["active"])
        self.assertEqual("draining", targets["projects"]["cloud::w2"]["desired_state"])
        status, body = self.request(
            "/api/runner-status",
            {"event": "runner-drained", "projectId": "cloud::w2",
             "baseProjectId": "cloud", "workerSlot": 2, "title": "zCloud · worker 2/2"},
        )
        self.assertEqual(200, status, body)
        with server.connect() as conn:
            desired = conn.execute(
                "SELECT desired_state FROM runner_workers WHERE project_id='cloud' AND worker_slot=2"
            ).fetchone()["desired_state"]
        self.assertEqual("paused", desired)

    def test_project_push_rejects_when_all_workers_are_paused(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        self.request("/api/runner-workers", {"project_id": "cloud", "worker_count": 2})
        first, _ = self.request(
            "/api/runner-control", {"project_id": "cloud::w1", "action": "pause"}
        )
        second, _ = self.request(
            "/api/runner-control", {"project_id": "cloud::w2", "action": "pause"}
        )
        self.assertEqual((200, 200), (first, second))
        status, body = self.request(
            "/api/runner-control", {"project_id": "cloud", "action": "push"}
        )
        self.assertEqual(409, status)
        self.assertIn("Geen actieve workers", body["error"])

    def test_worker_status_exposes_task_claim_and_advanced_metadata(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        server.task_claim_acquire(
            "cloud", "notion:abc", "owner-a", "cloud::w1", 300,
            {"task": "Veilige workerkaart bouwen", "branch": "worker/test"},
        )
        self.request(
            "/api/runner-status",
            {"event": "heartbeat", "projectId": "cloud::w1",
             "baseProjectId": "cloud", "workerSlot": 1, "title": "zCloud · worker 1/1"},
        )
        cloud = server.runner_statuses()["cloud"]
        self.assertEqual(1, cloud["desired_worker_count"])
        self.assertEqual(1, cloud["active_worker_count"])
        worker = cloud["workers"][0]
        self.assertEqual("cloud::w1", worker["worker_id"])
        self.assertTrue(worker["work_area"])
        self.assertEqual("Veilige workerkaart bouwen", worker["current_task"]["title"])
        self.assertEqual("worker/test", worker["current_task"]["branch"])

    def test_runner_targets_snapshot_never_cross_wires_queue_identity_during_slot_reassignment(self):
        with server.connect() as conn:
            conn.execute("DELETE FROM portfolio_queue")
            conn.execute("DELETE FROM ai_global_slots")

        cloud = server.portfolio_queue_enqueue(
            "cloud",
            "Fix atomic runner target binding",
            "P0",
            "Bind a worker to the exact queue identity from one allocation snapshot.",
            queue_id="cloud-atomic-snapshot",
        )
        raiseai = server.portfolio_queue_enqueue(
            "raiseai",
            "Implement bounded smart-home route",
            "P1",
            "Implement the next scoped smart-home connector increment.",
            queue_id="raiseai-slot-replacement",
        )
        ts = server.now()
        with server.connect() as conn:
            conn.execute(
                """UPDATE portfolio_queue
                   SET status='claimed',worker_slot=1,claimed_at=?,claim_expires='2999-01-01T00:00:00+00:00',updated_at=?
                   WHERE queue_id=?""",
                (ts, ts, cloud["queue_id"]),
            )
            conn.execute(
                """INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at)
                   VALUES(1,'cloud',1,?)""",
                (ts,),
            )

        snapshot = server.portfolio_queue_allocation()
        self.assertEqual("cloud::w1", snapshot["workers"][0]["worker_key"])
        self.assertEqual(cloud["queue_id"], snapshot["workers"][0]["queue_id"])

        original_allocation = server.global_worker_allocation

        def race_after_snapshot(*args, **kwargs):
            # Reproduce the production race: the public allocation snapshot still
            # says slot 1 belongs to Cloud while SQLite has already reassigned that
            # numeric slot to a different project's queue item.
            with server.connect() as conn:
                changed = server.now()
                conn.execute(
                    """UPDATE portfolio_queue
                       SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                       WHERE queue_id=?""",
                    (changed, cloud["queue_id"]),
                )
                conn.execute(
                    """UPDATE portfolio_queue
                       SET status='claimed',worker_slot=1,claimed_at=?,claim_expires='2999-01-01T00:00:00+00:00',updated_at=?
                       WHERE queue_id=?""",
                    (changed, changed, raiseai["queue_id"]),
                )
            return snapshot

        with mock.patch.object(server, "global_worker_allocation", side_effect=race_after_snapshot):
            status, targets = self.request("/api/runner-targets")

        self.assertEqual(200, status)
        self.assertEqual(cloud["queue_id"], targets["global_allocation"]["workers"][0]["queue_id"])
        worker = targets["projects"]["cloud::w1"]
        self.assertEqual(cloud["queue_id"], worker["queue_item"]["queue_id"])
        self.assertEqual("cloud", worker["queue_item"]["project_id"])
        self.assertNotEqual(raiseai["queue_id"], worker["queue_item"]["queue_id"])
        self.assertFalse(worker["assignment_ready"])

        # Sanity-check that the live numeric slot really did move to Raise AI;
        # the target stayed coherent because it followed queue identity, not slot.
        current = server.portfolio_queue_current_for_slot(1)
        self.assertEqual(raiseai["queue_id"], current["queue_id"])

    def test_worker_status_exposes_queue_generated_execution_lane(self):
        self.request("/api/runner-control", {"project_id": "cloud", "action": "start"})
        with server.connect() as conn:
            conn.execute("DELETE FROM portfolio_queue")
            conn.execute("DELETE FROM ai_global_slots")
        server.portfolio_queue_enqueue(
            "cloud",
            "Implement automatic queue lane scheduling",
            "P1",
            "Implement queue scheduler allocation with deterministic regression tests.",
        )
        server.portfolio_queue_allocate()
        server._persist_global_worker_allocation(server.portfolio_queue_allocation())
        self.request(
            "/api/runner-status",
            {"event": "heartbeat", "projectId": "cloud::w1",
             "baseProjectId": "cloud", "workerSlot": 1, "title": "zCloud · worker 1/1"},
        )

        worker = server.runner_statuses()["cloud"]["workers"][0]

        self.assertEqual("control-plane", worker["work_area"])
        self.assertEqual("control-plane", worker["execution_lane"]["lane_id"])
        self.assertIn("zcloud-queue", worker["execution_lane"]["scope"]["capabilities"])
        self.assertEqual("Implement automatic queue lane scheduling", worker["current_task"]["title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
