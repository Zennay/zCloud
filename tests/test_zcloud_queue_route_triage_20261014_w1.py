"""Offline regression matrix for independent GET-only zCloud runner-route triage."""
import copy
from datetime import datetime, timezone
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_queue_route_triage_20261014_w1 as triage
from scripts import zcloud_queue_route_summary_20261014_w1 as summary
from scripts import zcloud_watchdog_queue_collector_20261014_w1 as source

SHA = "a" * 40
OLD = "b" * 40
NOW = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
MAIN = "/repos/Zennay/zCloud/branches/main"
RUNS = "/repos/Zennay/zCloud/actions/runs?status=queued&per_page=100&page=1"
ROOT = "/repos/Zennay/zCloud"


def run(number, *, branch="main", sha=SHA, repo="Zennay/zCloud"):
    return {
        "id": number, "workflow_id": 7, "run_attempt": 1,
        "event": "workflow_run", "status": "queued",
        "head_sha": sha, "head_branch": branch,
        "head_repository": {"full_name": repo},
    }


def job(number, *, route="ftmo-research", state="queued"):
    return {
        "id": number + 1000, "run_id": number,
        "run_attempt": 1, "status": state,
        "head_sha": SHA, "head_branch": "main",
        "labels": ["self-hosted", route] if route != "zcloud_vps"
                  else ["self-hosted", "zcloud", "vps"],
        "runner_id": 0, "runner_name": "",
    }


def mock(records, by_run=None):
    by_run = by_run or {}
    calls = []
    def get(path):
        calls.append(path)
        if path == MAIN:
            return {"commit": {"sha": SHA}}, ""
        if path == RUNS:
            return {"total_count": len(records), "workflow_runs": copy.deepcopy(records)}, ""
        if path.endswith("/jobs?per_page=100&page=1"):
            rid = int(path.split("/runs/")[1].split("/")[0])
            group = copy.deepcopy(by_run.get(rid, []))
            return {"total_count": len(group), "jobs": group}, ""
        raise AssertionError("Unexpected GET: " + path)
    return get, calls


class QueueRouteTriageTests(unittest.TestCase):
    def test_empty_queue_is_observation_not_authority(self):
        get, calls = mock([])
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["decision"], "OBSERVED")
        self.assertEqual(result["observed_runs"], 0)
        self.assertEqual(result["route_counts"], {})
        self.assertEqual(calls.count(MAIN), 2)
        self.assertEqual(calls.count(RUNS), 2)
        self.assertFalse(result["runner_recovery_authorized"])
        self.assertIs(result["mutation_authorized"], False)
        self.assertEqual(result["cancel_run_ids"], [])
        self.assertFalse(result["production_verified"])

    def test_live_shaped_mixed_queue_separates_missing_job_records(self):
        rows = [run(1), run(2, sha=OLD), run(3, branch="diagnostic/SECRET", sha=OLD)]
        jobs = {
            1: [],
            2: [dict(job(2), head_sha=OLD)],
            3: [dict(job(3), head_sha=OLD, head_branch="diagnostic/SECRET")],
        }
        get, calls = mock(rows, jobs)
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["observed_runs"], 3)
        by_id = {row["run_id"]: row for row in result["runs"]}
        self.assertEqual((by_id[1]["scope"], by_id[1]["route"]),
                         ("current_main", "no_job_records"))
        self.assertEqual((by_id[2]["scope"], by_id[2]["route"]),
                         ("historical_main", "ftmo_research"))
        self.assertEqual((by_id[3]["scope"], by_id[3]["route"]),
                         ("other_branch", "ftmo_research"))
        self.assertEqual(by_id[2]["assignment"], "not_assigned")
        self.assertEqual(sum(result["route_counts"].values()), 3)
        self.assertEqual(sum("/jobs?" in path for path in calls), 6)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertFalse(result["worker_generation_verified"])

    def test_exact_eight_queued_run_shapes_remain_separate_and_non_authorizing(self):
        """Regression for the 14 October GET-only eight-run incident census."""
        ftmo_ids = [38444164413, 38417002886, 38410397682]
        nojob_ids = [37655509528, 37655509566, 37642380865,
                     37642380864, 37642380862]
        entries = [
            run(ftmo_ids[0], branch="fix/force-deploy-verified-live-sha-20261013", sha=OLD),
            run(ftmo_ids[1], branch="diagnostic/physics-20261013", sha=OLD),
            run(ftmo_ids[2], sha=OLD),
        ] + [run(number, sha=OLD) for number in nojob_ids]
        evidence = {}
        for entry in entries[:3]:
            number = entry["id"]
            j = job(number)
            j["head_sha"] = entry["head_sha"]
            j["head_branch"] = entry["head_branch"]
            evidence[number] = [j]
        get, calls = mock(entries, evidence)
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["decision"], "OBSERVED")
        self.assertEqual(result["observed_runs"], 8)
        self.assertEqual(sum(row["route"] == "ftmo_research" for row in result["runs"]), 3)
        self.assertEqual(sum(row["assignment"] == "not_assigned" for row in result["runs"]), 3)
        self.assertEqual(sum(row["route"] == "no_job_records" for row in result["runs"]), 5)
        self.assertEqual(sum(row["scope"] == "current_main" for row in result["runs"]), 0)
        self.assertEqual(sum("/jobs?" in path for path in calls), 16)
        self.assertEqual(result["cancel_run_ids"], [])
        self.assertFalse(result["runner_recovery_authorized"])
        self.assertFalse(result["worker_generation_verified"])

    def test_queue_filter_does_not_accept_a_completed_run(self):
        item = run(1)
        item["status"] = "completed"
        get, calls = mock([item], {1: [job(1)]})
        with self.assertRaisesRegex(source.InventoryError, "nonqueued_run"):
            triage.collect(get, clock=NOW)
        self.assertFalse(any("/jobs?" in path for path in calls))

    def test_job_state_enum_rejects_untrusted_github_status(self):
        item = job(1)
        item["status"] = "PRIVATE_SECRET"
        get, _ = mock([run(1)], {1: [item]})
        with self.assertRaisesRegex(source.InventoryError, "unknown_queue_job_state"):
            triage.collect(get, clock=NOW)

    def test_cross_run_duplicate_job_id_fails_closed(self):
        first = job(1)
        second = job(2)
        second["id"] = first["id"]
        get, calls = mock([run(1), run(2)], {1: [first], 2: [second]})
        with self.assertRaisesRegex(source.InventoryError, "job_identity_reused_across_runs"):
            triage.collect(get, clock=NOW)
        # Abort before repeating the snapshot or presenting a mixed diagnosis.
        self.assertEqual(calls.count(RUNS), 1)

    def test_current_main_zcloud_vps_job_is_still_not_runner_recovery(self):
        get, _ = mock([run(1)], {1: [job(1, route="zcloud_vps")]})
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["runs"][0]["route"], "zcloud_vps")
        self.assertEqual(result["runs"][0]["assignment"], "not_assigned")
        self.assertEqual(result["runs"][0]["scope"], "current_main")
        self.assertFalse(result["runner_recovery_authorized"])
        self.assertEqual(result["cancel_run_ids"], [])

    def test_second_pass_started_job_denied_not_summarized(self):
        get, _ = mock([run(1)], {1: [job(1, route="zcloud_vps")]})
        job_reads = [0]
        def racing(path):
            if "/jobs?" in path:
                job_reads[0] += 1
                if job_reads[0] == 2:
                    return {"total_count": 1, "jobs": [job(1, route="zcloud_vps", state="in_progress")]}, ""
            return get(path)
        with self.assertRaisesRegex(source.InventoryError, "job_state_changed"):
            triage.collect(racing, clock=NOW)

    def test_changed_runner_id_or_name_denied(self):
        for key, value in (("runner_id", 31), ("runner_name", "runner-SECRET")):
            with self.subTest(field=key):
                get, _ = mock([run(1)], {1: [job(1)]})
                reads = [0]
                def racing(path):
                    if "/jobs?" in path:
                        reads[0] += 1
                        if reads[0] == 2:
                            item = job(1)
                            item[key] = value
                            return {"total_count": 1, "jobs": [item]}, ""
                    return get(path)
                with self.assertRaisesRegex(source.InventoryError, "job_state_changed"):
                    triage.collect(racing, clock=NOW)

    def test_precise_job_states_never_infer_all_jobs_queued(self):
        for state in ("queued", "in_progress", "completed", "waiting",
                      "requested", "pending"):
            with self.subTest(status=state):
                get, _ = mock([run(1)], {1: [job(1, route="zcloud_vps", state=state)]})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["job_state"], state)
                self.assertEqual(result["runs"][0]["route"], "zcloud_vps")
                self.assertIs(result["mutation_authorized"], False)
                self.assertEqual(result["cancel_run_ids"], [])

    def test_mixed_job_states_are_not_misreported_as_only_queued(self):
        for other in ("completed", "in_progress", "waiting", "requested", "pending"):
            with self.subTest(other=other):
                jobs = [job(1, route="zcloud_vps"),
                        dict(job(1, route="zcloud_vps", state=other), id=2001)]
                get, _ = mock([run(1)], {1: jobs})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["job_state"], "mixed_job_states")
                self.assertEqual(result["runs"][0]["job_count"], 2)
                self.assertFalse(result["runner_recovery_authorized"])

    def test_queued_run_census_changed_denied(self):
        get, _ = mock([run(1)], {1: [job(1)]})
        hits = [0]
        def racing(path):
            if path == RUNS:
                hits[0] += 1
                if hits[0] == 2:
                    return {"total_count": 1, "workflow_runs": [run(2)]}, ""
            return get(path)
        with self.assertRaisesRegex(source.InventoryError, "queue_changed"):
            triage.collect(racing, clock=NOW)

    def test_main_sha_moves_denied(self):
        get, _ = mock([run(1)], {1: [job(1)]})
        hits = [0]
        def racing(path):
            if path == MAIN:
                hits[0] += 1
                if hits[0] == 2:
                    return {"commit": {"sha": OLD}}, ""
            return get(path)
        with self.assertRaisesRegex(source.InventoryError, "main_moved"):
            triage.collect(racing, clock=NOW)

    def test_forced_stale_snapshot_denied(self):
        get, _ = mock([])
        ticks = iter((1.0, 47.0))
        with self.assertRaisesRegex(source.InventoryError, "triage_stale"):
            triage.collect(get, clock=NOW, monotonic_clock=lambda: next(ticks))

    def test_too_many_runs_denied_before_fetching_any_jobs(self):
        rows = [run(i) for i in range(1, 42)]
        get, calls = mock(rows)
        with self.assertRaises(source.InventoryError):
            triage.collect(get, clock=NOW)
        self.assertFalse(any("/jobs?" in call for call in calls))

    def test_forged_job_parent_or_attempt_denied(self):
        for field, value in (("run_id", 10), ("run_attempt", True),
                             ("head_sha", OLD), ("head_branch", "main-secret")):
            with self.subTest(field=field):
                item = job(1)
                item[field] = value
                get, _ = mock([run(1)], {1: [item]})
                with self.assertRaises(source.InventoryError):
                    triage.collect(get, clock=NOW)

    def test_direct_assignment_helper_cannot_coerce_boolean_or_float_zero(self):
        # Defensive type boundary, even if the helper is ever reused before
        # the full GitHub job parser performs its strict schema check.
        for forged_id in (False, True, 0.0, "0"):
            for empty_name in ("", None):
                with self.subTest(runner_id=forged_id, name=empty_name):
                    self.assertEqual(
                        triage._assignment([{
                            "runner_id": forged_id, "runner_name": empty_name,
                        }]), "indeterminate")

    def test_null_and_zero_runner_id_with_empty_or_null_name_are_unassigned(self):
        # GitHub REST jobs can represent an unassigned runner by JSON null,
        # without proving that any particular listener is offline.
        for runner_id, runner_name in (
            (0, ""), (0, None), (None, ""), (None, None)
        ):
            with self.subTest(runner_id=runner_id, runner_name=runner_name):
                item = job(1, route="zcloud_vps")
                item["runner_id"] = runner_id
                item["runner_name"] = runner_name
                get, _ = mock([run(1)], {1: [item]})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["assignment"], "not_assigned")
                self.assertEqual(result["runs"][0]["job_state"], "queued")
                self.assertFalse(result["runner_recovery_authorized"])
                self.assertIs(result["mutation_authorized"], False)

    def test_incomplete_or_contradictory_runner_evidence_is_never_assigned(self):
        for runner_id, runner_name, expected in (
            (23, None, "indeterminate"),
            (23, "", "indeterminate"),
            (0, "named-runner", "indeterminate"),
            (None, "named-runner", "indeterminate"),
            (23, "named-runner", "assigned"),
        ):
            with self.subTest(runner_id=runner_id, runner_name=runner_name):
                item = job(1)
                item["runner_id"] = runner_id
                item["runner_name"] = runner_name
                get, _ = mock([run(1)], {1: [item]})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["assignment"], expected)
                self.assertEqual(result["cancel_run_ids"], [])

    def test_null_runner_end_to_end_summary_has_no_private_name_or_false_repair(self):
        observed_job = job(1, route="ftmo-research")
        observed_job["runner_id"] = None
        observed_job["runner_name"] = None
        observed_job["name"] = "PRIVATE_JOB_NAME"
        get, _ = mock([run(1)], {1: [observed_job]})
        receipt = triage.collect(get, clock=NOW)
        rendered, status = summary.safe_summary(
            json.dumps(receipt).encode("utf-8"), 0, now=NOW)
        self.assertEqual(status, 0)
        self.assertIn("**1 wachtende runs zonder runnertoewijzing**", rendered)
        self.assertIn("FTMO-runnerroute", rendered)
        self.assertIn("geen zCloud-VPS-storing", rendered)
        self.assertNotIn("PRIVATE_JOB_NAME", rendered)
        self.assertIn("Geen bewijs van productiestatus", rendered)

    def test_missing_or_invalid_runner_fields_never_become_bogus_unassigned(self):
        for change in ("missing_name", "missing_id", "boolean_name",
                       "list_name", "long_name", "negative_runner_id"):
            with self.subTest(case=change):
                item = job(1)
                if change == "missing_name":
                    del item["runner_name"]
                elif change == "missing_id":
                    del item["runner_id"]
                elif change == "boolean_name":
                    item["runner_name"] = True
                elif change == "list_name":
                    item["runner_name"] = ["runner"]
                elif change == "long_name":
                    item["runner_name"] = "r" * 129
                elif change == "negative_runner_id":
                    item["runner_id"] = -1
                get, _ = mock([run(1)], {1: [item]})
                with self.assertRaises(source.InventoryError):
                    triage.collect(get, clock=NOW)

    def test_whitespace_runner_name_does_not_imply_known_runner_assignment(self):
        for blank in (" ", "   ", "\u2003", "\u00a0"):
            with self.subTest(name=repr(blank)):
                entry = job(1)
                entry["runner_id"] = 73
                entry["runner_name"] = blank
                # Direct semantic classifier is conservative for every
                # whitespace-only name. Some Unicode whitespace is additionally
                # unprintable and is denied at the GitHub schema boundary.
                self.assertEqual(triage._assignment([entry]), "indeterminate")
                get, _ = mock([run(1)], {1: [entry]})
                if blank.isprintable():
                    result = triage.collect(get, clock=NOW)
                    self.assertEqual(result["runs"][0]["assignment"], "indeterminate")
                    self.assertIs(result["mutation_authorized"], False)
                    self.assertEqual(result["cancel_run_ids"], [])
                else:
                    with self.assertRaisesRegex(source.InventoryError, "invalid_runner_evidence"):
                        triage.collect(get, clock=NOW)

    def test_runner_name_control_characters_fail_closed_before_second_read(self):
        for bad in ("runner\nNEXT", "runner\rRETURN", "runner\tTAB",
                    "name\x00NUL", "runner\x7fDEL"):
            with self.subTest(bad=repr(bad)):
                entry = job(1)
                entry["runner_id"] = 19
                entry["runner_name"] = bad
                get, calls = mock([run(1)], {1: [entry]})
                with self.assertRaisesRegex(source.InventoryError, "invalid_runner_evidence"):
                    triage.collect(get, clock=NOW)
                self.assertEqual(calls.count(RUNS), 1)

    def test_change_from_null_to_assigned_between_reads_denies(self):
        item = job(1)
        item["runner_id"] = None
        item["runner_name"] = None
        get, _ = mock([run(1)], {1: [item]})
        reads = [0]
        def racing(path):
            if "/jobs?" in path:
                reads[0] += 1
                if reads[0] == 2:
                    update = dict(item, runner_id=12, runner_name="new-runner")
                    return {"total_count": 1, "jobs": [update]}, ""
            return get(path)
        with self.assertRaisesRegex(source.InventoryError, "job_state_changed"):
            triage.collect(racing, clock=NOW)

    def test_run_id_outside_github_integer_range_denied_before_jobs_get(self):
        oversize = (1 << 63)
        get, calls = mock([run(oversize)])
        with self.assertRaisesRegex(source.InventoryError, "oversized_run_identity"):
            triage.collect(get, clock=NOW)
        self.assertFalse(any("/jobs?" in path for path in calls))
        self.assertEqual(calls.count(RUNS), 1)

    def test_job_id_outside_github_integer_range_denied_before_second_pass(self):
        oversized_job = job(1)
        oversized_job["id"] = 1 << 63
        get, calls = mock([run(1)], {1: [oversized_job]})
        with self.assertRaisesRegex(source.InventoryError, "oversized_job_identity"):
            triage.collect(get, clock=NOW)
        self.assertEqual(calls.count(RUNS), 1)
        self.assertEqual(sum("/jobs?" in path for path in calls), 1)

    def test_maximum_signed_github_id_is_bounded_but_not_invented_as_liveness(self):
        max_id = (1 << 63) - 1
        observed = job(max_id)
        observed["id"] = max_id
        get, _ = mock([run(max_id)], {max_id: [observed]})
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["runs"][0]["run_id"], max_id)
        self.assertIs(result["mutation_authorized"], False)
        self.assertEqual(result["cancel_run_ids"], [])

    def test_runner_id_above_signed_boundary_is_not_a_known_assignment(self):
        forged = 1 << 63
        self.assertEqual(
            triage._assignment([{"runner_id": forged, "runner_name": "runner"}]),
            "indeterminate",
        )
        entry = job(1)
        entry["runner_id"] = forged
        entry["runner_name"] = "runner"
        get, calls = mock([run(1)], {1: [entry]})
        with self.assertRaisesRegex(source.InventoryError, "invalid_runner_evidence"):
            triage.collect(get, clock=NOW)
        self.assertEqual(calls.count(RUNS), 1)

    def test_nonprinting_unicode_runner_name_cannot_forge_assignment(self):
        for name in ("runner\u200bghost", "runner\u202eoverride", "\ufeff"):
            with self.subTest(name=repr(name)):
                self.assertEqual(
                    triage._assignment([{"runner_id": 45, "runner_name": name}]),
                    "indeterminate",
                )
                entry = job(1)
                entry["runner_id"] = 45
                entry["runner_name"] = name
                get, _ = mock([run(1)], {1: [entry]})
                with self.assertRaisesRegex(source.InventoryError, "invalid_runner_evidence"):
                    triage.collect(get, clock=NOW)

    def test_legitimate_unicode_runner_name_remains_assigned_but_not_verified(self):
        entry = job(1)
        entry["runner_id"] = 45
        entry["runner_name"] = "Haarlem-één"
        get, _ = mock([run(1)], {1: [entry]})
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["runs"][0]["assignment"], "assigned")
        self.assertIs(result["mutation_authorized"], False)
        self.assertIs(result["worker_generation_verified"], False)
        self.assertNotIn("Haarlem-één", json.dumps(result, ensure_ascii=False))

    def test_boolean_runner_assignment_denied(self):
        item = job(1)
        item["runner_id"] = True
        get, _ = mock([run(1)], {1: [item]})
        with self.assertRaises(source.InventoryError):
            triage.collect(get, clock=NOW)

    def test_mixed_route_jobs_are_not_confused_as_ftmo(self):
        get, _ = mock([run(1)], {1: [job(1, route="zcloud_vps"),
                                           dict(job(1), id=1002)]})
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["runs"][0]["route"], "mixed_job_routes")
        self.assertEqual(result["runs"][0]["job_count"], 2)

    def test_only_unambiguous_hosted_image_label_is_classified_hosted(self):
        candidates = [
            (["ubuntu-latest"], "github_hosted"),
            (["windows-2022"], "github_hosted"),
            ([], "other_or_mixed"),
            (["undocumented-custom-image"], "other_or_mixed"),
            (["ubuntu-latest", "private-runner"], "other_or_mixed"),
            (["zcloud", "vps"], "other_or_mixed"),
        ]
        for index, (labels, expected) in enumerate(candidates, start=1):
            with self.subTest(labels=labels):
                item = job(index)
                item["labels"] = labels
                get, _ = mock([run(index)], {index: [item]})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["route"], expected)
                self.assertFalse(result["runner_recovery_authorized"])

    def test_exact_self_hosted_pool_labels_not_broadened_by_private_extra(self):
        candidates = [
            (["self-hosted", "zcloud", "vps"], "zcloud_vps"),
            (["self-hosted", "zcloud", "vps", "Linux", "X64"], "zcloud_vps"),
            (["self-hosted", "ftmo-research", "Linux", "ARM64"], "ftmo_research"),
            (["self-hosted", "zcloud", "vps", "ftmo-research"], "other_or_mixed"),
            (["self-hosted", "zcloud", "vps", "secret-runner"], "other_or_mixed"),
            (["self-hosted", "ftmo-research", "private-gpu"], "other_or_mixed"),
            (["self-hosted", "ftmo-research", "ftmo-research"], "other_or_mixed"),
        ]
        for n, (labels, expected) in enumerate(candidates, start=1):
            with self.subTest(labels=labels):
                item = job(n)
                item["labels"] = labels
                get, _ = mock([run(n)], {n: [item]})
                result = triage.collect(get, clock=NOW)
                self.assertEqual(result["runs"][0]["route"], expected)
                self.assertIs(result["mutation_authorized"], False)

    def test_job_with_positive_assigned_runner_keeps_uncertain_liveness(self):
        item = job(1)
        item["runner_id"] = 32
        item["runner_name"] = "some-runner"
        get, _ = mock([run(1)], {1: [item]})
        result = triage.collect(get, clock=NOW)
        self.assertEqual(result["runs"][0]["assignment"], "assigned")
        self.assertFalse(result["production_verified"])
        self.assertFalse(result["worker_generation_verified"])

    def test_invalid_label_length_denied_before_render(self):
        item = job(1)
        item["labels"] = ["self-hosted", "PRIVATE" * 100]
        get, _ = mock([run(1)], {1: [item]})
        with self.assertRaisesRegex(source.InventoryError, "invalid_runner_evidence"):
            triage.collect(get, clock=NOW)

    def test_api_cli_missing_readonly_token_denies_without_raw_error(self):
        entry = Path(__file__).resolve().parents[1] / "scripts/zcloud_queue_route_triage_20261014_w1.py"
        import os
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        result = subprocess.run([sys.executable, str(entry)], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["decision"], "DENY")
        self.assertIs(report["mutation_authorized"], False)
        self.assertEqual(report["cancel_run_ids"], [])
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
