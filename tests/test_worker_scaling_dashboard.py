import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkerScalingDashboardTests(unittest.TestCase):
    def test_dashboard_loads_scaling_separately_from_status(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function workerScalingPanel()", app)
        self.assertIn("function loadWorkerScaling()", app)
        self.assertIn("fetch('/api/worker-scaling?hours=12'", app)
        self.assertIn("scalingChip(p.id)", app)
        self.assertIn("${dynamicWorkerControl()}${workerScalingPanel()}${workerDebugPanel()}", app)
        self.assertNotIn("api('/api/status?worker_scaling", app)

    def test_dashboard_exposes_per_worker_time_breakdown(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("worker-time-breakdown", app)
        self.assertIn("m.working_seconds", app)
        self.assertIn("m.idle_seconds", app)
        self.assertIn("m.blocked_seconds", app)
        self.assertIn("m.observed_seconds", app)
        self.assertIn(" · idle ", app)
        self.assertIn(" · blocked ", app)

    def test_server_endpoint_is_read_only_report_adapter(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("if u.path=='/api/worker-scaling':", server)
        self.assertIn("hours=max(1.0,min(72.0", server)
        self.assertIn("worker_scaling_report.report(DB,hours,project_id)", server)

    def test_scaling_copy_does_not_make_causal_claims(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        report = (ROOT / "scripts" / "worker_scaling_report.py").read_text(encoding="utf-8")
        self.assertIn("observational", app.lower())
        self.assertIn("observational runner telemetry", report)
        self.assertIn("no causal claim", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
