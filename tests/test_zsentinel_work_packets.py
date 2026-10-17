"""Offline zSentinel delivery packet selector regressions (no live integrations)."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.zsentinel_work_packets import select_packets, _regular_json


NOW = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)
MAIN = "a" * 40


def issue(item_id="fix-1", project="supa", priority="P1", files=None, **changes):
    value = {
        "project": project, "item_id": item_id, "kind": "issue",
        "priority": priority, "impact": 4, "effort": 3,
        "owner": "free", "claim": "free", "hold": "none",
        "source_allowed": True, "files": files or ["server/sync-store.mjs"],
    }
    value.update(changes)
    return value


def pr(item_id="pr-21", project="cloud", priority="P1", files=None, **changes):
    value = issue(item_id, project, priority, files or ["scripts/fix.py"])
    value.update({
        "kind": "pr", "head_sha": "b" * 40, "base_sha": MAIN,
        "ci_passed": True, "review_approved": True, "mergeable": True,
        "draft": False,
    })
    value.update(changes)
    return value


def receipt(items=None):
    return {
        "schema_version": 1, "observed_at": "2026-10-17T19:55:00Z",
        "projects": [
            {"id": "supa", "main_sha": MAIN, "lease_verified": True},
            {"id": "cloud", "main_sha": MAIN, "lease_verified": True},
            {"id": "mixlab", "main_sha": MAIN, "lease_verified": True},
        ],
        "items": items if items is not None else [issue()],
    }


class WorkPacketsTests(unittest.TestCase):
    def run_packet(self, items=None, limit=4):
        return select_packets(receipt(items), now=NOW, limit=limit)

    def test_selects_real_source_issue_without_mutation_authority(self):
        report = self.run_packet()
        self.assertEqual(report["packets"][0]["action"], "implement_issue")
        self.assertEqual(len(report["packets"][0]["delivery"]), 3)
        self.assertFalse(report["mutation_performed"])
        self.assertFalse(report["queue_claimed"])
        self.assertFalse(report["merge_or_deploy_authorized"])
        self.assertFalse(report["packets"][0]["authorization_granted"])

    def test_prefer_reviewed_green_main_current_pr_over_new_qa(self):
        report = self.run_packet([issue("new-task", "cloud", files=["public/ui.js"]),
                                  pr("existing-green", "cloud")])
        self.assertEqual(report["packets"][0]["item_id"], "existing-green")
        self.assertEqual(report["packets"][1]["item_id"], "new-task")

    def test_pr_must_be_green_reviewed_non_draft_and_current_base(self):
        values = [
            {"ci_passed": False}, {"review_approved": False},
            {"mergeable": False}, {"draft": True}, {"base_sha": "f" * 40},
        ]
        for override in values:
            with self.subTest(override=override):
                report = self.run_packet([pr(**override), issue("fallback")])
                self.assertEqual([p["item_id"] for p in report["packets"]], ["fallback"])
                self.assertEqual(report["blocked"]["unverified_pr"], 1)

    def test_owner_and_claims_fail_closed_but_other_project_proceeds(self):
        data = receipt([
            issue("active-owner", priority="P0", owner="active"),
            issue("unknown-claim", claim="unknown"),
            issue("unverified-lease", project="mixlab", files=["tests/a.py"]),
            issue("free", project="cloud", files=["public/ui.js"]),
        ])
        data["projects"][2]["lease_verified"] = False
        report = select_packets(data, now=NOW)
        self.assertEqual([p["item_id"] for p in report["packets"]], ["free"])
        self.assertEqual(report["blocked"]["ownership_or_lease"], 3)

    def test_external_live_hold_allows_independent_source_only_issue(self):
        report = self.run_packet([
            issue("safe-source", hold="external"),
            issue("not-authorized", project="mixlab", source_allowed=False, files=["a.py"]),
            pr("no-release", hold="external"),
            issue("production-writer", project="cloud", hold="release", files=["a.py"]),
        ])
        self.assertEqual([p["item_id"] for p in report["packets"]], ["safe-source"])
        self.assertEqual(report["blocked"]["release_or_external_gate"], 2)

    def test_priority_before_effort_and_impact(self):
        report = self.run_packet([
            issue("p2", "mixlab", "P2", ["a.py"], impact=5),
            issue("p0", "cloud", "P0", ["a.py"], impact=2),
            issue("p1", "supa", "P1", ["a.py"], impact=5),
        ])
        self.assertEqual([p["item_id"] for p in report["packets"]], ["p0", "p1", "p2"])

    def test_packet_cap_two_per_project_and_file_scope_disjoint(self):
        items = [issue("a", files=["server"]),
                 issue("b", files=["server/sync-store.mjs"]),
                 issue("c", files=["tests/sync.test.mjs"]),
                 issue("d", files=["public/app.js"]),
                 issue("e", project="mixlab", files=["mixlab/core.py"])]
        report = self.run_packet(items)
        self.assertEqual([p["item_id"] for p in report["packets"]], ["a", "c", "e"])
        self.assertEqual(len(self.run_packet(items, limit=1)["packets"]), 1)

    def test_stale_future_timestamp_never_admitted(self):
        for when in [NOW - timedelta(minutes=16), NOW + timedelta(minutes=3)]:
            data = receipt()
            data["observed_at"] = when.isoformat().replace("+00:00", "Z")
            with self.subTest(when=when), self.assertRaises(ValueError):
                select_packets(data, now=NOW)

    def test_project_identity_and_provenance_required(self):
        for override in [{"id": "../main"}, {"main_sha": "not a SHA"},
                         {"lease_verified": 1}]:
            data = receipt()
            data["projects"][0].update(override)
            with self.subTest(override=override), self.assertRaises(ValueError):
                select_packets(data, now=NOW)

    def test_rejects_untrusted_files_and_duplicate_candidates(self):
        bad_files = ["../secret", "/root/path", ".git/config", "a//b", "a/./b",
                     "a/../b", "a\\b", "", "a\nb"]
        for bad in bad_files:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_packet([issue(files=[bad])])
        with self.assertRaises(ValueError):
            self.run_packet([issue(), issue()])

    def test_bad_flags_and_pr_sha_rejected(self):
        for override in [{"source_allowed": 1}, {"impact": True},
                         {"effort": 0}, {"claim": "unknown-but-free"},
                         {"files": ["a.py", "a.py"]}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.run_packet([issue(**override)])
        for override in [{"draft": 0}, {"ci_passed": "true"}, {"head_sha": "g" * 40}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.run_packet([pr(**override)])


    def test_unhashable_control_fields_are_rejected_not_interpreted(self):
        for override in [{"kind": []}, {"priority": []}, {"owner": []},
                         {"claim": {}}, {"hold": ["none"]}]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.run_packet([issue(**override)])

    def test_valid_cli_input_emits_machine_readable_denied_authority(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "verified-receipt.json"
            data = receipt([issue("safe-source")])
            data["observed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            p.write_text(json.dumps(data), encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "zsentinel_work_packets.py"
            result = subprocess.run([sys.executable, str(script), str(p), "--limit", "1"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["packets"][0]["item_id"], "safe-source")
            self.assertFalse(report["merge_or_deploy_authorized"])
            self.assertFalse(report["mutation_performed"])

    def test_missing_or_extra_pr_flags_fail_closed(self):
        broken = pr()
        del broken["review_approved"]
        with self.assertRaises(ValueError):
            self.run_packet([broken])
        with self.assertRaises(ValueError):
            self.run_packet([issue(ci_passed=True)])

    def test_invalid_envelope_and_limit_rejected(self):
        for invalid in [0, True, 5]:
            with self.subTest(limit=invalid), self.assertRaises(ValueError):
                self.run_packet(limit=invalid)
        data = receipt()
        data["schema_version"] = True
        with self.assertRaises(ValueError):
            select_packets(data, now=NOW)

    def test_regular_file_ingress_rejects_symlink_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "valid.json"
            p.write_text(json.dumps(receipt()), encoding="utf-8")
            self.assertEqual(_regular_json(p)["schema_version"], 1)
            link = Path(root) / "link.json"
            link.symlink_to(p)
            with self.assertRaises(OSError):
                _regular_json(link)
            p.write_text('{"schema_version":1,"schema_version":false}', encoding="utf-8")
            with self.assertRaises(ValueError):
                _regular_json(p)

    def test_cli_returns_error_without_echoing_sensitive_values(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "secret.json"
            p.write_text('{"bad":"TOP_SECRET_TOKEN"}', encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "zsentinel_work_packets.py"
            result = subprocess.run([sys.executable, str(script), str(p)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("TOP_SECRET_TOKEN", result.stderr + result.stdout)
            self.assertNotIn(str(p), result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
