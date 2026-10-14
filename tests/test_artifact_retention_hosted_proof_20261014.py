"""No self-hosted job is required to audit repository artifact metadata.

The scanner consumes checked-out GitHub Actions YAML only. PRs run an
immutable checkout on GitHub-hosted CI, and the manual source scan stays
owner/main-only. This test never contacts the VPS or GitHub API.
"""
from pathlib import Path
import unittest

WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/zcloud-artifact-retention-audit.yml")


def jobs(source: str) -> tuple[str, str]:
    validate = source.split("  validate:\n", 1)[1].split("  audit:\n", 1)[0]
    audit = source.split("  audit:\n", 1)[1]
    return validate, audit


def manual_admitted(event, repository, ref, actor, triggering_actor):
    return (event == "workflow_dispatch" and repository == "Zennay/zCloud"
            and ref == "refs/heads/main" and actor == "Zennay"
            and triggering_actor == "Zennay")


class ArtifactRetentionHostedProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.validate, cls.audit = jobs(cls.source)
        cls.gate = cls.audit.split("    if: >-\n", 1)[1].split(
            "    runs-on:", 1)[0]

    def test_all_jobs_are_hosted_and_no_self_hosted_labels(self):
        for fragment in (self.validate, self.audit):
            self.assertIn("runs-on: ubuntu-latest", fragment)
            self.assertNotIn("runs-on: [self-hosted", fragment)
            self.assertNotIn("runs-on: self-hosted", fragment)
        self.assertNotIn("scripts/zcloud_vps_runner_guard.py", self.source)

    def test_checkout_exact_sha_and_no_persisted_credentials(self):
        for fragment in (self.validate, self.audit):
            self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", fragment)
            self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", fragment)
            self.assertIn("persist-credentials: false", fragment)
            self.assertIn('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"', fragment)

    def test_pull_request_origin_and_both_actors_required(self):
        for term in (
            "github.event_name == 'pull_request'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
            "github.event.pull_request.head.repo.full_name == github.repository",
        ):
            self.assertIn(term, self.gate)

    def test_manual_requires_owner_origin_and_main(self):
        for term in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "github.triggering_actor == 'Zennay'",
        ):
            self.assertIn(term, self.gate)
        self.assertTrue(manual_admitted("workflow_dispatch", "Zennay/zCloud",
                                        "refs/heads/main", "Zennay", "Zennay"))

    def test_fork_branch_and_replayed_manual_events_denied(self):
        inputs = ("workflow_dispatch", "Zennay/zCloud", "refs/heads/main",
                  "Zennay", "Zennay")
        for i, invalid in ((0, "pull_request"), (1, "other/zCloud"),
                           (2, "refs/heads/feature"), (3, "other"),
                           (4, "other")):
            vals = list(inputs); vals[i] = invalid
            with self.subTest(i=i):
                self.assertFalse(manual_admitted(*vals))

    def test_proof_reads_only_source_files(self):
        for term in ("--repo-root . --max-days 30", "mutation_performed",
                     "tests.test_zcloud_artifact_retention_audit",
                     "tests.test_artifact_retention_hosted_proof_20261014"):
            self.assertIn(term, self.audit)
        for forbidden in ("sudo ", "systemctl ", "sqlite3 ",
                          "git push", "zcloud_runtime.py --db",
                          "urllib.request", "gh api "):
            self.assertNotIn(forbidden, self.source)

    def test_audit_artifact_is_retained_boundedly(self):
        self.assertIn("retention-days: 14", self.audit)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", self.audit)
        self.assertIn("if-no-files-found: error", self.audit)

    def test_no_cancelled_inflight_proof_or_write_access(self):
        self.assertIn("cancel-in-progress: false", self.source)
        self.assertIn("permissions:\n  contents: read", self.source)
        self.assertNotIn("actions: write", self.source)
        self.assertNotIn("contents: write", self.source)


if __name__ == "__main__":
    unittest.main()
