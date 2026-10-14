"""Offline safety tests for the human-readable, GET-only route triage output."""
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

from scripts import zcloud_queue_route_summary_20261014_w1 as summary
from scripts import zcloud_queue_route_triage_20261014_w1 as triage

CLI = Path(__file__).resolve().parents[1] / "scripts/zcloud_queue_route_summary_20261014_w1.py"


def record(number=71, *, scope="historical_main", route="ftmo_research",
           assignment="not_assigned", state="queued", jobs=1):
    return {
        "run_id": number,
        "url": f"https://github.com/Zennay/zCloud/actions/runs/{number}",
        "scope": scope, "route": route,
        "assignment": assignment, "job_state": state, "job_count": jobs,
    }


def report(rows=None):
    rows = [] if rows is None else rows
    buckets = {}
    for row in rows:
        key = f'{row["scope"]}/{row["route"]}/{row["assignment"]}/{row["job_state"]}'
        buckets[key] = buckets.get(key, 0) + 1
    return {
        "policy": triage.IDENTITY, "decision": "OBSERVED",
        "observed_runs": len(rows), "runs": rows, "route_counts": buckets,
        "runner_recovery_authorized": False, "production_verified": False,
        "worker_generation_verified": False, "cancel_run_ids": [],
    }


class QueueRouteSummaryTests(unittest.TestCase):
    def render(self, data, rc=0):
        return summary.safe_summary(json.dumps(data).encode("utf-8"), rc)

    def test_empty_queue_is_observation_not_authorization(self):
        markdown, rc = self.render(report())
        self.assertEqual(rc, 0)
        self.assertIn("0 runs", markdown)
        self.assertIn("Geen bewijs van productiestatus", markdown)
        self.assertIn("Geen bevoegdheid", markdown)

    def test_disjoint_routes_render_with_canonical_links(self):
        rows = [
            record(71),
            record(72, scope="historical_main", route="no_job_records",
                   assignment="no_job_records", state="no_job_records", jobs=0),
            record(73, scope="current_main", route="zcloud_vps"),
        ]
        markdown, rc = self.render(report(rows))
        self.assertEqual(rc, 0)
        self.assertIn("3 runs", markdown)
        for number in (71, 72, 73):
            self.assertIn(f"https://github.com/Zennay/zCloud/actions/runs/{number}", markdown)
        self.assertIn("ftmo_research", markdown)
        self.assertIn("no_job_records", markdown)
        self.assertIn("zcloud_vps", markdown)
        self.assertNotIn("runner is offline", markdown)

    def test_verified_dutch_route_totals_are_derived_from_rows(self):
        rows = [
            record(21, route="ftmo_research"),
            record(22, route="ftmo_research"),
            record(23, route="no_job_records", assignment="no_job_records",
                   state="no_job_records", jobs=0),
            record(24, scope="current_main", route="zcloud_vps"),
        ]
        markdown, code = self.render(report(rows))
        self.assertEqual(code, 0)
        self.assertIn("**2 FTMO-onderzoek**", markdown)
        self.assertIn("**1 zCloud-VPS**", markdown)
        self.assertIn("**1 zonder jobrecords**", markdown)
        self.assertIn("**3 zonder runnertoewijzing**", markdown)
        self.assertIn("**1 op huidige main**", markdown)
        self.assertNotIn("productie groen", markdown.lower())

    def test_route_counts_are_not_printed_when_any_row_is_invalid(self):
        rows = [record(1), record(2, route="no_job_records",
                                  assignment="no_job_records",
                                  state="no_job_records", jobs=0)]
        payload = report(rows)
        payload["route_counts"] = {"wrong": 2}
        markdown, status = self.render(payload)
        self.assertEqual(status, 1)
        self.assertNotIn("FTMO-onderzoek", markdown)
        self.assertNotIn("zonder jobrecords**", markdown)

    def test_wrong_exit_status_and_incomplete_reports_deny(self):
        for rc in (1, 2, 124, True):
            with self.subTest(code=rc):
                markdown, status = self.render(report([record()]), rc)
                self.assertEqual(status, 1)
                self.assertIn("Geen aantallen gepubliceerd", markdown)
                self.assertNotIn("Run 71", markdown)

    def test_invalid_privilege_flags_deny(self):
        for field, val in (("runner_recovery_authorized", True),
                           ("production_verified", True),
                           ("worker_generation_verified", True),
                           ("cancel_run_ids", [71])):
            with self.subTest(field=field):
                data = report([record()])
                data[field] = val
                self.assertEqual(self.render(data)[1], 1)

    def test_duplicate_or_boolean_run_id_deny(self):
        for rows in ([record(), record()],
                     [record(True)], [record(0)], [record("71")]):
            with self.subTest(rows=rows):
                data = report(rows)
                self.assertEqual(self.render(data)[1], 1)

    def test_invalid_route_counts_deny_instead_of_claiming_progress(self):
        for change in ({"observed_runs": 0}, {"route_counts": {}},
                       {"observed_runs": True}, {"runs": []}):
            data = report([record()])
            data.update(change)
            with self.subTest(change=change):
                markdown, status = self.render(data)
                self.assertEqual(status, 1)
                self.assertNotIn("Run 71", markdown)

    def test_unknown_or_html_injected_scope_is_not_reflected(self):
        data = report([record()])
        data["runs"][0]["scope"] = "<script>SECRET</script>"
        markdown, rc = self.render(data)
        self.assertEqual(rc, 1)
        self.assertNotIn("SECRET", markdown)
        self.assertIn("ONBETROUWBAAR", markdown)

    def test_job_counts_must_be_consistent_with_route(self):
        for invalid in (
            record(route="no_job_records", assignment="no_job_records",
                   state="no_job_records", jobs=1),
            record(jobs=0), record(jobs=True), record(jobs=200),
        ):
            with self.subTest(row=invalid):
                self.assertEqual(self.render(report([invalid]))[1], 1)

    def test_duplicate_json_keys_denied(self):
        raw = json.dumps(report()).encode("utf-8")[:-1] + b',"decision":"OBSERVED"}'
        self.assertEqual(summary.safe_summary(raw, 0)[1], 1)

    def test_missing_input_oversized_invalid_utf8_and_nan_denied(self):
        for payload in (b"", b"\xff", b"x" * 32768,
                        json.dumps(report()).encode()[:-1] + b',"x":NaN}'):
            with self.subTest(payload_len=len(payload)):
                self.assertEqual(summary.safe_summary(payload, 0)[1], 1)

    def test_untrusted_freeform_data_never_leaks_to_operator(self):
        data = report([record()])
        data["token"] = "MY_SECRET_TOKEN"
        data["runs"][0]["private_notes"] = "PRIVATE_PROMPT"
        markdown, status = self.render(data)
        self.assertEqual(status, 0)
        self.assertNotIn("MY_SECRET_TOKEN", markdown)
        self.assertNotIn("PRIVATE_PROMPT", markdown)

    def test_unknown_url_is_denied_not_rendered(self):
        data = report([record()])
        data["runs"][0]["url"] = "https://hostile.example/steal"
        markdown, status = self.render(data)
        self.assertEqual(status, 1)
        self.assertNotIn("hostile", markdown)

    def test_cli_report_generates_markdown_without_raw_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "triage.json"
            target = Path(tmp) / "summary.md"
            data = report([record()])
            data["token"] = "SECRET"
            source.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CLI), "--report", str(source),
                 "--collector-exit", "0", "--summary-path", str(target)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            output = target.read_text(encoding="utf-8")
            self.assertIn("Run 71", output)
            self.assertNotIn("SECRET", output)
            self.assertEqual(result.stdout, "")

    def test_cli_missing_report_is_non_green_and_nonleaking(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "summary.md"
            result = subprocess.run(
                [sys.executable, str(CLI), "--report", str(Path(tmp) / "missing"),
                 "--collector-exit", "1", "--summary-path", str(target)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("ONBETROUWBAAR", target.read_text(encoding="utf-8"))
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
