"""Offline real-module tests for read-only worker-generation evidence."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

from scripts import zcloud_worker_generation_evidence_20261013_w1 as subject


NOW = datetime(2026, 10, 13, 18, 30, tzinfo=timezone.utc)


def targets(*items):
    projects = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("worker_key")
        if not isinstance(key, str) or "::w" not in key:
            continue
        project, slot = key.rsplit("::w", 1)
        try:
            local_slot = int(slot)
        except ValueError:
            continue
        projects[key] = {
            "project_id": key, "base_project_id": project,
            "worker_slot": local_slot,
            "global_worker_slot": item.get("global_worker_slot"),
            "provider": item.get("provider", "chatgpt"),
            "active": True, "assignment_ready": True,
            "desired_state": "running",
            "queue_item": {"queue_id": item.get("queue_id"), "project_id": project,
                           "worker_slot": item.get("global_worker_slot"),
                           "status": "claimed", "eligible": True,
                           "claimed_at": (NOW - timedelta(minutes=5)).isoformat(),
                           "claim_expires": (NOW + timedelta(minutes=5)).isoformat()},
        }
    return {"projects": projects, "global_allocation": {"workers": list(items)}}


def allocation(name="cloud::w1", provider="chatgpt"):
    project, slot = name.rsplit("::w", 1)
    return {"worker_key": name, "project_id": project, "worker_slot": int(slot),
            "global_worker_slot": int(slot), "queue_id": "qid-" + name, "provider": provider}


def row(name="cloud::w1", verdict="generating", **changes):
    project, slot = name.rsplit("::w", 1)
    return {
        "worker": name, "project": project, "slot": int(slot),
        "global_slot": int(slot), "queue_id": "qid-" + name, "provider": "chatgpt",
        "verdict": verdict, "last_event": "heartbeat", "last_event_age_s": 8,
        "desired_state": "running",
        "last_prompt_age_s": 10,
        **changes,
    }


def debug(*rows, at=NOW):
    return {"time": at.isoformat(), "workers": list(rows)}


class GenerationEvidenceTests(unittest.TestCase):
    def classify(self, t=None, d=None, **kwargs):
        return subject.classify(
            targets(allocation()) if t is None else t,
            debug(row()) if d is None else d,
            at=NOW, **kwargs,
        )

    def denied(self, t=None, d=None, reason=None):
        with self.assertRaises(subject.EvidenceDenied) as error:
            self.classify(t=t, d=d)
        if reason:
            self.assertEqual(reason, str(error.exception))

    def test_fresh_generation_is_reported_only_as_browser_telemetry(self):
        report = self.classify()
        self.assertTrue(report["all_allocated_chatgpt_generating"])
        self.assertFalse(report["control_authorized"])
        self.assertFalse(report["tab_presence_verified"])
        self.assertFalse(report["generation_directly_verified"])
        self.assertEqual([{"worker": "cloud::w1", "state": "generating_observed",
                           "generation_evidence": True, "progress_evidence": False}], report["workers"])

    def test_heartbeat_generating_is_not_evidence_of_new_answer_text(self):
        report = self.classify(require_progress=True)
        self.assertTrue(report["all_allocated_chatgpt_generating"])
        self.assertFalse(report["all_allocated_chatgpt_progressing"])
        self.assertFalse(report["workers"][0]["progress_evidence"])
        self.assertTrue(report["progress_policy_requested"])

    def test_fresh_generation_progress_event_is_both_evidence_types(self):
        report = self.classify(d=debug(row(last_event="generation-progress", last_event_age_s=12)),
                               require_progress=True)
        self.assertTrue(report["all_allocated_chatgpt_generating"])
        self.assertTrue(report["all_allocated_chatgpt_progressing"])
        self.assertTrue(report["workers"][0]["progress_evidence"])

    def test_old_progress_event_can_still_be_generating_but_not_progressing(self):
        report = self.classify(d=debug(row(last_event="generation-progress",
                                           last_event_age_s=subject.MAX_PROGRESS_AGE_SECONDS + 1)),
                               require_progress=True)
        self.assertTrue(report["all_allocated_chatgpt_generating"])
        self.assertFalse(report["all_allocated_chatgpt_progressing"])

    def test_strict_progress_requires_every_allocated_chatgpt_worker(self):
        r = self.classify(
            t=targets(allocation(), allocation("haxlab::w1")),
            d=debug(row(last_event="generation-progress"), row("haxlab::w1")),
            require_progress=True,
        )
        self.assertTrue(r["all_allocated_chatgpt_generating"])
        self.assertFalse(r["all_allocated_chatgpt_progressing"])
        self.assertEqual([True, False], [x["progress_evidence"] for x in r["workers"]])

    def test_progress_must_be_boolean_policy(self):
        for invalid in ("true", 1, None, [], -1):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    subject.EvidenceDenied, "invalid_progress_policy"):
                self.classify(require_progress=invalid)

    def test_prompt_sent_is_not_generation(self):
        result = self.classify(d=debug(row(verdict="prompt-sent")))
        self.assertFalse(result["all_allocated_chatgpt_generating"])
        self.assertEqual("prompt_sent_without_generation", result["workers"][0]["state"])

    def test_runtime_gates_generation_on_assignment_and_pause(self):
        for changes, expected in [
            ({"active": False, "desired_state": "paused"}, "inactive_target"),
            ({"assignment_ready": False}, "assignment_unready"),
        ]:
            with self.subTest(changes=changes):
                t = targets(allocation())
                t["projects"]["cloud::w1"].update(changes)
                output = self.classify(t=t)
                self.assertFalse(output["all_allocated_chatgpt_generating"])
                self.assertEqual(expected, output["workers"][0]["state"])

    def test_runtime_claim_and_identity_mismatch_denied(self):
        t = targets(allocation())
        t["projects"]["cloud::w1"]["queue_item"]["queue_id"] = "other"
        self.denied(t=t, reason="target_queue_claim_mismatch")
        t = targets(allocation())
        t["projects"]["cloud::w1"]["global_worker_slot"] = 2
        self.denied(t=t, reason="target_assignment_identity_mismatch")
        self.denied(d=debug(row(desired_state="paused")),
                    reason="allocation_debug_binding_mismatch")
        t = targets(allocation())
        t.pop("projects")
        self.denied(t=t, reason="missing_target_configuration")

    def test_target_pause_changed_across_reads_is_detected(self):
        before = targets(allocation())
        after = targets(allocation())
        after["projects"]["cloud::w1"]["desired_state"] = "paused"
        after["projects"]["cloud::w1"]["active"] = False
        with patch.object(subject, "_fetch", side_effect=[before, debug(row()), after]):
            with self.assertRaisesRegex(subject.EvidenceDenied, "allocation_changed_during_observation"):
                subject._live_snapshots()

    def test_http_fetch_bypasses_proxy_environment(self):
        with patch.object(subject.urllib.request, "build_opener", wraps=subject.urllib.request.build_opener) as make:
            class FakeResponse:
                status = 200
                headers = {"Content-Type": "application/json"}
                def read(self, _limit):
                    return b'{}'
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return False
            with patch.object(subject.urllib.request.OpenerDirector, "open", return_value=FakeResponse()):
                subject._fetch("/api/runner-targets")
        self.assertTrue(any(isinstance(a, subject.urllib.request.ProxyHandler) and not a.proxies
                            for a in make.call_args.args))

    def test_missing_browser_heartbeat_is_not_generation(self):
        report = self.classify(d=debug(row(verdict="no-heartbeat")))
        self.assertEqual("missing_browser_heartbeat", report["workers"][0]["state"])

    def test_paused_is_not_worker_failure_or_generation(self):
        report = self.classify(d=debug(row(verdict="paused")))
        self.assertEqual("paused", report["workers"][0]["state"])

    def test_empty_allocation_never_green(self):
        result = self.classify(t=targets(), d=debug())
        self.assertFalse(result["all_allocated_chatgpt_generating"])
        self.assertEqual([], result["workers"])

    def test_missing_debug_row_never_green(self):
        result = self.classify(d=debug())
        self.assertEqual("missing_worker_telemetry", result["workers"][0]["state"])

    def test_provider_isolated_from_chatgpt_status(self):
        t = targets(allocation(), allocation("haxlab::w2", "claude"))
        report = self.classify(t=t, d=debug(row()))
        self.assertTrue(report["all_allocated_chatgpt_generating"])
        self.assertEqual("other_provider", report["workers"][1]["state"])

    def test_claude_only_allocation_is_not_chatgpt_green(self):
        report = self.classify(t=targets(allocation("haxlab::w2", "claude")), d=debug())
        self.assertFalse(report["all_allocated_chatgpt_generating"])

    def test_multiworker_requires_every_chatgpt_generation(self):
        report = self.classify(
            t=targets(allocation(), allocation("haxlab::w1")),
            d=debug(row(), row("haxlab::w1", "prompt-sent")),
        )
        self.assertFalse(report["all_allocated_chatgpt_generating"])
        self.assertEqual(2, report["allocated_count"])
        self.assertEqual({"generating_observed": 1, "prompt_sent_without_generation": 1}, report["counts"])

    def test_generating_requires_non_terminal_browser_event(self):
        for event in (None, "generation-finished", "send-blocked",
                      "tab-ready", "", True):
            with self.subTest(event=event):
                finding = self.classify(d=debug(row(last_event=event)))
                self.assertFalse(finding["all_allocated_chatgpt_generating"])
                self.assertEqual("untrusted_generation_event", finding["workers"][0]["state"])

    def test_nonterminal_browser_event_types_valid_with_fresh_flag(self):
        for event in ("heartbeat", "generation-started", "generation-progress"):
            with self.subTest(event=event):
                report = self.classify(d=debug(row(last_event=event)))
                self.assertTrue(report["all_allocated_chatgpt_generating"])

    def test_extreme_json_nesting_denied_without_traceback(self):
        depth = 1200
        raw = b'{"x":' + b'[' * depth + b'0' + b']' * depth + b'}'
        with self.assertRaisesRegex(subject.EvidenceDenied, "malformed_json"):
            subject._load(raw)

    def test_invalid_queue_status_and_slot_or_eligibility_denied(self):
        for changes in [
            {"status": "queued"}, {"status": "done"},
            {"eligible": False}, {"eligible": 1},
            {"project_id": "haxlab"}, {"worker_slot": 2},
            {"worker_slot": True},
        ]:
            with self.subTest(changes=changes):
                t = targets(allocation())
                t["projects"]["cloud::w1"]["queue_item"].update(changes)
                self.denied(t=t, reason="target_queue_not_eligible")

    def test_expired_queue_lease_rejects_fresh_generating_flag(self):
        t = targets(allocation())
        t["projects"]["cloud::w1"]["queue_item"]["claim_expires"] = (NOW - timedelta(seconds=2)).isoformat()
        report = self.classify(t=t)
        self.assertEqual("expired_queue_claim", report["workers"][0]["state"])
        self.assertFalse(report["all_allocated_chatgpt_generating"])

    def test_no_expiry_queue_claim_is_supported(self):
        t = targets(allocation())
        t["projects"]["cloud::w1"]["queue_item"]["claim_expires"] = None
        self.assertTrue(self.classify(t=t)["all_allocated_chatgpt_generating"])

    def test_generation_from_before_current_queue_claim_is_not_green(self):
        t = targets(allocation())
        t["projects"]["cloud::w1"]["queue_item"]["claimed_at"] = (NOW - timedelta(seconds=3)).isoformat()
        report = self.classify(t=t, d=debug(row(last_event_age_s=8)))
        self.assertFalse(report["all_allocated_chatgpt_generating"])
        self.assertEqual("generation_predates_claim", report["workers"][0]["state"])

    def test_recent_event_after_current_claim_can_still_be_reported(self):
        t = targets(allocation())
        t["projects"]["cloud::w1"]["queue_item"]["claimed_at"] = (NOW - timedelta(seconds=9)).isoformat()
        report = self.classify(t=t, d=debug(row(last_event_age_s=8)))
        self.assertTrue(report["all_allocated_chatgpt_generating"])

    def test_claim_epoch_missing_naive_and_future_denied(self):
        for raw, reason in [
            (None, "invalid_observation_time"),
            ("2026-10-13T18:30:00", "naive_observation_time"),
            ((NOW + timedelta(seconds=11)).isoformat(), "future_target_claim"),
        ]:
            with self.subTest(raw=raw):
                t = targets(allocation())
                t["projects"]["cloud::w1"]["queue_item"]["claimed_at"] = raw
                self.denied(t=t, reason=reason)

    def test_same_queue_claim_epoch_refresh_is_detected(self):
        before = targets(allocation())
        after = targets(allocation())
        after["projects"]["cloud::w1"]["queue_item"]["claimed_at"] = (NOW - timedelta(minutes=3)).isoformat()
        with patch.object(subject, "_fetch", side_effect=[before, debug(row()), after]):
            with self.assertRaisesRegex(subject.EvidenceDenied, "allocation_changed_during_observation"):
                subject._live_snapshots()

    def test_stale_event_even_with_generating_verdict_fails(self):
        report = self.classify(d=debug(row(last_event_age_s=121)))
        self.assertEqual("stale_generation_telemetry", report["workers"][0]["state"])

    def test_missing_or_boolean_age_denied(self):
        for age in (None, True, False, -1, 2592001, "4", 1.2):
            with self.subTest(age=age):
                self.denied(d=debug(row(last_event_age_s=age)), reason="invalid_telemetry_age")

    def test_stale_prompt_age_denied(self):
        self.denied(d=debug(row(verdict="prompt-sent", last_prompt_age_s=None)),
                    reason="invalid_telemetry_age")

    def test_blocked_waiting_idle_are_non_generating(self):
        for verdict, expected in [
            ("blocked:contract", "blocked"), ("blocked:client", "blocked"),
            ("waiting", "not_generating"), ("idle:no-assignment", "not_generating"),
        ]:
            with self.subTest(verdict=verdict):
                self.assertEqual(expected, self.classify(d=debug(row(verdict=verdict)))["workers"][0]["state"])

    def test_unrecognized_verdict_fails_closed(self):
        self.denied(d=debug(row(verdict="unknown-status")), reason="unknown_worker_verdict")

    def test_stale_and_future_snapshot_denied(self):
        for seconds in (-11, 121):
            self.denied(d=debug(row(), at=NOW-timedelta(seconds=seconds)), reason="stale_or_future_observation")

    def test_missing_and_naive_snapshot_time_denied(self):
        self.denied(d={"workers": [row()]}, reason="invalid_observation_time")
        self.denied(d={"time": "2026-10-13T18:30:00", "workers": [row()]},
                    reason="naive_observation_time")

    def test_allocation_identity_conflict_denied(self):
        bad = allocation()
        bad["worker_slot"] = 2
        self.denied(t=targets(bad), reason="allocation_identity_mismatch")
        bad["worker_slot"] = True
        self.denied(t=targets(bad), reason="allocation_identity_mismatch")

    def test_generation_from_previous_queue_owner_denied(self):
        self.denied(d=debug(row(queue_id="another-claim")),
                    reason="allocation_debug_binding_mismatch")

    def test_generation_from_reassigned_global_slot_denied(self):
        self.denied(d=debug(row(global_slot=2)), reason="allocation_debug_binding_mismatch")

    def test_generation_from_wrong_provider_denied(self):
        self.denied(d=debug(row(provider="claude")), reason="allocation_debug_binding_mismatch")

    def test_missing_or_ambiguous_allocation_binding_denied(self):
        for changes, reason in [
            ({"queue_id": None}, "invalid_allocated_queue"),
            ({"queue_id": ""}, "invalid_allocated_queue"),
            ({"queue_id": "z"*161}, "invalid_allocated_queue"),
            ({"global_worker_slot": True}, "invalid_allocated_global_slot"),
            ({"global_worker_slot": 0}, "invalid_allocated_global_slot"),
        ]:
            with self.subTest(changes=changes):
                item = allocation()
                item.update(changes)
                self.denied(t=targets(item), reason=reason)

    def test_duplicate_allocation_and_debug_denied(self):
        self.denied(t=targets(allocation(), allocation()), reason="duplicate_allocated_worker")
        self.denied(d=debug(row(), row()), reason="duplicate_debug_worker")

    def test_debug_identity_conflict_denied(self):
        self.denied(d=debug(row(project="ftmo")), reason="debug_identity_mismatch")

    def test_bad_worker_keys_denied(self):
        for key in ("cloud", "cloud::w0", "cloud::w01", "Cloud::w1", "../secret::w1", "cloud::w1\n"):
            with self.subTest(key=key):
                bad = allocation()
                bad["worker_key"] = key
                self.denied(t=targets(bad), reason="invalid_worker_identity")

    def test_missing_allocation_and_worker_rows_denied(self):
        self.denied(t={}, reason="missing_allocation")
        self.denied(d={"time": NOW.isoformat()}, reason="missing_worker_debug")

    def test_untyped_or_unbounded_allocation_denied(self):
        self.denied(t=targets("worker"), reason="invalid_allocation_row")
        self.denied(t=targets(*[allocation(f"cloud::w{i}") for i in range(1,34)]),
                    reason="oversized_allocation")

    def test_duplicate_json_decoded_keys_denied(self):
        with self.assertRaisesRegex(subject.EvidenceDenied, "duplicate_json_key"):
            subject._load(b'{"x":1,"\\u0078":2}')

    def test_bad_bytes_and_bad_shapes_denied(self):
        for raw in (b'[]', b'{', b'{"a":\xff}', b' ' * (subject.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:16]):
                with self.assertRaises(subject.EvidenceDenied):
                    subject._load(raw)

    def test_decoded_unpaired_surrogates_fail_closed_at_all_depths(self):
        cases = [
            b'{"x":"\\ud800"}', b'{"x":"\\udc00"}',
            b'{"\\ud800":"val"}', b'{"nested":[{"key":"\\udfff"}]}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(subject.EvidenceDenied, "invalid_decoded_unicode"):
                    subject._load(raw)

    def test_valid_surrogate_pair_and_utf8_emoji_accepted(self):
        self.assertEqual(subject._load(b'{"x":"\\ud83d\\ude80"}')["x"], "🚀")
        self.assertEqual(subject._load(json.dumps({"x": "🚀"}, ensure_ascii=False).encode())["x"], "🚀")

    def test_nonfinite_json_constants_and_overflow_denied(self):
        for raw in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                    b'{"nested":[1e999]}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(subject.EvidenceDenied, "nonfinite_json"):
                    subject._load(raw)

    def test_allocation_fingerprint_stable_under_nonidentity_changes(self):
        left = targets(allocation())
        right = targets(allocation())
        right["memory_guard"] = {"available_mb": 42}
        right["global_allocation"]["workers"][0]["score"] = 17
        self.assertEqual(subject._allocation_fingerprint(left), subject._allocation_fingerprint(right))

    def test_allocation_fingerprint_deny_reassigned_queue(self):
        before = targets(allocation())
        after = targets(allocation())
        after["global_allocation"]["workers"][0]["queue_id"] = "different"
        after["projects"]["cloud::w1"]["queue_item"]["queue_id"] = "different"
        self.assertNotEqual(subject._allocation_fingerprint(before), subject._allocation_fingerprint(after))

    def test_allocation_fingerprint_rejects_duplicate_global_slots(self):
        second = allocation("haxlab::w1")
        with self.assertRaisesRegex(subject.EvidenceDenied, "ambiguous_allocation_snapshot"):
            subject._allocation_fingerprint(targets(allocation(), second))

    def test_live_bracketing_rejects_reassignment_during_debug(self):
        first = targets(allocation())
        second = targets(allocation())
        second["global_allocation"]["workers"][0]["queue_id"] = "reassigned"
        second["projects"]["cloud::w1"]["queue_item"]["queue_id"] = "reassigned"
        with patch.object(subject, "_fetch", side_effect=[first, debug(row()), second]) as fetch:
            with self.assertRaisesRegex(subject.EvidenceDenied, "allocation_changed_during_observation"):
                subject._live_snapshots()
            self.assertEqual(
                ["/api/runner-targets", "/api/worker-debug", "/api/runner-targets"],
                [call.args[0] for call in fetch.call_args_list],
            )

    def test_live_bracketing_accepts_stable_identity(self):
        first = targets(allocation())
        second = targets(allocation())
        second["global_allocation"]["workers"][0]["priority"] = "P1"
        evidence = debug(row())
        with patch.object(subject, "_fetch", side_effect=[first, evidence, second]):
            self.assertEqual((second, evidence), subject._live_snapshots())

    def test_live_cli_denies_changed_allocation_without_secret_output(self):
        first = targets(allocation())
        second = targets(allocation())
        second["global_allocation"]["workers"][0]["queue_id"] = "private-queue-identifier"
        second["projects"]["cloud::w1"]["queue_item"]["queue_id"] = "private-queue-identifier"
        output = StringIO()
        with patch.object(subject, "_fetch", side_effect=[first, debug(row()), second]):
            with redirect_stdout(output):
                rc = subject.main([])
        self.assertEqual(2, rc)
        report = json.loads(output.getvalue())
        self.assertEqual("allocation_changed_during_observation", report["reason"])
        self.assertNotIn("private-queue-identifier", output.getvalue())
        self.assertFalse(report["control_authorized"])

    def test_strict_json_content_type_denies_html_with_json_token(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html; application/json"}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        with patch.object(subject.urllib.request.OpenerDirector, "open",
                          return_value=FakeResponse()):
            with self.assertRaisesRegex(subject.EvidenceDenied, "invalid_http_response"):
                subject._fetch("/api/worker-debug")

    def test_fixture_cli_green_and_non_green_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory)/"targets.json", Path(directory)/"debug.json"
            live_targets = targets(allocation())
            live_targets["projects"]["cloud::w1"]["queue_item"]["claim_expires"] = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat()
            first.write_text(json.dumps(live_targets), encoding="utf-8")
            second.write_text(json.dumps(debug(row(), at=datetime.now(timezone.utc))), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = subject.main(["--targets-file", str(first), "--debug-file", str(second)])
            self.assertEqual(0, result, output.getvalue())
            parsed = json.loads(output.getvalue())
            self.assertTrue(parsed["all_allocated_chatgpt_generating"])
            second.write_text(json.dumps(debug(row(verdict="prompt-sent"), at=datetime.now(timezone.utc))), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(1, subject.main(["--targets-file", str(first), "--debug-file", str(second)]))

    def test_strict_progress_cli_has_distinct_exit_while_preserving_old_one(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory)/"targets.json", Path(directory)/"debug.json"
            evidence = targets(allocation())
            evidence["projects"]["cloud::w1"]["queue_item"]["claim_expires"] = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat()
            first.write_text(json.dumps(evidence), encoding="utf-8")
            heartbeat = debug(row(last_event="heartbeat"), at=datetime.now(timezone.utc))
            second.write_text(json.dumps(heartbeat), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(0, subject.main(["--targets-file", str(first), "--debug-file", str(second)]))
            buf = StringIO()
            with redirect_stdout(buf):
                code = subject.main(["--targets-file", str(first), "--debug-file", str(second),
                                     "--require-progress"])
            self.assertEqual(1, code)
            report = json.loads(buf.getvalue())
            self.assertTrue(report["all_allocated_chatgpt_generating"])
            self.assertFalse(report["all_allocated_chatgpt_progressing"])
            heartbeat["workers"][0]["last_event"] = "generation-progress"
            second.write_text(json.dumps(heartbeat), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(0, subject.main(["--targets-file", str(first), "--debug-file", str(second),
                                                  "--require-progress"]))

    def test_symlink_fixture_denied_before_read(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"source.json"
            alias = Path(directory)/"alias.json"
            debugfile = Path(directory)/"debug.json"
            source.write_text(json.dumps(targets(allocation())), encoding="utf-8")
            alias.symlink_to(source)
            debugfile.write_text(json.dumps(debug(row())), encoding="utf-8")
            with redirect_stdout(StringIO()) as output:
                rc = subject.main(["--targets-file", str(alias), "--debug-file", str(debugfile)])
            self.assertEqual(2, rc)
            self.assertEqual("invalid_fixture_path", json.loads(output.getvalue())["reason"])

    def test_reviewed_zssh_recipe_is_bounded_and_silent(self):
        import runpy
        import subprocess
        from types import SimpleNamespace
        recipe = Path(__file__).resolve().parents[1] / "ops/zssh-runner-recipes/zcloud-generation-check.py"
        for code in (0, 1, 2):
            with self.subTest(code=code):
                with patch("subprocess.run", return_value=SimpleNamespace(returncode=code)) as invoked:
                    with self.assertRaises(SystemExit) as outcome:
                        runpy.run_path(str(recipe), run_name="__main__")
                    self.assertEqual(code, outcome.exception.code)
                args, kw = invoked.call_args
                self.assertEqual(
                    [subject.sys.executable, "scripts/zcloud_worker_generation_evidence_20261013_w1.py", "--require-progress"],
                    args[0],
                )
                self.assertIs(kw["stdin"], subprocess.DEVNULL)
                self.assertIs(kw["stdout"], subprocess.DEVNULL)
                self.assertIs(kw["stderr"], subprocess.DEVNULL)
                self.assertEqual(15, kw["timeout"])
                self.assertFalse(kw["check"])
                self.assertNotIn("shell", kw)

    def test_live_http_is_read_only_fixed_localhost_get(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json; charset=utf-8"}
            def __init__(self, content):
                self.content = content
            def read(self, limit):
                return self.content[:limit]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        seen=[]
        def fake_open(_opener, request, timeout):
            seen.append((request.full_url, request.get_method(), timeout))
            return FakeResponse(b'{"global_allocation":{"workers":[]}}')
        with patch.object(subject.urllib.request.OpenerDirector, "open", fake_open):
            # Verify the request constructor and fixed host; do not access VPS.
            payload = subject._fetch("/api/runner-targets")
        self.assertEqual({"global_allocation": {"workers": []}}, payload)
        self.assertEqual([(subject.ENDPOINT + "/api/runner-targets", "GET", 5)], seen)


if __name__ == "__main__":
    unittest.main()
