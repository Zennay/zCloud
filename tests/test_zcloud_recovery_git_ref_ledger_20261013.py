"""Pure unit and concurrent mock contract for GitHub-ref durable recovery claims.

This test suite NEVER issues POST to GitHub and creates no repository refs.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from threading import Lock
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "zcloud_recovery_git_ref_ledger_20261013.py"
spec = importlib.util.spec_from_file_location("ref_claim", MODULE)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_recovery_retry_policy_20261013 as policy

S = "a" * 40
T = "b" * 40


def candidate(sha=S):
    return {"decision": "READY_FOR_EXCLUSIVE_RESERVATION", "sha": sha, "authorized": False}


def created(plan):
    return {"ref": plan["ref"], "object": {"type": "commit", "sha": plan["sha"]}}


class FakeAtomicGitRefServer:
    """In-memory fake of atomic create-only naming; not a GitHub API server."""
    def __init__(self):
        self.lock = Lock()
        self.refs = {}

    def post(self, plan):
        with self.lock:
            if plan["ref"] in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[plan["ref"]] = plan["sha"]
            return 201, created(plan)


class GitRefReservationTests(unittest.TestCase):
    def plan(self, sha=S):
        plan = ledger.reservation_plan(candidate(sha))
        self.assertIs(plan["authorized"], False)
        self.assertIs(plan["dispatch_allowed"], False)
        self.assertIs(plan["mutations_performed"], False)
        return plan

    def classify(self, plan, status, response=None, expected="DENY"):
        outcome = ledger.classify_create_result(plan, status=status, response=response)
        self.assertEqual(outcome["reservation_state"], expected)
        self.assertIs(outcome["authorized"], False)
        self.assertIs(outcome["dispatch_allowed"], False)
        self.assertTrue(outcome["do_not_retry_automatically"])
        return outcome

    def test_actual_failed_production_status_cannot_create_ref_plan(self):
        snapshot = {
            "schema": policy.SCHEMA, "repository": policy.REPO,
            "main_sha": S,
            "production_status": {
                "sha": S, "complete": True,
                "statuses": [{"context": "zcloud/vps-production", "state": "failure"}],
            },
        }
        # Deliberately no workflow history: exact status takes precedence.
        outcome = policy.classify(snapshot)
        self.assertEqual(outcome["decision"], "NOOP")
        self.assertEqual(outcome["production_state"], "failure")
        with self.assertRaises(ledger.ContractViolation):
            ledger.reservation_plan(outcome)

    def test_trusted_green_regression_remains_non_authorizing_candidate(self):
        run = {
            "id": 1, "run_attempt": 1, "head_sha": S, "head_branch": "main",
            "event": "push", "status": "completed", "conclusion": "success",
        }
        snapshot = {
            "schema": policy.SCHEMA, "repository": policy.REPO,
            "main_sha": S,
            "production_status": {"sha": S, "complete": True, "statuses": []},
            "deploy": {"sha": S, "workflow": policy.WORKFLOWS["deploy"],
                       "complete": True, "has_next_page": False, "runs": []},
            "regression": {"sha": S, "workflow": policy.WORKFLOWS["regression"],
                           "complete": True, "has_next_page": False, "runs": [run]},
        }
        candidate = policy.classify(snapshot)
        self.assertEqual(candidate["decision"], "READY_FOR_EXCLUSIVE_RESERVATION")
        planned = ledger.reservation_plan(candidate)
        self.assertEqual(planned["sha"], S)
        self.assertFalse(planned["authorized"])
        self.assertFalse(planned["dispatch_allowed"])

    def test_deterministic_exact_main_git_ref(self):
        p1 = self.plan()
        p2 = self.plan()
        self.assertEqual(p1, p2)
        self.assertEqual(p1["ref"], ledger.PREFIX + "/" + S)
        self.assertEqual(p1["path"], "/repos/Zennay/zCloud/git/refs")
        self.assertEqual(p1["method"], "POST")
        self.assertEqual(p1["repository_id"], 1389227570)
        self.assertEqual(p1["payload"], {"ref": p1["ref"], "sha": S})
        self.assertIs(p1["requires_protected_tag_namespace"], True)
        self.assertIs(p1["requires_no_tag_deletion_or_rewrite"], True)
        self.assertIs(p1["requires_durable_owner_review"], True)

    def test_fresh_main_uses_different_key(self):
        self.assertNotEqual(self.plan(S)["ref"], self.plan(T)["ref"])

    def test_deny_every_non_candidate_and_untrusted_authorization(self):
        for val in ("NOOP", "WAIT", "DENY", None, True):
            with self.subTest(val=val):
                with self.assertRaises(ledger.ContractViolation):
                    ledger.reservation_plan({"decision": val, "sha": S, "authorized": False})
        for val in (None, True, 0, "false"):
            with self.subTest(authorized=val):
                with self.assertRaises(ledger.ContractViolation):
                    ledger.reservation_plan({"decision": "READY_FOR_EXCLUSIVE_RESERVATION", "sha": S, "authorized": val})

    def test_deny_bad_sha_and_non_dict_input(self):
        for val in ("bad", "A" * 40, S + "0", "a" * 39, 0, {}, None, True):
            with self.subTest(sha=val):
                with self.assertRaises(ledger.ContractViolation):
                    ledger.reservation_plan(candidate(val))
        with self.assertRaises(ledger.ContractViolation):
            ledger.reservation_plan(None)

    def test_201_must_exactly_match_created_commit_ref(self):
        plan = self.plan()
        out = self.classify(plan, 201, created(plan), "RESERVATION_RECORDED")
        self.assertEqual(out["reason"], "exact_ref_created")
        self.assertIs(out["mutations_performed"], False)  # classifier never writes

    def test_201_malformed_or_mismatched_response_denied(self):
        plan = self.plan()
        for body in (None, [], {}, {"ref": plan["ref"]},
                     {"ref": plan["ref"], "object": {"type": "tag", "sha": S}},
                     {"ref": "refs/heads/main", "object": {"type": "commit", "sha": S}},
                     {"ref": plan["ref"], "object": {"type": "commit", "sha": T}}):
            with self.subTest(response=body):
                self.classify(plan, 201, body)

    def test_422_is_ambiguous_and_does_not_grant_duplicate_success(self):
        plan = self.plan()
        for status in (409, 422):
            out = self.classify(plan, status, {"message": "Reference already exists"})
            self.assertEqual(out["reason"], "ref_conflict_or_validation_denied")

    def test_401_403_deny_write_authority(self):
        plan = self.plan()
        for status in (401, 403):
            out = self.classify(plan, status, {"message": "forbidden"})
            self.assertEqual(out["reason"], "missing_or_denied_write_authority")

    def test_unknown_timeout_and_server_errors_never_auto_retry(self):
        plan = self.plan()
        for status in (None, 500, 502, 503, 504, 429):
            self.classify(plan, status)

    def test_mutated_plan_cannot_be_reused(self):
        p = self.plan()
        modifications = [
            ("ref", "refs/heads/main"), ("sha", T), ("repository", "other/project"),
            ("repository_id", True), ("method", "PATCH"),
            ("path", "/repos/Zennay/zCloud/git/ref/tags/stale"),
            ("payload", {"ref": p["ref"], "sha": T}),
            ("authorized", True), ("dispatch_allowed", True),
            ("requires_owner_provenance", False),
            ("requires_exact_main_recheck_before_claim", False),
            ("requires_exact_main_recheck_after_claim", False),
            ("requires_protected_tag_namespace", False),
            ("requires_durable_owner_review", False),
            ("requires_no_tag_deletion_or_rewrite", False)
        ]
        for name, value in modifications:
            modified = {**p, name: value}
            with self.subTest(name=name):
                with self.assertRaises(ledger.ContractViolation):
                    ledger.classify_create_result(modified, status=201, response=created(p))

    def test_exact_existing_ref_is_non_authorizing(self):
        p = self.plan()
        observed = ledger.classify_existing_ref(p, created(p))
        self.assertEqual(observed["reservation_state"], "EXISTING_RESERVATION")
        self.assertIs(observed["dispatch_allowed"], False)
        self.assertTrue(observed["do_not_retry_automatically"])

    def test_missing_or_altered_ref_never_allows_retry(self):
        p = self.plan()
        for body in (None, {}, [], {"ref": p["ref"]},
                     {"ref": p["ref"], "object": {"sha": T, "type": "commit"}}):
            with self.subTest(body=body):
                observed = ledger.classify_existing_ref(p, body)
                self.assertEqual(observed["reservation_state"], "DENY")
                self.assertTrue(observed["do_not_retry_automatically"])

    def test_atomic_reference_simulation_has_only_one_winner(self):
        plan = self.plan()
        server = FakeAtomicGitRefServer()
        def attempt(_):
            status, response = server.post(plan)
            return ledger.classify_create_result(plan, status=status, response=response)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(attempt, range(64)))
        self.assertEqual(sum(x["reservation_state"] == "RESERVATION_RECORDED"
                             for x in results), 1)
        self.assertEqual(len(server.refs), 1)
        self.assertTrue(all(not x["dispatch_allowed"] for x in results))

    def test_atomic_reference_simulation_new_main_new_generation(self):
        server = FakeAtomicGitRefServer()
        for sha in (S, T):
            plan = self.plan(sha)
            status, body = server.post(plan)
            self.classify(plan, status, body, expected="RESERVATION_RECORDED")
        self.assertEqual(len(server.refs), 2)

    def test_offline_module_contains_no_network_transport_or_secret(self):
        text = MODULE.read_text()
        for forbidden in ("urllib", "requests.", "httpx", "github.com/",
                          "GITHUB_TOKEN", "os.environ", "subprocess", "systemctl"):
            self.assertNotIn(forbidden, text)

if __name__ == "__main__":
    unittest.main()
