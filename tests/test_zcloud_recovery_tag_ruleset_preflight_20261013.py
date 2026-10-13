"""Offline regression for exact namespace GitHub ruleset evidence (no writes)."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_recovery_tag_ruleset_preflight_20261013 as preflight
import zcloud_recovery_github_evidence_20261013 as api

SHA = "a" * 40
OTHER = "b" * 40


def rule_summary():
    return {"id": 17, "source_type": "Repository", "source": "Zennay/zCloud",
            "enforcement": "active"}


def rule_detail():
    return {
        **rule_summary(), "target": "tag", "bypass_actors": [],
        "conditions": {
            "ref_name": {
                "include": [preflight.PREFIX_PATTERN], "exclude": []
            }
        },
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }


def screen(**updates):
    data = {
        "repo_id": 1389227570,
        "before_sha": SHA, "after_sha": SHA,
        "listing_complete": True,
        "summaries": [rule_summary()],
        "details": {17: rule_detail()},
    }
    data.update(updates)
    return preflight.screen_snapshot(**data)


class FakeGitHub:
    """Mimics bounded canonical GET calls; no HTTP or repository writes."""
    def __init__(self, *, rulesets=None, detail=None):
        self.before = SHA
        self.after = SHA
        self.rulesets = [rule_summary()] if rulesets is None else rulesets
        self.detail = rule_detail() if detail is None else detail
        self.calls = []
        self.main_calls = 0
        self.ruleset_list_calls = 0
        self.second_rulesets = None

    def __call__(self, path):
        self.calls.append(path)
        if path == "/commits/main":
            self.main_calls += 1
            return {"sha": self.before if self.main_calls == 1 else self.after}
        if path == "":
            return {"id": 1389227570, "full_name": "Zennay/zCloud"}
        if path.startswith("/rulesets?"):
            self.assert_path(path)
            self.ruleset_list_calls += 1
            if self.ruleset_list_calls > 1 and self.second_rulesets is not None:
                return self.second_rulesets
            return self.rulesets
        if path == "/rulesets/17?includes_parents=true":
            return self.detail
        raise AssertionError("unexpected GET: " + path)

    def assert_path(self, path):
        assert path == (
            "/rulesets?includes_parents=true&targets=tag&per_page=100&page=1"
        ), "unexpected GET ruleset query"


class PreflightTests(unittest.TestCase):
    def test_strict_ruleset_only_candidate_not_authority(self):
        observation = screen()
        self.assertEqual(observation["reason"], "candidate_ruleset_syntax_only")
        self.assertIs(observation["tag_protection_candidate"], True)
        self.assertIs(observation["effective_protection_verified"], False)
        self.assertIs(observation["owner_review_required"], True)
        self.assertIs(observation["authorized"], False)
        self.assertIs(observation["dispatch_allowed"], False)

    def test_zero_rulesets_matches_actual_repo_and_denies(self):
        fake = FakeGitHub(rulesets=[])
        result = preflight.observe(fake)
        self.assertEqual(result["reason"], "no_rulesets")
        self.assertFalse(result["tag_protection_candidate"])
        self.assertFalse(result["authorized"])
        self.assertEqual(fake.main_calls, 2)
        self.assertEqual(len(fake.calls), 5)
        self.assertEqual(fake.ruleset_list_calls, 2)

    def test_valid_exact_get_flow_candidate_only(self):
        fake = FakeGitHub()
        observed = preflight.observe(fake)
        self.assertEqual(observed["reason"], "candidate_ruleset_syntax_only")
        self.assertFalse(observed["effective_protection_verified"])
        self.assertFalse(observed["authorized"])
        self.assertEqual(fake.calls, [
            "/commits/main", "",
            "/rulesets?includes_parents=true&targets=tag&per_page=100&page=1",
            "/rulesets/17?includes_parents=true",
            "/rulesets?includes_parents=true&targets=tag&per_page=100&page=1",
            "/commits/main"
        ])

    def test_missing_bypass_actor_list_denied_because_rest_may_omit_it(self):
        data = rule_detail()
        del data["bypass_actors"]
        self.assertFalse(preflight.exact_protection(data))

    def test_bypass_actors_denied_even_if_update_deletion_present(self):
        data = rule_detail()
        data["bypass_actors"] = [{"actor_type": "RepositoryRole",
                                  "actor_id": 5, "bypass_mode": "always"}]
        self.assertFalse(preflight.exact_protection(data))

    def test_wrong_target_or_enforcement_denied(self):
        for field, bad in (
            ("target", "branch"), ("target", "push"),
            ("enforcement", "evaluate"), ("enforcement", "disabled"),
        ):
            data = rule_detail()
            data[field] = bad
            with self.subTest(field=field, bad=bad):
                self.assertFalse(preflight.exact_protection(data))

    def test_exact_prefix_required_no_broad_wildcards(self):
        for pattern in ("~ALL", "refs/tags/*",
                        "refs/tags/zcloud-recovery-reserved-v1/*",
                        preflight.PREFIX_PATTERN[:-1] + "x",
                        preflight.PREFIX_PATTERN.upper(), ""):
            data = rule_detail()
            data["conditions"]["ref_name"]["include"] = [pattern]
            with self.subTest(pattern=pattern):
                self.assertFalse(preflight.exact_protection(data))

    def test_unsupported_extra_condition_is_ambiguous(self):
        item = rule_detail()
        item["conditions"]["repository_name"] = {
            "include": ["other"], "exclude": []
        }
        self.assertFalse(preflight.exact_protection(item))

    def test_unsupported_ref_name_field_fails_closed(self):
        item = rule_detail()
        item["conditions"]["ref_name"]["case_sensitive"] = False
        self.assertFalse(preflight.exact_protection(item))

    def test_extra_creation_rule_denies_candidate_as_unusable(self):
        item = rule_detail()
        item["rules"].append({"type": "creation"})
        self.assertFalse(preflight.exact_protection(item))

    def test_unrelated_organization_ruleset_source_denied(self):
        summary = {**rule_summary(), "source_type": "Organization",
                   "source": "other_org"}
        detail = {**rule_detail(), "source_type": "Organization",
                  "source": "other_org"}
        observation = screen(summaries=[summary], details={17: detail})
        self.assertEqual(observation["reason"], "untrusted_ruleset_source")

    def test_any_exclusion_denies(self):
        data = rule_detail()
        data["conditions"]["ref_name"]["exclude"] = [
            "refs/tags/zcloud-recovery-reserved-v1/regression/a*"
        ]
        self.assertFalse(preflight.exact_protection(data))

    def test_requires_both_update_and_deletion(self):
        for kinds in ([], [{"type": "deletion"}], [{"type": "update"}],
                      [{"type": "deletion"}, {"type": "unknown"}]):
            data = rule_detail()
            data["rules"] = kinds
            self.assertFalse(preflight.exact_protection(data))

    def test_no_ruleset_authority_even_if_matching(self):
        data = rule_detail()
        result = screen(details={17: data})
        self.assertFalse(result["effective_protection_verified"])
        self.assertFalse(result["authorized"])
        self.assertFalse(result["dispatch_allowed"])

    def test_identity_and_main_drift_fail_closed(self):
        for updates in (
            {"repo_id": True}, {"repo_id": 777},
            {"before_sha": OTHER}, {"after_sha": OTHER},
            {"after_sha": None},
        ):
            with self.subTest(updates=updates):
                self.assertFalse(screen(**updates)["tag_protection_candidate"])

    def test_pagination_requires_complete_unambiguous_list(self):
        cases = (
            {"listing_complete": False},
            {"listing_complete": None},
            {"summaries": "not a list"},
            {"summaries": [rule_summary(), rule_summary()]},
            {"summaries": [{**rule_summary(), "id": True}]},
            {"summaries": [rule_summary()] * 1001},
        )
        for updates in cases:
            with self.subTest(updates=str(updates)[:100]):
                self.assertFalse(screen(**updates)["tag_protection_candidate"])

    def test_details_must_be_exact_same_id_set(self):
        for details in ({}, {17: rule_detail(), 99: rule_detail()},
                        {"17": rule_detail()},
                        {17: {**rule_detail(), "id": 99}},
                        {17: {**rule_detail(), "source": "other/repo"}}):
            with self.subTest(detail=str(details)[:100]):
                self.assertFalse(screen(details=details)["tag_protection_candidate"])

    def test_conflicting_enforcement_between_summary_and_detail_denies(self):
        detail = rule_detail()
        detail["enforcement"] = "evaluate"
        self.assertEqual(screen(details={17: detail})["reason"],
                         "detail_summary_mismatch")

    def test_untrusted_ruleset_source_denied(self):
        summary = {**rule_summary(), "source": "evil/project"}
        detail = {**rule_detail(), "source": "evil/project"}
        res = screen(summaries=[summary], details={17: detail})
        self.assertEqual(res["reason"], "untrusted_ruleset_source")

    def test_empty_details_when_nonempty_summary_denies(self):
        self.assertEqual(screen(details={})["reason"],
                         "missing_or_extra_ruleset_details")

    def test_live_response_changed_main_fails_closed(self):
        fake = FakeGitHub()
        fake.after = OTHER
        observed = preflight.observe(fake)
        self.assertEqual(observed["reason"], "main_drift_or_invalid_sha")
        self.assertFalse(observed["tag_protection_candidate"])

    def test_wrong_repository_id_denied_before_page_query(self):
        class WrongRepo(FakeGitHub):
            def __call__(self, path):
                if path == "":
                    return {"id": 999, "full_name": "Zennay/zCloud"}
                return super().__call__(path)
        fake = WrongRepo()
        r = preflight.observe(fake)
        self.assertEqual(r["reason"], "untrusted_repository")
        self.assertNotIn("/rulesets/17?includes_parents=true", fake.calls)

    def test_ruleset_removed_between_list_and_details_denies(self):
        fake = FakeGitHub()
        fake.second_rulesets = []
        r = preflight.observe(fake)
        self.assertEqual(r["reason"], "rulesets_changed_during_snapshot")
        self.assertFalse(r["tag_protection_candidate"])
        self.assertFalse(r["authorized"])

    def test_ruleset_policy_changed_between_list_reads_denies(self):
        fake = FakeGitHub()
        fake.second_rulesets = [{**rule_summary(), "enforcement": "evaluate"}]
        r = preflight.observe(fake)
        self.assertEqual(r["reason"], "rulesets_changed_during_snapshot")
        self.assertFalse(r["tag_protection_candidate"])

    def test_missing_ruleset_detail_denies(self):
        class Missing(FakeGitHub):
            def __call__(self, path):
                if path == "/rulesets/17?includes_parents=true":
                    raise preflight.SourceDenied("github_transport_unavailable")
                return super().__call__(path)
        r = preflight.observe(Missing())
        self.assertEqual(r["reason"], "github_transport_unavailable")

    def test_malformed_ruleset_listing_denies(self):
        fake = FakeGitHub(rulesets={"unexpected": "mapping"})
        self.assertEqual(preflight.observe(fake)["reason"], "bad_ruleset_listing")

    def test_no_mutating_paths_or_credential_echos(self):
        source = (ROOT / "scripts/zcloud_recovery_tag_ruleset_preflight_20261013.py").read_text()
        for forbidden in ('method="POST"', "actions: write", "git/refs",
                          "delete_ref", "update_ref", "GITHUB_TOKEN", "subprocess"):
            self.assertNotIn(forbidden, source)
        self.assertIn('return result("no_rulesets"', source)


if __name__ == "__main__":
    unittest.main()
