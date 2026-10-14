"""Retired one-night recovery must stay absent from deployable Actions workflows.

The 9 October eight-worker incident handler carried actions:write, issues:write,
sudo/service recovery, pool rewrites, force-push and new-chat mutations. A
workflow_dispatch trigger remained callable long after its one-night expiry.
Check the Actions directory itself so renaming the YAML cannot quietly restore
the privileged handler. This test never invokes any VPS or GitHub operation.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRED = WORKFLOWS / "zcloud-overnight-eight-20261009.yml"


class RetiredEightWorkerBurstTests(unittest.TestCase):
    def test_expired_eight_worker_workflow_is_removed(self):
        self.assertFalse(
            RETIRED.exists(),
            "The 9 October one-night eight-worker recovery must not remain dispatchable",
        )

    def test_renamed_expired_handler_cannot_reappear(self):
        forbidden_filenames = (
            "zcloud-overnight-eight-",
            "overnight-eight-worker-watchdog",
        )
        forbidden_source_markers = (
            "zcloud-overnight-eight/1",
            "zcloud-overnight-20261009.json",
            "name: zCloud overnight eight-worker watchdog",
        )
        for path in (*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")):
            with self.subTest(workflow=path.name):
                name = path.name.lower()
                self.assertFalse(
                    any(marker in name for marker in forbidden_filenames),
                    "Retired incident handler was reintroduced under a new filename",
                )
                source = path.read_text(encoding="utf-8")
                self.assertFalse(
                    any(marker.lower() in source.lower() for marker in forbidden_source_markers),
                    "Retired incident handler was reintroduced under a new workflow name",
                )


if __name__ == "__main__":
    unittest.main()
