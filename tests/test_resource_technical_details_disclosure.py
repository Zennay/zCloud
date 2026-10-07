import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENHANCEMENTS = ROOT / "public" / "enhancements.js"


class ResourceTechnicalDetailsDisclosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = ENHANCEMENTS.read_text(encoding="utf-8")
        start = cls.text.index("  function resourcePanel(){")
        end = cls.text.index("  function incidentPanel(){", start)
        cls.resource = cls.text[start:end]

    def test_technical_metrics_are_behind_one_native_disclosure(self):
        self.assertIn(
            '<details class="section-details resource-details"><summary>Technical details</summary>',
            self.resource,
        )
        self.assertIn('<section class="resource-tech-panel"', self.resource)
        self.assertIn("</section></details>", self.resource)
        for technical in (
            "CPU '+cpu+' · RAM '+mem+' · weight ",
            "system/unattributed",
            "Weights: Background 100",
        ):
            self.assertIn(technical, self.resource)

    def test_priority_controls_and_user_facing_state_stay_visible(self):
        self.assertIn(
            '<div class="resource-grid">'+rows+'</div>'+details+'</div>',
            self.resource,
        )
        self.assertIn('data-resource-priority="', self.resource)
        self.assertIn("Running on the VPS", self.resource)
        self.assertIn("No dedicated VPS worker", self.resource)

    def test_disclosure_does_not_change_priority_mutation_handler(self):
        after = self.text[self.text.index("document.addEventListener('change'") :]
        self.assertIn("'/api/resource-priority'", after)
        self.assertIn("el.disabled=true", after)
        self.assertIn("el.disabled=false", after)
        self.assertIn("await refresh(true)", after)


if __name__ == "__main__":
    unittest.main()
