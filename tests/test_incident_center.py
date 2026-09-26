import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

MODULE_PATH=Path(__file__).resolve().parents[1]/"enhancements.py"
SPEC=importlib.util.spec_from_file_location("zcloud_incident_enhancements",MODULE_PATH)
enhancements=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enhancements)


class IncidentCenterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="zcloud-incidents-")
        self.root=Path(self.tmp.name)
        self.db=self.root/"history.db"
        with sqlite3.connect(self.db) as c:
            enhancements.init_db(c)
            c.execute("""CREATE TABLE task_claims(
                project_id TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                worker_id TEXT NOT NULL DEFAULT '',
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                lease_until TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(project_id,claim_key)
            )""")
        self.recovery=self.root/"recovery"
        self.recovery.mkdir()
        (self.recovery/"last-known-good.json").write_text(json.dumps({
            "snapshot_id":"snap-green","updated_at":"2026-09-26T22:00:00+00:00"
        }))
        (self.recovery/"recovery.log").write_text(
            json.dumps({"event":"rollback_succeeded","time":"2026-09-26T21:00:00+00:00","snapshot_id":"snap-green"})+"\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def emit(self,kind,title="Test",detail="detail",severity="high"):
        enhancements.emit_incident(self.db,"cloud",kind,severity,title,detail,"test:"+kind)

    def test_positive_breakthrough_is_not_attention(self):
        self.emit("breakthrough","Nieuwe milestone","goed","high")
        center=enhancements.incident_center(self.db,{},self.recovery)
        self.assertEqual([],center["items"])

    def test_stale_worker_is_actionable_and_has_rollback_context(self):
        runners={"ftmo":{"workers":[{
            "worker_id":"ftmo::w2","state":"offline","age_seconds":601,
            "last_event":{"time":"2026-09-26T22:00:00+00:00"}
        }]}}
        center=enhancements.incident_center(self.db,runners,self.recovery)
        item=center["items"][0]
        self.assertEqual("stale_worker",item["type"])
        self.assertEqual("ftmo",item["project"])
        self.assertTrue(item["rollback"]["available"])
        self.assertEqual("tested",item["rollback"]["status"])

    def test_current_service_health_is_actionable_but_old_health_alert_is_not(self):
        self.emit("freeze","old service alert","old")
        healthy={"time":"2026-09-26T22:00:00+00:00","projects":[{"id":"ftmo","health":"healthy","services":[]}]}
        self.assertEqual([],enhancements.incident_center(self.db,{},self.recovery,data=healthy)["items"])
        bad={"time":"2026-09-26T22:00:00+00:00","projects":[{"id":"ftmo","health":"attention","services":[{"name":"Research timer","state":"failed","result":"exit-code"}]}]}
        item=enhancements.incident_center(self.db,{},self.recovery,data=bad)["items"][0]
        self.assertEqual("service_health",item["type"])
        self.assertIn("Research timer",item["cause"])

    def test_stale_handoff_failed_deploy_and_active_claim_conflict_are_recognized(self):
        self.emit("stale_handoff")
        self.emit("deploy_failed")
        claim_key="notion:active-task"
        now=enhancements.datetime.now(enhancements.timezone.utc)
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?)",
                (
                    "cloud",claim_key,"owner-a","cloud::w1",
                    now.isoformat(),now.isoformat(),
                    (now+enhancements.timedelta(minutes=15)).isoformat(),"{}",
                ),
            )
        enhancements.emit_incident(
            self.db,"cloud","claim_conflict","warning","Conflict",
            claim_key+" · huidige eigenaar owner-a · nieuwe poging owner-b",
            "claim-conflict:cloud:active-task",
        )
        center=enhancements.incident_center(self.db,{},self.recovery)
        kinds={x["type"] for x in center["items"]}
        self.assertTrue({"stale_handoff","deploy_failed","claim_conflict"}.issubset(kinds))
        for item in center["items"]:
            self.assertTrue(item["cause"])
            self.assertTrue(item["impact"])
            self.assertTrue(item["action"])
            self.assertIn("status",item["rollback"])

    def test_released_claim_conflict_disappears_from_attention(self):
        claim_key="notion:released-task"
        enhancements.emit_incident(
            self.db,"cloud","claim_conflict","warning","Conflict",
            claim_key+" · huidige eigenaar owner-a · nieuwe poging owner-b",
            "claim-conflict:cloud:released-task",
        )
        center=enhancements.incident_center(self.db,{},self.recovery)
        self.assertNotIn("claim_conflict",{x["type"] for x in center["items"]})

    def test_expired_claim_conflict_disappears_from_attention(self):
        claim_key="notion:expired-task"
        now=enhancements.datetime.now(enhancements.timezone.utc)
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO task_claims VALUES(?,?,?,?,?,?,?,?)",
                (
                    "cloud",claim_key,"owner-a","cloud::w1",
                    (now-enhancements.timedelta(minutes=20)).isoformat(),
                    (now-enhancements.timedelta(minutes=20)).isoformat(),
                    (now-enhancements.timedelta(minutes=5)).isoformat(),"{}",
                ),
            )
        enhancements.emit_incident(
            self.db,"cloud","claim_conflict","warning","Conflict",
            claim_key+" · huidige eigenaar owner-a · nieuwe poging owner-b",
            "claim-conflict:cloud:expired-task",
        )
        center=enhancements.incident_center(self.db,{},self.recovery)
        self.assertNotIn("claim_conflict",{x["type"] for x in center["items"]})


    def test_main_renderer_uses_incidents_not_raw_alert_stream(self):
        js=(Path(__file__).resolve().parents[1]/"public/enhancements.js").read_text(encoding="utf-8")
        start=js.index("function incidentPanel")
        end=js.index("function milestonePanel",start)
        panel=js[start:end]
        self.assertIn("DATA.incidents",panel)
        self.assertNotIn("DATA.alerts",panel)
        self.assertIn("Oorzaak",panel)
        self.assertIn("Impact",panel)
        self.assertIn("Herstel",panel)
        self.assertIn("Technische details",panel)

    def test_failed_recovery_becomes_critical_incident(self):
        (self.recovery/"recovery.log").write_text(
            json.dumps({"event":"rollback_failed_reverted","time":"2026-09-26T22:01:00+00:00","error":"boom"})+"\n"
        )
        center=enhancements.incident_center(self.db,{},self.recovery)
        item=next(x for x in center["items"] if x["type"]=="recovery_failed")
        self.assertEqual("Kritiek",item["health"])
        self.assertEqual("problem",item["rollback"]["status"])


if __name__=="__main__":
    unittest.main(verbosity=2)
