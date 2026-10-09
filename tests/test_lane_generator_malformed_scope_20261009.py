"""Regression: invalid scope metadata may not authorize parallel writes.

Pure lane-planner tests using only synthetic queue/claim objects.
Raises a clear ValueError instead of silently dropping conflicting paths or
interpreting an object as its keys. No live queue, SQLite, browser or VPS.
"""
import unittest

from lane_generator import generate_execution_lanes, scopes_overlap

PROJECT = {"id": "cloud", "lane_profile": "platform"}


def entry(queue_id, title, scope, status="queued"):
    return {
        "queue_id": queue_id,
        "project_id": "cloud",
        "title": title,
        "status": status,
        "priority": "P1",
        "created_at": "2026-10-09T00:00:00Z",
        "metadata": {"conflict_scope": scope},
    }


class InvalidScopeAdmissionTests(unittest.TestCase):
    def assert_denied(self, invalid_scope, pattern):
        control = entry("control", "Implement queue scheduler",
                        invalid_scope, "running")
        runtime = entry("runtime", "Repair Firefox automation",
                        {"files": ["src/shared.py"]})
        with self.assertRaisesRegex(ValueError, pattern):
            generate_execution_lanes(PROJECT, [control, runtime])

    def test_mapping_file_entries_are_not_misread_as_mapping_keys(self):
        self.assert_denied({"files": {"filename": "src/shared.py"}},
                           "invalid conflict_scope.files")

    def test_numeric_file_scope_denied(self):
        self.assert_denied({"files": 27}, "invalid conflict_scope.files")

    def test_boolean_file_scope_denied(self):
        self.assert_denied({"files": True}, "invalid conflict_scope.files")

    def test_file_list_with_nonstring_entry_denied(self):
        self.assert_denied({"files": ["src/shared.py", None]},
                           "invalid conflict_scope.files")

    def test_capability_mapping_denied(self):
        self.assert_denied({"capabilities": {"claim": "shared-cap"}},
                           "invalid conflict_scope.capabilities")

    def test_capability_list_with_numeric_element_denied(self):
        self.assert_denied({"capabilities": ["shared-cap", 42]},
                           "invalid conflict_scope.capabilities")

    def test_conflict_scope_as_scalar_denied(self):
        self.assert_denied("src/shared.py", "invalid conflict_scope")

    def test_parent_traversal_must_not_disappear(self):
        self.assert_denied({"files": ["src/../shared.py"]},
                           "parent traversal")

    def test_active_claim_with_malformed_scope_denied(self):
        runtime = entry("runtime", "Repair Firefox automation",
                        {"files": ["src/shared.py"]})
        claims = [{
            "project_id": "cloud",
            "claim_key": "peer",
            "metadata": {"conflict_scope": {
                "files": {"path": "src/shared.py"},
            }},
        }]
        with self.assertRaisesRegex(ValueError,
                                    "invalid conflict_scope.files"):
            generate_execution_lanes(PROJECT, [runtime], claims)

    def test_queued_candidate_with_malformed_scope_denied(self):
        runtime = entry("runtime", "Repair Firefox automation",
                        {"files": ["src/../shared.py"]})
        with self.assertRaisesRegex(ValueError, "parent traversal"):
            generate_execution_lanes(PROJECT, [runtime])

    def test_valid_scalar_and_list_forms_still_overlap(self):
        overlap = scopes_overlap(
            {"files": "src/shared.py", "capabilities": "cloud:runtime-automation"},
            {"files": ["src/shared.py"], "capabilities": ["cloud:runtime-automation"]},
        )
        self.assertEqual(["src/shared.py"], overlap["files"])
        self.assertEqual(["cloud:runtime-automation"], overlap["capabilities"])

    def test_normalized_dot_path_and_backslashes_still_overlap(self):
        overlap = scopes_overlap(
            {"files": [r"./src\\shared.py"]},
            {"files": ["src/shared.py"]},
        )
        self.assertEqual(["src/shared.py"], overlap["files"])

    def test_empty_optional_scopes_remain_valid(self):
        overlap = scopes_overlap(
            {"files": None, "capabilities": None},
            {"files": [], "capabilities": []},
        )
        self.assertEqual({"files": [], "capabilities": []}, overlap)
    def test_corrupt_queue_metadata_json_does_not_hide_write_scope(self):
        queued = entry("runtime", "Repair Firefox automation", {})
        queued.pop("metadata")
        queued["metadata_json"] = '{"conflict_scope":{"files":["shared.py"]'
        with self.assertRaisesRegex(ValueError, "invalid metadata_json"):
            generate_execution_lanes(PROJECT, [queued])

    def test_nonobject_queue_metadata_json_does_not_hide_write_scope(self):
        queued = entry("runtime", "Repair Firefox automation", {})
        queued.pop("metadata")
        queued["metadata_json"] = '["not-a-scope"]'
        with self.assertRaisesRegex(ValueError, "invalid metadata_json"):
            generate_execution_lanes(PROJECT, [queued])

    def test_corrupt_claim_metadata_json_blocks_unknown_ownership(self):
        queued = entry("runtime", "Repair Firefox automation",
                       {"files": ["src/shared.py"]})
        claims = [{
            "project_id": "cloud",
            "claim_key": "peer",
            "metadata_json": '{"conflict_scope":{"files":["src/shared.py"]'
        }]
        with self.assertRaisesRegex(ValueError, "invalid metadata_json"):
            generate_execution_lanes(PROJECT, [queued], claims)

    def test_valid_claim_metadata_json_remains_authoritative(self):
        queued = entry("runtime", "Repair Firefox automation",
                       {"files": ["src/shared.py"]})
        claims = [{
            "project_id": "cloud",
            "claim_key": "peer",
            "metadata_json": '{"conflict_scope":{"files":["src/shared.py"]}}',
        }]
        lanes = generate_execution_lanes(PROJECT, [queued], claims)
        runtime = next(row for row in lanes
                       if row["lane_id"] == "runtime-automation")
        self.assertEqual("blocked", runtime["status"])
        self.assertEqual("task_claim_scope_conflict",
                         runtime["blocked_by"][0]["reason"])

    def test_explicit_blank_file_scope_entry_is_not_ignored(self):
        self.assert_denied({"files": ["  "]},
                           "invalid conflict_scope.files")

    def test_dot_root_path_cannot_hide_file_scope(self):
        self.assert_denied({"files": ["./"]},
                           "invalid conflict_scope.files")

    def test_explicit_empty_capability_entry_denied(self):
        self.assert_denied({"capabilities": ["---"]},
                           "invalid conflict_scope.capabilities")

    def test_control_characters_in_file_path_denied(self):
        self.assert_denied({"files": ["src/secret\x00.py"]},
                           "control-character path")

    def test_valid_scope_with_optional_empty_lists_remains_valid(self):
        result = scopes_overlap(
            {"files": [], "capabilities": []},
            {"files": [], "capabilities": []}
        )
        self.assertEqual({"files": [], "capabilities": []}, result)

    def test_trailing_newline_file_path_denied_before_stripping(self):
        self.assert_denied({"files": ["src/server.py\n"]},
                           "control-character path")

    def test_leading_tab_file_path_denied_before_stripping(self):
        self.assert_denied({"files": ["\tsrc/server.py"]},
                           "control-character path")

    def test_capability_embedded_control_character_denied(self):
        self.assert_denied({"capabilities": ["queue\x00owner"]},
                           "control character")

    def test_capability_trailing_control_character_denied(self):
        self.assert_denied({"capabilities": ["queue-owner\n"]},
                           "control character")

    def test_blank_metadata_json_does_not_mean_empty_scope(self):
        queued = entry("runtime", "Repair Firefox automation", {})
        queued.pop("metadata")
        queued["metadata_json"] = ""
        with self.assertRaisesRegex(ValueError, "invalid metadata_json"):
            generate_execution_lanes(PROJECT, [queued])

    def test_falsy_numeric_metadata_json_does_not_mean_empty_scope(self):
        queued = entry("runtime", "Repair Firefox automation", {})
        queued.pop("metadata")
        queued["metadata_json"] = 0
        with self.assertRaisesRegex(ValueError, "invalid metadata_json"):
            generate_execution_lanes(PROJECT, [queued])

    def test_absent_metadata_json_still_defaults_to_empty(self):
        self.assertEqual(
            {"files": [], "capabilities": []},
            scopes_overlap({}, {"files": None, "capabilities": None}),
        )



if __name__ == "__main__":
    unittest.main()
