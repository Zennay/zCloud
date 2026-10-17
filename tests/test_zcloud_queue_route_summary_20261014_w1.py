"""Offline safety tests for the human-readable, GET-only route triage output."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
NOW = datetime(2026, 10, 14, 4, 10, 30, tzinfo=timezone.utc)


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
        "main_sha_before": "a" * 40, "main_sha_after": "a" * 40,
        "observed_at": "2026-10-14T04:10:00Z",
        "observed_runs": len(rows), "runs": rows, "route_counts": buckets,
        "reason": "read_only_route_counts_not_runner_liveness",
        "mutation_authorized": False,
        "runner_recovery_authorized": False, "production_verified": False,
        "worker_generation_verified": False, "cancel_run_ids": [],
    }


class QueueRouteSummaryTests(unittest.TestCase):
    def render(self, data, rc=0):
        return summary.safe_summary(json.dumps(data).encode("utf-8"), rc, now=NOW)

    def test_stale_or_future_source_report_never_claims_current_queue(self):
        data = report([record()])
        old = datetime(2026, 10, 14, 4, 8, tzinfo=timezone.utc)
        future = datetime(2026, 10, 14, 4, 11, tzinfo=timezone.utc)
        for value in (old, future):
            with self.subTest(time=value):
                data["observed_at"] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
                markdown, code = self.render(data)
                self.assertEqual(code, 1)
                self.assertIn("Geen aantallen gepubliceerd", markdown)
                self.assertNotIn("Run 71", markdown)

    def test_exact_freshness_window_and_clock_identity(self):
        data = report([record()])
        data["observed_at"] = "2026-10-14T04:10:00Z"
        for observed_now, expected in (
            (datetime(2026, 10, 14, 4, 10, tzinfo=timezone.utc), 0),
            (datetime(2026, 10, 14, 4, 11, tzinfo=timezone.utc), 0),
            (datetime(2026, 10, 14, 4, 11, 0, 1, tzinfo=timezone.utc), 1),
            (datetime(2026, 10, 14, 4, 9, 59, tzinfo=timezone.utc), 1),
            (datetime(2026, 10, 14, 4, 10), 1),
        ):
            with self.subTest(observed_now=observed_now):
                markdown, status = summary.safe_summary(
                    json.dumps(data).encode("utf-8"), 0, now=observed_now)
                self.assertEqual(status, expected)
                if expected:
                    self.assertNotIn("Run 71", markdown)

    def test_forged_mutation_authorization_and_wrong_reason_denied(self):
        for key, value in (
            ("mutation_authorized", True),
            ("mutation_authorized", None),
            ("reason", "run_cancellation_approved"),
            ("reason", None),
        ):
            with self.subTest(key=key, value=value):
                data = report([record()])
                data[key] = value
                markdown, status = self.render(data)
                self.assertEqual(status, 1)
                self.assertNotIn("Run 71", markdown)

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
        self.assertIn("FTMO-onderzoeksroute", markdown)
        self.assertIn("geen jobgegevens", markdown)
        self.assertIn("zCloud-VPS-route", markdown)
        self.assertNotIn("runner is offline", markdown)

    def test_actionable_guidance_is_specific_to_verified_run_route(self):
        cases = [
            ([record(71, scope="current_main", route="zcloud_vps")],
             "zCloud-runnerlabels en repositorytoegang",
             "leid hieruit geen zCloud-VPS-storing af"),
            ([record(72, route="ftmo_research")],
             "FTMO-runnerroute",
             "zCloud-runnerlabels en repositorytoegang"),
            ([record(73, route="no_job_records", assignment="no_job_records",
                     state="no_job_records", jobs=0)],
             "workflowvoorwaarden",
             "FTMO-runnerroute"),
        ]
        for rows, included, excluded in cases:
            with self.subTest(rows=rows):
                markdown, status = self.render(report(rows))
                self.assertEqual(status, 0)
                self.assertIn(included, markdown)
                self.assertNotIn(excluded, markdown)
                self.assertIn("Geen bewijs van productiestatus", markdown)
                self.assertNotIn("restart", markdown.lower())
                self.assertNotIn("cancel", markdown.lower())

    def test_dutch_job_phase_is_precise_and_read_only(self):
        phases = [
            ("queued", "wachtend"),
            ("in_progress", "bezig met uitvoeren"),
            ("completed", "al afgerond"),
            ("waiting", "wacht op volgende stap"),
            ("requested", "uitvoering aangevraagd"),
            ("pending", "in behandeling"),
            ("mixed_job_states", "jobs in verschillende fasen"),
        ]
        for phase, dutch in phases:
            with self.subTest(phase=phase):
                data = report([record(state=phase, scope="current_main",
                                      route="zcloud_vps")])
                output, code = self.render(data)
                self.assertEqual(code, 0)
                self.assertIn(dutch, output)
                self.assertIn("Geen bevoegdheid", output)
                if phase != "queued":
                    self.assertNotIn("zCloud-runnerlabels en repositorytoegang", output)

    def test_finished_or_waiting_ftmo_job_cannot_trigger_runner_queue_advice(self):
        for phase in ("completed", "in_progress", "requested", "waiting",
                      "pending", "mixed_job_states"):
            with self.subTest(phase=phase):
                payload = report([record(state=phase)])
                output, status = self.render(payload)
                self.assertEqual(status, 0)
                self.assertNotIn("onderzoek afzonderlijk de FTMO-runnerroute", output)
                self.assertNotIn("Geen jobgegevens", output)
                self.assertIn("Geen bewijs van productiestatus", output)

    def test_runner_pressure_counts_only_still_queued_unassigned_jobs(self):
        rows = [
            record(71, route="ftmo_research", state="completed"),
            record(72, route="zcloud_vps", state="in_progress"),
            record(73, route="ftmo_research", state="waiting"),
            record(74, route="ftmo_research", state="queued"),
            record(75, route="zcloud_vps", state="queued",
                   assignment="assigned"),
        ]
        output, status = self.render(report(rows))
        self.assertEqual(status, 0)
        self.assertIn("**1 wachtende runs zonder runnertoewijzing**", output)
        self.assertIn("FTMO-runnerroute", output)
        self.assertNotIn("zCloud-runnerlabels en repositorytoegang", output)
        self.assertIn("Geen bewijs van productiestatus", output)

    def test_multi_job_run_is_one_run_not_three_runner_incidents(self):
        payload = report([record(111, scope="current_main",
                                 route="zcloud_vps",
                                 assignment="not_assigned",
                                 state="queued", jobs=3)])
        output, code = self.render(payload)
        self.assertEqual(code, 0)
        self.assertIn("**1 wachtende runs zonder runnertoewijzing**", output)
        self.assertNotIn("**3 wachtende runs zonder runnertoewijzing**", output)
        self.assertIn("Run 111", output)
        self.assertIn("Geen bewijs van productiestatus", output)

    def test_terminal_job_without_runner_does_not_claim_future_assignment(self):
        # GitHub can return completed/skipped evidence with no runner assigned.
        # The human output must not imply an active runner-capacity incident.
        payload = report([record(88, scope="current_main", route="ftmo_research",
                                 state="completed", assignment="not_assigned")])
        markdown, status = self.render(payload)
        self.assertEqual(status, 0)
        self.assertIn("al afgerond", markdown)
        self.assertIn("geen runner geregistreerd", markdown)
        self.assertNotIn("nog geen runner toegewezen", markdown)
        self.assertIn("**0 wachtende runs zonder runnertoewijzing**", markdown)
        self.assertNotIn("onderzoek afzonderlijk de FTMO-runnerroute", markdown)
        self.assertIn("Geen bewijs van productiestatus", markdown)

    def test_unknown_job_phase_does_not_print_any_run_link(self):
        data = report([record(state="PRIVATE_UNTRUSTED_STATUS")])
        output, status = self.render(data)
        self.assertEqual(status, 1)
        self.assertNotIn("PRIVATE_UNTRUSTED_STATUS", output)
        self.assertNotIn("Run 71", output)

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
        self.assertIn("**3 wachtende runs zonder runnertoewijzing**", markdown)
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

    def test_oversized_run_ids_never_generate_operator_links(self):
        for oversized in ((1 << 63), (1 << 128), (1 << 256)):
            with self.subTest(run_id=oversized):
                data = report([record(oversized)])
                output, status = self.render(data)
                self.assertEqual(status, 1)
                self.assertIn("Geen aantallen gepubliceerd", output)
                self.assertNotIn("https://github.com/Zennay/zCloud/actions/runs/", output)
        max_id = (1 << 63) - 1
        markdown, code = self.render(report([record(max_id)]))
        self.assertEqual(code, 0)
        self.assertIn(f"/runs/{max_id}", markdown)

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

    def test_bucket_counts_must_be_real_positive_integers_not_json_booleans(self):
        for invalid in (True, False, 1.0, 1.5, 0, -1, None, "1", [], {}):
            with self.subTest(invalid=invalid):
                data = report([record(71)])
                bucket = next(iter(data["route_counts"]))
                data["route_counts"][bucket] = invalid
                rendered, status = self.render(data)
                self.assertEqual(status, 1)
                self.assertIn("Geen aantallen gepubliceerd", rendered)
                self.assertNotIn("Run 71", rendered)

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

    def test_contradictory_route_assignment_and_status_are_rejected(self):
        for item in (
            record(route="ftmo_research", assignment="no_job_records"),
            record(route="ftmo_research", state="no_job_records"),
            record(route="no_job_records", assignment="not_assigned",
                   state="no_job_records", jobs=0),
            record(route="no_job_records", assignment="no_job_records",
                   state="queued", jobs=0),
            record(route="zcloud_vps", assignment="no_job_records",
                   state="mixed_or_active"),
        ):
            with self.subTest(row=item):
                markdown, status = self.render(report([item]))
                self.assertEqual(status, 1)
                self.assertIn("Geen aantallen gepubliceerd", markdown)
                self.assertNotIn("Run 71", markdown)

    def test_noncanonical_impossible_and_unparsed_utc_clock_denied(self):
        for when in (
            "PRIVATE_SECRETZ", "2026-02-30T04:10:00Z",
            "2026-10-14T25:10:00Z", "2026-1-14T04:10:00Z",
            "2026-10-14T04:10:00.999Z", "2026-10-14T04:10:00ZZ",
            "2026-10-14T04:10:00+00:00",
        ):
            with self.subTest(observed_at=when):
                data = report([record()])
                data["observed_at"] = when
                markdown, code = self.render(data)
                self.assertEqual(code, 1)
                self.assertNotIn("PRIVATE_SECRET", markdown)
                self.assertNotIn("Run 71", markdown)

    def test_main_sha_drift_and_missing_identity_deny_all_route_counts(self):
        for field, value in (
            ("main_sha_after", "b" * 40),
            ("main_sha_before", True),
            ("main_sha_before", "ABC" * 13 + "X"),
            ("main_sha_before", ""),
            ("observed_at", "2026-10-14T04:10:00+00:00"),
            ("observed_at", None),
        ):
            with self.subTest(field=field):
                data = report([record()])
                data[field] = value
                markdown, code = self.render(data)
                self.assertEqual(code, 1)
                self.assertIn("Geen aantallen gepubliceerd", markdown)
                self.assertNotIn("Run 71", markdown)

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
            data["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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

    def test_cli_replayed_observation_never_publishes_old_run_links(self):
        for when, authorized in (
            ("2025-01-01T00:00:00Z", False),
            ("2099-01-01T00:00:00Z", False),
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), True),
        ):
            with self.subTest(observed_at=when, authorized=authorized):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "data.json"
                    summary_path = Path(tmp) / "github-step-summary.md"
                    payload = report([record()])
                    payload["observed_at"] = when
                    payload["mutation_authorized"] = authorized
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(CLI), "--report", str(path),
                         "--collector-exit", "0", "--summary-path", str(summary_path)],
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn("Run 71", summary_path.read_text(encoding="utf-8"))
                    self.assertNotIn("Traceback", result.stderr)

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
