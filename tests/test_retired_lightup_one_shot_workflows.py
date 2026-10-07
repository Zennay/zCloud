import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RETIRED = {
    "lightup-pr13-exact-head-proof.yml": ("13", "8434f6c99460a34c8f0b23a9e465246809848485"),
    "lightup-pr37-exact-head-proof.yml": ("37", "3c300c7a2882940df11cb4c4be0cde422cf91417"),
    "lightup-pr40-exact-head-proof.yml": ("40", "29e720902e062b215560665677328c59cdc9ec9d"),
    "lightup-pr40-generic-listener-recovery.yml": ("40", "37343030852"),
    "lightup-pr40-zcloud-runner-recovery.yml": ("40", "37341085861"),
    "lightup-pr41-exact-head-proof.yml": ("41", "7668943e0f8dd719d95e6da4f30f621cc8adf642"),
    "lightup-pr42-exact-head-proof.yml": ("42", "8afa27a7d1846bdc08ffb221e087360d2378c27d"),
    "lightup-pr44-exact-head-proof.yml": ("44", "2a678f33ecc71bed09f298f7136a48514ccb15fc"),
    "lightup-pr47-exact-head-proof.yml": ("47", "c1d9e3c15ca3ffe7b2583e4296ea192d4f482f6d"),
    "lightup-pr49-exact-head-proof.yml": ("49", "70b49cebd9d7d43dc5883d190c4afcacebbfd0ff"),
    "lightup-pr50-exact-head-proof.yml": ("50", "ebe1b2e0f03da737e2d9314308bb09d22c77a9c2"),
    "lightup-pr51-exact-head-proof.yml": ("51", "5ad1dbb3654c1c0aff0388999c1761788798328b"),
}


class RetiredLightUpOneShotWorkflowTests(unittest.TestCase):
    def test_closed_target_workflows_stay_retired(self):
        for filename in RETIRED:
            with self.subTest(filename=filename):
                self.assertFalse(
                    (WORKFLOWS / filename).exists(),
                    f"closed-target one-shot workflow must stay retired: {filename}",
                )

    def test_retired_target_markers_are_not_reintroduced_in_workflows(self):
        workflow_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in WORKFLOWS.glob("*.yml")
            if path.is_file()
        )
        for filename, (pr_number, marker) in RETIRED.items():
            with self.subTest(filename=filename):
                self.assertNotIn(
                    marker,
                    workflow_text,
                    f"retired LightUp PR #{pr_number} marker reintroduced",
                )


if __name__ == "__main__":
    unittest.main()
