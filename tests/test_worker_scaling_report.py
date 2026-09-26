import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import worker_scaling_report as scaling


class WorkerScalingReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="zcloud-scaling-")
        self.db=Path(self.tmp.name)/"history.db"
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""CREATE TABLE runner_events(
                id INTEGER PRIMARY KEY,ts TEXT,event TEXT,target TEXT,title TEXT,
                generating INTEGER,sending INTEGER,reason TEXT,tab_id INTEGER,error TEXT,
                project_id TEXT,progress_at TEXT,assistant_chars INTEGER,worker_slot INTEGER NOT NULL DEFAULT 1)""")
            c.execute("""CREATE TABLE runner_targets(
                project_id TEXT PRIMARY KEY,name TEXT,conversation_id TEXT,prompt TEXT,active INTEGER,worker_count INTEGER)""")
            c.execute("""CREATE TABLE alerts(
                id TEXT PRIMARY KEY,ts TEXT,project TEXT,kind TEXT,severity TEXT,title TEXT,
                detail TEXT,important INTEGER,fingerprint TEXT)""")
            c.execute("INSERT INTO runner_targets VALUES('ftmo','FTMO','','',1,2)")
            c.commit()
        self.now=datetime(2026,9,26,22,0,tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self,slot,minutes,event="heartbeat",generating=0):
        ts=(self.now-timedelta(minutes=minutes)).isoformat()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("""INSERT INTO runner_events(ts,event,generating,sending,project_id,worker_slot)
                         VALUES(?,?,?,?,?,?)""",(ts,event,generating,0,"ftmo",slot))
            c.commit()

    def heartbeat_series(self,slot,start_minutes,count,generating=True):
        for i in range(count):
            self.add(slot,start_minutes-i,"heartbeat",1 if generating else 0)

    def test_worker_metrics_separates_work_idle_and_blocked(self):
        rows=[]
        # Direct dict rows are sufficient for worker_metrics.
        base=self.now-timedelta(minutes=4)
        rows=[
            {"ts":base.isoformat(),"event":"heartbeat","generating":1},
            {"ts":(base+timedelta(minutes=1)).isoformat(),"event":"generation-finished","generating":0},
            {"ts":(base+timedelta(minutes=1)).isoformat(),"event":"heartbeat","generating":0},
            {"ts":(base+timedelta(minutes=2)).isoformat(),"event":"send-blocked","generating":0},
            {"ts":(base+timedelta(minutes=2)).isoformat(),"event":"heartbeat","generating":0},
            {"ts":(base+timedelta(minutes=3)).isoformat(),"event":"heartbeat","generating":0},
        ]
        metrics=scaling.worker_metrics(rows)
        self.assertEqual(180,metrics["observed_seconds"])
        self.assertEqual(60,metrics["working_seconds"])
        self.assertEqual(60,metrics["idle_seconds"])
        self.assertEqual(60,metrics["blocked_seconds"])

    def test_report_marks_useful_two_worker_scaling_with_enough_data(self):
        # 40 minutes of reliable heartbeats for each worker.
        for slot in (1,2):
            self.heartbeat_series(slot,40,41,True)
        # Primary 4 finishes, extra 3 finishes -> >=60% rate and enough evidence.
        for m in (35,25,15,5): self.add(1,m,"generation-finished",0)
        for m in (30,20,10): self.add(2,m,"generation-finished",0)
        payload=scaling.report(self.db,12,"ftmo",self.now)
        p=payload["projects"][0]
        self.assertEqual("useful_scaling",p["assessment"]["state"])
        self.assertEqual(2,p["desired_workers"])

    def test_report_refuses_thin_extra_worker_data(self):
        self.heartbeat_series(1,40,41,True)
        self.heartbeat_series(2,10,11,True)
        for m in (35,25,15,5): self.add(1,m,"generation-finished",0)
        self.add(2,5,"generation-finished",0)
        p=scaling.report(self.db,12,"ftmo",self.now)["projects"][0]
        self.assertEqual("insufficient_data",p["assessment"]["state"])

    def test_claim_conflicts_measure_duplicate_work_prevented(self):
        with closing(sqlite3.connect(self.db)) as c:
            for i in range(2):
                c.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?)",
                          (f"a{i}",self.now.isoformat(),"ftmo","claim_conflict","warning","Conflict","",1,f"f{i}"))
            c.commit()
        self.heartbeat_series(1,5,6,False)
        p=scaling.report(self.db,12,"ftmo",self.now)["projects"][0]
        self.assertEqual(2,p["duplicate_work_prevented"])

    def test_database_is_opened_read_only(self):
        self.heartbeat_series(1,5,6,False)
        before=self.db.stat().st_mtime_ns
        scaling.report(self.db,12,"ftmo",self.now)
        after=self.db.stat().st_mtime_ns
        self.assertEqual(before,after)


if __name__=="__main__":
    unittest.main(verbosity=2)

[executed on device: vps-bb300bba (43be714e-c0e5-463e-8ac8-e5aa8446b070)]