import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import zcloud_temp_artifact_cleanup_selector as selector


NOW = datetime(2026, 10, 6, 21, 55, tzinfo=timezone.utc)


def artifact(**overrides):
    value = {
        "scope": "system_tmp",
        "name": "zcloud-old-canary.json",
        "size_bytes": 4096,
        "modified_at": "2026-09-20T12:00:00Z",
        "regular_file": True,
        "symlink": False,
        "open_handles": 0,
        "active_process_ref": False,
        "active_run_ref": False,
        "durable_copy": True,
    }
    value.update(overrides)
    return value


class TempArtifactSelectorTests(unittest.TestCase):
    def test_old_safe_durable_idle_file_is_candidate(self):
        result = selector.select([artifact()], now=NOW)
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(0, result["blocked_count"])
        self.assertFalse(result["mutation_performed"])

    def test_blocks_each_loss_or_liveness_risk(self):
        cases = [
            ("directory", {"regular_file": False}, "NOT_REGULAR_FILE"),
            ("symlink", {"symlink": True}, "SYMLINK"),
            ("open", {"open_handles": 1}, "OPEN_HANDLE"),
            ("process", {"active_process_ref": True}, "ACTIVE_PROCESS_REF"),
            ("run", {"active_run_ref": True}, "ACTIVE_RUN_REF"),
            ("not_durable", {"durable_copy": False}, "NO_DURABLE_COPY"),
            ("recent", {"modified_at": "2026-10-02T00:00:00Z"}, "TOO_RECENT"),
            ("prefix", {"name": "unowned-cache.json"}, "UNSAFE_PREFIX"),
            ("suffix", {"name": "zcloud-old-canary.sqlite"}, "UNSAFE_SUFFIX"),
        ]
        for label, overrides, reason in cases:
            with self.subTest(label=label):
                result = selector.select([artifact(**overrides)], now=NOW)
                self.assertEqual(0, result["candidate_count"])
                self.assertIn(reason, result["blocked"][0]["reasons"])

    def test_rejects_paths_and_hidden_mutation_controls(self):
        with self.assertRaises(selector.InputError):
            selector.select([artifact(name="../zcloud-old-canary.json")], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([{**artifact(), "delete": True}], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select({"artifacts": [artifact()], "apply": True}, now=NOW)

    def test_rejects_loose_types_and_future_time(self):
        with self.assertRaises(selector.InputError):
            selector.select([artifact(open_handles=False)], now=NOW)
        with self.assertRaises(selector.InputError):
            selector.select([artifact(modified_at="2026-10-07T00:00:00Z")], now=NOW)

    def test_rejects_oversized_artifact_and_inventory(self):
        with self.assertRaises(selector.InputError):
            selector.select(
                [artifact(size_bytes=selector.MAX_SIZE_BYTES + 1)],
                now=NOW,
            )
        with self.assertRaises(selector.InputError):
            selector.select(
                [artifact(name=f"zcloud-{i}.json") for i in range(selector.MAX_RECORDS + 1)],
                now=NOW,
            )

    def test_load_payload_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "payload.json"
            target.write_text(json.dumps([artifact()]), encoding="utf-8")
            link = root / "payload-link.json"
            os.symlink(target, link)
            with self.assertRaises(selector.InputError):
                selector.load_payload(link)

    def test_candidate_order_is_deterministic(self):
        result = selector.select(
            [
                artifact(scope="runner_temp", name="zcloud-b.json", modified_at="2026-09-01T00:00:00Z"),
                artifact(scope="runner_temp", name="zcloud-a.json", modified_at="2026-09-01T00:00:00Z"),
                artifact(scope="system_tmp", name="ftmo-older.txt", modified_at="2026-08-01T00:00:00Z"),
            ],
            now=NOW,
        )
        self.assertEqual(
            ["ftmo-older.txt", "zcloud-a.json", "zcloud-b.json"],
            [item["name"] for item in result["candidates"]],
        )


if __name__ == "__main__":
    unittest.main()
