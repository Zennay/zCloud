"""Synthetic negative contract for trusted, fresh, read-only V2 live proof."""
from datetime import datetime, timedelta, timezone
from email.message import Message
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import zcloud_v2_live_proof_freshness_20261013_w1 as m

NOW = datetime(2026, 10, 13, 14, 25, tzinfo=timezone.utc)
WORKER = ("zcloud-vps-1", 1)
PATH = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-v2-live-proof.yml"


def sample():
    return {"chatgpt_runners": {"zcloud-vps-1": {"workers": [
        {"active": True, "worker_id": "zcloud-vps-1::w1", "worker_slot": 1},
    ]}}}


def receipt(age=20, reason=m.REASON, generating=0, sending=0):
    return ((NOW - timedelta(seconds=age)).isoformat(), reason, generating, sending)


class FakeReply:
    def __init__(self, payload, status=200, mime="application/json"):
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = mime
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n):
        return self.body[:n]


class FakeOpener:
    def __init__(self, value):
        self.value = value

    def open(self, request, timeout):
        assert request.full_url == m.API
        assert timeout == 5
        return self.value


class LiveProofTests(unittest.TestCase):
    def test_fresh_exact_worker_receipt_accepts_without_release_authority(self):
        result = m.verify_receipts((WORKER,), {WORKER: receipt()}, NOW)
        self.assertEqual(result, {"active_workers": 1, "recent_v2_heartbeats": 1})

    def test_two_active_workers_require_independent_rows(self):
        data = sample()
        data["chatgpt_runners"]["zcloud-vps-1"]["workers"].append(
            {"active": True, "worker_id": "zcloud-vps-1::w2", "worker_slot": 2}
        )
        selected = m.active_workers(data)
        self.assertEqual(len(selected), 2)
        with self.assertRaises(m.ProofDenied):
            m.verify_receipts(selected, {WORKER: receipt()}, NOW)
        self.assertEqual(
            m.verify_receipts(selected, {WORKER: receipt(),
                                        ("zcloud-vps-1", 2): receipt()}, NOW)["recent_v2_heartbeats"], 2
        )

    def test_missing_or_untyped_runner_inventory_denied(self):
        for bad in (None, [], {}, {"chatgpt_runners": None},
                    {"chatgpt_runners": {}},
                    {"chatgpt_runners": {"runner": None}},
                    {"chatgpt_runners": {"runner": {"workers": None}}},
                    {"chatgpt_runners": {"runner": {"workers": [None]}}}):
            with self.subTest(bad=bad):
                with self.assertRaises(m.ProofDenied):
                    m.active_workers(bad)

    def test_no_active_worker_denied(self):
        p = sample()
        p["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["active"] = False
        with self.assertRaises(m.ProofDenied):
            m.active_workers(p)

    def test_duplicate_id_denied(self):
        p = sample()
        p["chatgpt_runners"]["zcloud-vps-1"]["workers"] *= 2
        with self.assertRaises(m.ProofDenied):
            m.active_workers(p)

    def test_active_type_unknown_denied(self):
        for x in (None, 0, 1, "true", []):
            p = sample()
            p["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["active"] = x
            with self.subTest(x=x), self.assertRaises(m.ProofDenied):
                m.active_workers(p)

    def test_slot_identity_mismatch_and_missing_slot_denied(self):
        for x in (None, True, "1", 0, 2, 129):
            p = sample()
            p["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["worker_slot"] = x
            with self.subTest(x=x), self.assertRaises(m.ProofDenied):
                m.active_workers(p)

    def test_invalid_worker_ids_denied(self):
        for x in ("", "w1", "zcloud-vps-1::w0", "zcloud-vps-1::w01",
                  "zcloud-vps-1::w2", 17, "zcloud-vps-1::w1\n"):
            p = sample()
            p["chatgpt_runners"]["zcloud-vps-1"]["workers"][0]["worker_id"] = x
            with self.subTest(x=x), self.assertRaises(m.ProofDenied):
                m.active_workers(p)

    def test_bounded_active_count(self):
        p = sample()
        p["chatgpt_runners"]["zcloud-vps-1"]["workers"] = [
            {"active": True, "worker_slot": n, "worker_id": f"zcloud-vps-1::w{n}"}
            for n in range(1, m.MAX_ACTIVE + 2)
        ]
        with self.assertRaises(m.ProofDenied):
            m.active_workers(p)

    def test_stale_and_future_heartbeat_denied(self):
        for age in (91, 1000, -11, -1000):
            with self.subTest(age=age), self.assertRaises(m.ProofDenied):
                m.verify_receipts((WORKER,), {WORKER: receipt(age)}, NOW)

    def test_recent_within_bounds(self):
        for age in (0, 20, 89, 90, -10):
            with self.subTest(age=age):
                self.assertEqual(
                    m.verify_receipts((WORKER,), {WORKER: receipt(age)}, NOW)["recent_v2_heartbeats"], 1
                )

    def test_unsafe_timestamp_denied(self):
        for ts in (None, "n/a", "", "2026-10-13T14:25:00",
                   "2026-10-13", 123, "a" * 90):
            with self.subTest(ts=ts), self.assertRaises(m.ProofDenied):
                m.verify_receipts((WORKER,), {WORKER: (ts, m.REASON, 0, 0)}, NOW)

    def test_latest_wrong_reason_denied(self):
        for reason in ("legacy", None, "", "worker-v2-check-30s "):
            with self.subTest(reason=reason), self.assertRaises(m.ProofDenied):
                m.verify_receipts((WORKER,), {WORKER: receipt(reason=reason)}, NOW)

    def test_flags_cannot_be_malformed(self):
        for flag in ("generating", "sending"):
            for bad in (True, False, None, "0", -1, 2):
                row = list(receipt())
                row[2 if flag == "generating" else 3] = bad
                with self.subTest(flag=flag, bad=bad), self.assertRaises(m.ProofDenied):
                    m.verify_receipts((WORKER,), {WORKER: tuple(row)}, NOW)

    def test_payload_missing_rows_and_malformed_denied(self):
        for rows in ({}, {WORKER: None}, {WORKER: []}, {("other", 1): receipt()}):
            with self.subTest(rows=rows), self.assertRaises(m.ProofDenied):
                m.verify_receipts((WORKER,), rows, NOW)

    def test_strict_duplicate_json_fields(self):
        with self.assertRaises(m.ProofDenied):
            json.loads('{"a":1,"a":2}', object_pairs_hook=m.unique_json)

    def test_no_remote_redirect(self):
        with self.assertRaises(m.ProofDenied):
            m.NoRedirect().redirect_request(None, None, 302, "", {}, "http://external/")

    def test_local_http_valid(self):
        self.assertEqual(m.live_status(FakeOpener(FakeReply(sample()))), (WORKER,))

    def test_http_invalid_status_mime_size_denied(self):
        for response in (
            FakeReply(sample(), status=503),
            FakeReply(sample(), mime="text/html"),
            FakeReply(b"x" * (m.MAX_BYTES + 1)),
            FakeReply(b'{"chatgpt_runners":1,"chatgpt_runners":2}'),
            FakeReply(b'{"chatgpt_runners":'),
        ):
            with self.subTest(status=response.status, n=len(response.body)), self.assertRaises(
                (m.ProofDenied, ValueError)
            ):
                m.live_status(FakeOpener(response))

    def test_sqlite_fresh_latest_id_not_old_matching_reason(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "history.db"
            with sqlite3.connect(db) as con:
                con.execute(
                    "CREATE TABLE runner_events (id INTEGER PRIMARY KEY, ts TEXT, reason TEXT, "
                    "generating INTEGER, sending INTEGER, project_id TEXT, worker_slot INTEGER, event TEXT)"
                )
                con.executemany(
                    "INSERT INTO runner_events VALUES (?,?,?,?,?,?,?,?)",
                    [(1, receipt()[0], m.REASON, 0, 0, WORKER[0], 1, "heartbeat"),
                     (2, receipt()[0], "legacy", 0, 0, WORKER[0], 1, "heartbeat")],
                )
            rows = m.latest_sqlite_receipts((WORKER,), db)
            self.assertEqual(rows[WORKER][1], "legacy")
            with self.assertRaises(m.ProofDenied):
                m.verify_receipts((WORKER,), rows, NOW)

    def test_sqlite_missing_and_symlink_denied(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "absent"
            with self.assertRaises(m.ProofDenied):
                m.latest_sqlite_receipts((WORKER,), missing)
            real = Path(d) / "real"
            real.write_text("")
            link = Path(d) / "alias"
            link.symlink_to(real)
            with self.assertRaises(m.ProofDenied):
                m.latest_sqlite_receipts((WORKER,), link)


class HostedTrustWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PATH.read_text()

    def test_no_automatic_push_or_privileged_pr(self):
        events = self.source.split("\npermissions:", 1)[0]
        self.assertNotIn("\n  push:\n", events)
        self.assertNotIn("\n  workflow_run:\n", events)
        self.assertIn("\n  pull_request:\n", events)
        self.assertIn("\n  workflow_dispatch:\n", events)
        self.assertIn("confirm_sha:", events)
        self.assertEqual(self.source.count("runs-on: [self-hosted, zcloud, vps]"), 1)

    def test_hosted_vs_vps_job_disjoint(self):
        val = self.source.split("  validate:\n", 1)[1].split("\n  prove:\n", 1)[0]
        prove = self.source.split("  prove:\n", 1)[1]
        self.assertIn("github.event_name == 'pull_request'", val)
        self.assertIn("runs-on: ubuntu-latest", val)
        self.assertNotIn("self-hosted", val)
        self.assertIn("github.event_name == 'workflow_dispatch'", prove)
        self.assertIn("github.ref == 'refs/heads/main'", prove)
        self.assertIn("github.actor == 'Zennay'", prove)

    def test_immutable_checkout_guard_and_secrets(self):
        self.assertEqual(self.source.count(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2)
        self.assertEqual(self.source.count("persist-credentials: false"), 2)
        self.assertIn("permissions:\n  contents: read", self.source)
        prove = self.source.split("  prove:\n", 1)[1]
        self.assertLess(prove.index("Trust preflight before reading live worker/SQLite state"),
                        prove.index("Check live worker heartbeats on read-only SQLite and localhost"))
        for expected in ("CONFIRMED_SHA:", 'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
                         "git ls-remote --exit-code origin refs/heads/main",
                         "python3 scripts/zcloud_vps_runner_guard.py --json"):
            self.assertIn(expected, prove)

    def test_no_live_mutation_in_workflow(self):
        prove = self.source.split("  prove:\n", 1)[1]
        for unsafe in ("sudo ", "systemctl restart", "systemctl stop", "rm -rf",
                       "node scripts/zcloud_reload_extension.mjs",
                       "sqlite3 INSERT", "tabs.remove(", "kill -9"):
            self.assertNotIn(unsafe, prove)
        self.assertIn("scripts/zcloud_v2_live_proof_freshness_20261013_w1.py", prove)


if __name__ == "__main__":
    unittest.main()
