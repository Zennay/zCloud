import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

class ProjectMonitorTests(unittest.TestCase):
    def setUp(self):
        server._PROJECT_CACHE.update(expires=0, value={})

    def test_monitor_filters_private_runner_fields_and_reuses_snapshot(self):
        raw={"time":"2026-10-12T16:50:00Z","projects":[{"id":"supa","name":"Supa","progress":40,"next":"Boodschappen","source_status":"ok","repo":"/private/path","token":"never-expose"}],"runner_targets":{"secret":"hidden"}}
        with patch.object(server, "zcloud", return_value=raw) as upstream:
            result=server.office_projects()
            self.assertTrue(result["online"])
            self.assertEqual(result["projects"][0]["next"],"Boodschappen")
            self.assertNotIn("token", result["projects"][0])
            self.assertNotIn("repo", result["projects"][0])
            self.assertNotIn("runner_targets", result)
            self.assertEqual(server.office_projects(),result)
            self.assertEqual(upstream.call_count,1)

    def test_offline_monitor_does_not_present_previous_data_as_live(self):
        with patch.object(server,"zcloud",side_effect=ConnectionError("offline")):
            result=server.office_projects()
        self.assertFalse(result["online"])
        self.assertEqual(result["projects"],[])

    def test_invalid_snapshot_is_not_called_online(self):
        with patch.object(server,"zcloud",return_value={"projects":{"wrong":"shape"}}):
            self.assertFalse(server.office_projects()["online"])
