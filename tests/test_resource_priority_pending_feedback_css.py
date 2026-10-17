from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "public" / "enhancements.css").read_text(encoding="utf-8")
JS = (ROOT / "public" / "enhancements.js").read_text(encoding="utf-8")


class ResourcePriorityPendingFeedbackCssTests(unittest.TestCase):
    def test_existing_handler_disables_before_request_and_reenables_in_finally(self):
        disable = JS.index("el.disabled=true;")
        request = JS.index("await fetch('/api/resource-priority'")
        finally_block = JS.index("}finally{", request)
        reenable = JS.index("el.disabled=false;", finally_block)
        self.assertLess(disable, request)
        self.assertLess(request, finally_block)
        self.assertLess(finally_block, reenable)

    def test_disabled_resource_priority_control_has_visible_pending_copy(self):
        self.assertIn(
            ".resource-row:has(select[data-resource-priority]:disabled)::after",
            CSS,
        )
        self.assertIn("content:'Saving priority…'", CSS)
        self.assertIn("grid-column:1/-1", CSS)

    def test_disabled_resource_priority_control_looks_non_repeatable(self):
        selector = (
            ".resource-row:has(select[data-resource-priority]:disabled) "
            "select[data-resource-priority]"
        )
        self.assertIn(selector, CSS)
        self.assertIn("cursor:wait", CSS)


if __name__ == "__main__":
    unittest.main()
