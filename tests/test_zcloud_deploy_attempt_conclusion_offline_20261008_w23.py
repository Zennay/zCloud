import unittest
from scripts.zcloud_deploy_attempt_conclusion_offline_20261008_w23 import (
    has_unique_terminal_evidence, is_terminal_attempt_evidence, live_deploy_authorized
)

SHA = "a" * 40
BASE = dict(run_id=42, attempt=2, head_sha=SHA, job_id=13, conclusion="success",
            completed_at="2026-10-08T04:00:00Z")

class AttemptConclusionContractTests(unittest.TestCase):
    def check_bad(self, **changes):
        self.assertFalse(is_terminal_attempt_evidence({**BASE, **changes}, run_id=42, attempt=2, head_sha=SHA))

    def test_valid_terminal(self):
        self.assertTrue(is_terminal_attempt_evidence(BASE, run_id=42, attempt=2, head_sha=SHA))

    def test_wrong_attempt(self): self.check_bad(attempt=1)
    def test_wrong_run(self): self.check_bad(run_id=41)
    def test_wrong_sha(self): self.check_bad(head_sha="b"*40)
    def test_nonterminal(self): self.check_bad(conclusion=None)
    def test_untrusted_conclusion(self): self.check_bad(conclusion="in_progress")
    def test_bool_job_id(self): self.check_bad(job_id=True)
    def test_missing_timestamp(self): self.check_bad(completed_at="")
    def test_non_utc_timestamp(self): self.check_bad(completed_at="2026-10-08T04:00:00+01:00")
    def test_unknown_field(self):
        self.assertFalse(is_terminal_attempt_evidence({**BASE, "token": "secret"}, run_id=42, attempt=2, head_sha=SHA))
    def test_missing_field(self):
        self.assertFalse(is_terminal_attempt_evidence({k:v for k,v in BASE.items() if k!="job_id"},run_id=42,attempt=2,head_sha=SHA))
    def test_duplicate_job(self):
        self.assertFalse(has_unique_terminal_evidence([BASE, dict(BASE)],run_id=42,attempt=2,head_sha=SHA))
    def test_empty_jobs(self):
        self.assertFalse(has_unique_terminal_evidence([],run_id=42,attempt=2,head_sha=SHA))
    def test_distinct_jobs(self):
        self.assertTrue(has_unique_terminal_evidence([BASE, {**BASE,"job_id":14,"conclusion":"failure"}],run_id=42,attempt=2,head_sha=SHA))
    def test_live_authorization_never_granted(self):
        self.assertFalse(live_deploy_authorized(BASE))

if __name__ == "__main__": unittest.main()
