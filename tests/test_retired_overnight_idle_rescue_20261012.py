"""Static regression: expired privileged rescue entrypoints stay retired.

The 2026-10-09 emergency overnight window has ended. Keep a failed/expired
one-shot workflow from regaining Actions-write, issue-write, or VPS restart
privileges via GitHub's manual dispatch endpoint.
"""
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPIRED = "zcloud-overnight-idle-rescue-20261009.yml"


# A renamed copy of the expired rescue could bypass an exact-filename-only
# retirement test. These distinctive snippets are implementation fingerprints
# of the 2026-10-09 incident-specific restart dispatcher, not a ban on ordinary
# owner-approved incident workflows. Keep this list intentionally narrow.
MAX_WORKFLOW_SOURCE_BYTES = 2 * 1024 * 1024


RETIRED_RESCUE_FINGERPRINTS = frozenset((
    "zcloud-overnight-idle-rescue/1",
    "GUARDED_RESTART_DISPATCHED=1",
    "RESCUE_SKIPPED=no-old-pending-work",
    "OLD_ALLOCATED_PENDING",
))


def unexpected_rescue_fingerprints(directory: Path) -> dict[str, list[str]]:
    """Scan workflow sources offline, never dispatch or inspect VPS services."""
    matches = {}
    for file in sorted(directory.iterdir()):
        if file.suffix.lower() not in {".yml", ".yaml"}:
            continue
        if file.is_symlink() or not file.is_file():
            matches[file.name] = ["untrusted_workflow_file_type"]
            continue
        try:
            size = file.stat().st_size
            if not 0 <= size <= MAX_WORKFLOW_SOURCE_BYTES:
                matches[file.name] = ["oversized_workflow_source"]
                continue
            contents = file.read_text(encoding="utf-8")
            if len(contents.encode("utf-8")) != size:
                matches[file.name] = ["workflow_source_changed_during_scan"]
                continue
        except (OSError, UnicodeError):
            matches[file.name] = ["unreadable_workflow_source"]
            continue
        hits = [needle for needle in sorted(RETIRED_RESCUE_FINGERPRINTS) if needle in contents]
        if hits:
            matches[file.name] = hits
    return matches


class ExpiredOvernightRescueRetirementTests(unittest.TestCase):
    def test_privileged_one_shot_is_not_registered(self):
        self.assertFalse((WORKFLOWS / EXPIRED).exists())

    def test_no_other_workflow_still_references_expired_entrypoint(self):
        # The new hosted-only regression workflow intentionally watches the
        # deleted path in pull_request/push 'paths:' filters. That is *not*
        # a workflow_dispatch or recovery target and must remain covered.
        proof = "zcloud-expired-overnight-rescue-retirement-20261012.yml"
        watcher = (WORKFLOWS / proof).read_text(encoding="utf-8")
        self.assertIn('".github/workflows/' + EXPIRED + '"', watcher)
        self.assertIn("runs-on: ubuntu-latest", watcher)
        self.assertIn("contents: read", watcher)
        self.assertNotIn("actions: write", watcher)
        self.assertNotIn("issues: write", watcher)
        self.assertNotIn("workflow_dispatch:", watcher)
        for candidate in sorted(WORKFLOWS.glob("*.y*ml")):
            if candidate.name == proof:
                continue
            with self.subTest(workflow=candidate.name):
                self.assertNotIn(EXPIRED, candidate.read_text(encoding="utf-8"))

    def test_renamed_expired_rescue_signatures_never_reappear(self):
        self.assertEqual({}, unexpected_rescue_fingerprints(WORKFLOWS))

    def test_renamed_copy_and_alternate_extension_cannot_bypass_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "replacement-restart.yml").write_text(
                "name: different\n# dormant?\nOLD_ALLOCATED_PENDING\n"
                "GUARDED_RESTART_DISPATCHED=1\n", encoding="utf-8",
            )
            (root / "renamed-worker.yaml").write_text(
                "name: another\n# zcloud-overnight-idle-rescue/1\n",
                encoding="utf-8",
            )
            result = unexpected_rescue_fingerprints(root)
            self.assertEqual(set(result), {"replacement-restart.yml", "renamed-worker.yaml"})
            self.assertIn("OLD_ALLOCATED_PENDING", result["replacement-restart.yml"])
            self.assertIn("zcloud-overnight-idle-rescue/1", result["renamed-worker.yaml"])

    def test_safe_manual_recovery_does_not_trigger_old_rescue_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "explicit-owner-manual.yml").write_text(
                "name: owner manual check\non: [workflow_dispatch]\n"
                "permissions:\n  contents: read\n"
                "jobs:\n  check:\n    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            self.assertEqual({}, unexpected_rescue_fingerprints(root))

    def test_retirement_scan_rejects_symlinked_workflows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "safe.txt"
            source.write_text("name: safe", encoding="utf-8")
            (root / "pointed.yml").symlink_to(source)
            self.assertEqual(
                {"pointed.yml": ["untrusted_workflow_file_type"]},
                unexpected_rescue_fingerprints(root),
            )

    def test_scan_rejects_oversized_workflow_before_loading_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "large.yml").write_bytes(b"#" * (MAX_WORKFLOW_SOURCE_BYTES + 1))
            self.assertEqual(
                {"large.yml": ["oversized_workflow_source"]},
                unexpected_rescue_fingerprints(root),
            )

    def test_scan_allows_regular_small_yaml_and_ignores_unrelated_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "safe.yaml").write_text(
                "name: safe\npermissions:\n  contents: read\n", encoding="utf-8",
            )
            (root / "old-rescue.txt").write_text(
                "GUARDED_RESTART_DISPATCHED=1\n", encoding="utf-8",
            )
            self.assertEqual({}, unexpected_rescue_fingerprints(root))

    def test_dated_eight_worker_safety_keeps_automatic_trigger_retired(self):
        # PR #1367 also retired the formerly manually dispatchable eight-worker
        # incident handler. The old assertion incorrectly tried to read the
        # deleted file and failed every unrelated PR's hosted regression.
        # Keep cross-incident protection without resurrecting that workflow.
        path = WORKFLOWS / "zcloud-overnight-eight-20261009.yml"
        self.assertFalse(path.exists(), "Expired eight-worker handler must stay absent")
        for candidate in sorted(WORKFLOWS.iterdir()):
            if candidate.suffix.lower() not in {".yaml", ".yml"}:
                continue
            with self.subTest(workflow=candidate.name):
                self.assertNotIn(
                    "name: zCloud overnight eight-worker watchdog",
                    candidate.read_text(encoding="utf-8"),
                    "Renamed expired eight-worker handler must stay retired",
                )

    def test_no_unsafe_retirement_evidence_is_inferred(self):
        # Deleting an expired workflow is NOT proof of active worker continuity
        # or production availability. Keep this fixture explicit in evidence.
        outcome = {
            "dated_entrypoint_retired": not (WORKFLOWS / EXPIRED).exists(),
            "vps_tested": False,
            "workers_restarted": False,
            "production_promoted": False,
        }
        self.assertTrue(outcome["dated_entrypoint_retired"])
        self.assertFalse(outcome["vps_tested"])
        self.assertFalse(outcome["workers_restarted"])
        self.assertFalse(outcome["production_promoted"])


if __name__ == "__main__":
    unittest.main()
