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
    return {"global_allocation": {"workers": list(items)}}


def allocation(name="cloud::w1", provider="chatgpt"):
    project, slot = name.rsplit("::w", 1)
    return {"worker_key": name, "project_id": project, "worker_slot": int(slot),
            "global_worker_slot": int(slot), "queue_id": "qid-" + name, "provider": provider}


def row(name="cloud::w1", verdict="generating", **changes):
    project, slot = name.rsplit("::w", 1)
    return {
        "worker": name, "project": project, "slot": int(slot),
        "global_slot": int(slot), "queue_id": "qid-" + name, "provider": "chatgpt",
        "verdict": verdict, "last_event_age_s": 8,
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
                           "generation_evidence": True}], report["workers"])

    def test_prompt_sent_is_not_generation(self):
        result = self.classify(d=debug(row(verdict="prompt-sent")))
        self.assertFalse(result["all_allocated_chatgpt_generating"])
        self.assertEqual("prompt_sent_without_generation", result["workers"][0]["state"])

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

    def test_fixture_cli_green_and_non_green_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory)/"targets.json", Path(directory)/"debug.json"
            first.write_text(json.dumps(targets(allocation())), encoding="utf-8")
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
                    [subject.sys.executable, "scripts/zcloud_worker_generation_evidence_20261013_w1.py"],
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
