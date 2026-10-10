"""Regression checks for the shared dashboard visual tokens and small-screen contract.

These verify source-level invariants; a browser screenshot/viewport proof is still
needed before claiming that every rendered card is visually correct.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


class DashboardDesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (PUBLIC / "style.css").read_text(encoding="utf-8")
        cls.enhancements = (PUBLIC / "enhancements.css").read_text(encoding="utf-8")
        cls.system = (PUBLIC / "design-system.css").read_text(encoding="utf-8")
        cls.html = (PUBLIC / "index.html").read_text(encoding="utf-8")

    def test_shared_styles_load_after_existing_dashboard_styles(self):
        self.assertIn('href="/design-system.css?v=r3"', self.html)
        self.assertLess(self.html.index('href="/style.css"'),
                        self.html.index('href="/enhancements.css?v=r3"'))
        self.assertLess(self.html.index('href="/enhancements.css?v=r3"'),
                        self.html.index('href="/design-system.css?v=r2"'))

    def test_all_static_css_variables_resolve_to_declared_tokens(self):
        source = "\n".join((self.base, self.enhancements, self.system))
        declared = set(re.findall(r"(--[a-zA-Z][\w-]*)\s*:", source))
        used = set(re.findall(r"var\(\s*(--[a-zA-Z][\w-]*)", source))
        self.assertEqual(set(), used - declared,
                         f"Unresolved dashboard CSS variables: {sorted(used - declared)}")

    def test_scaling_panels_have_defined_border_background_and_state_colors(self):
        for name in ("--line", "--panel-soft", "--success", "--warning", "--accent"):
            self.assertRegex(self.system, rf"{re.escape(name)}\s*:")
        self.assertIn(".project-scaling-chip", self.system)
        self.assertIn(".worker-scaling-row", self.system)
        self.assertIn("overflow-wrap: anywhere", self.system)

    def test_cards_wrap_and_touch_controls_survive_narrow_phones(self):
        self.assertIn("min-width: 0", self.system)
        self.assertIn("max-width: 100%", self.system)
        self.assertIn("--ui-min-touch: 44px", self.system)
        self.assertIn("@media (max-width: 430px)", self.system)
        self.assertIn("flex-direction: column", self.system)
        self.assertIn(".project-action-buttons > button", self.system)


if __name__ == "__main__":
    unittest.main()
