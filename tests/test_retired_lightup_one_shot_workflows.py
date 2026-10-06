import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = {
    "lightup-pr13-exact-head-proof.yml": ("13", "8434f6c99460a34c8f0b23a9e465246809848485"),
    "lightup-pr37-exact-head-proof.yml": ("37", "3c300c7a2882940df11cb4c4be0cde422cf91417"),
    "lightup-pr41-exact-head-proof.yml": ("41", "7668943e0f8dd719d95e6da4f30f621cc8adf642"),
    "lightup-pr42-exact-head-proof.yml": ("42", "8afa27a7d1846bdc08ffb221e087360d2378c27d"),
}


class RetiredLightUpOneShotWorkflowTests(unittest.TestCase):
    def test_closed_target_workflows_stay_retired(self):
        for filename in RETIRED:
            with self.subTest(filename=filename):
                self.assertFalse(
                    (WORKFLOWS / filename).exists(),
                    f"closed-target one-shot workflow must stay retired: {filename}",
                )

    def test_retired_target_shas_are_not_reintroduced_in_workflows(self):
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
            if path.is_file()
        )
        for filename, (pr_number, target_sha) in RETIRED.items():
            with self.subTest(filename=filename):
                self.assertNotIn(
                    target_sha,
                    workflow_text,
                    f"retired LightUp PR #{pr_number} exact head reintroduced",
                )


if __name__ == "__main__":
    unittest.main()
