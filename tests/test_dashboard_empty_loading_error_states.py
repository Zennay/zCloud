import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "public" / "app.js").read_text(encoding="utf-8")


class DashboardStateContractTests(unittest.TestCase):
    def test_initial_loading_and_status_region_are_visible(self):
        self.assertIn('id="notice" class="notice" hidden role="status"', INDEX)
        self.assertIn('Loading my workspace…', INDEX)
        self.assertIn('id="view"', INDEX)

    def test_transport_failure_keeps_or_offers_a_recovery_path(self):
        self.assertIn('Connection lost. The last loaded data remains visible; we will retry automatically.', APP)
        self.assertIn('The VPS is temporarily unreachable. Try again or wait for the next refresh.', APP)
        self.assertIn('data-retry', APP)
        self.assertIn("if(e.target.closest('[data-retry]'))refresh(true)", APP)

    def test_cached_snapshot_fallback_is_explicit(self):
        self.assertIn('const cachedStatus=loadCachedStatus()', APP)
        self.assertIn('Live connection is temporarily unavailable. Showing the last successful dashboard snapshot while reconnecting automatically.', APP)
        self.assertIn("'Last successful measurement '+clock(DATA.time)+' · reconnecting…'", APP)

    def test_partial_history_failure_is_fail_visible_without_hiding_live_state(self):
        self.assertIn("const fails=results.filter(r=>r.status==='rejected').length", APP)
        self.assertIn('Part of the history is temporarily unavailable. Live measurements remain visible.', APP)

    def test_empty_activity_and_chart_states_are_explicit(self):
        self.assertIn('No activity recorded yet.', APP)
        self.assertIn('No activity for this project yet.', APP)
        self.assertIn('No measurements in this period.', APP)
        self.assertIn('New history is recorded every five minutes.', APP)

    def test_attention_zero_state_explains_why_nothing_is_shown(self):
        self.assertIn('Nothing needs you right now', APP)
        self.assertIn('Workers can keep moving without a human gate.', APP)

    def test_unknown_project_has_a_clear_escape_hatch(self):
        self.assertIn('This project was not found.', APP)
        self.assertIn('<a href="#overview">Back to overview</a>', APP)

    def test_user_visible_errors_use_text_content_for_runtime_messages(self):
        self.assertIn("$('notice').textContent=stale?", APP)
        self.assertIn("$('notice').textContent='Could not mark the attention item done: '+(err.message||err)", APP)


if __name__ == "__main__":
    unittest.main()
