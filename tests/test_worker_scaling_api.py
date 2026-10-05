import unittest
from pathlib import Path
from unittest.mock import patch

import server


class WorkerScalingApiTests(unittest.TestCase):
    def test_summary_delegates_to_read_only_scaling_report(self):
        expected = {"generated_at": "2026-10-04T22:00:00+00:00", "projects": []}
        with patch.object(server.worker_scaling_report, "report", return_value=expected) as report:
            payload = server.worker_scaling_summary("ftmo", "6")

        self.assertEqual(expected, payload)
        report.assert_called_once_with(server.DB, 6.0, "ftmo")

    def test_summary_supports_portfolio_wide_report(self):
        with patch.object(server.worker_scaling_report, "report", return_value={"projects": []}) as report:
            server.worker_scaling_summary("", 12)

        report.assert_called_once_with(server.DB, 12.0, None)

    def test_summary_rejects_unknown_project_and_unbounded_window(self):
        with self.assertRaises(KeyError):
            server.worker_scaling_summary("not-a-project", 12)
        for hours in ("nope", 0.1, 169):
            with self.assertRaises(ValueError):
                server.worker_scaling_summary("ftmo", hours)

    def test_http_route_exposes_scaling_indicator(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn("if u.path=='/api/worker-scaling':", source)
        self.assertIn("worker_scaling_summary(project_id,q.get('hours',['12'])[0])", source)
        self.assertIn("return self.reply({'error':'Onbekend project'},404)", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
