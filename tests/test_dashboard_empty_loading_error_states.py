from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "public" / "app.js").read_text(encoding="utf-8")


class DashboardStateContractTests:
    def test_initial_loading_and_status_region_are_visible(self):
        assert 'id="notice" class="notice" hidden role="status"' in INDEX
        assert 'Loading my workspace…' in INDEX
        assert 'id="view"' in INDEX

    def test_transport_failure_keeps_or_offers_a_recovery_path(self):
        assert 'Connection lost. The last loaded data remains visible; we will retry automatically.' in APP
        assert 'The VPS is temporarily unreachable. Try again or wait for the next refresh.' in APP
        assert 'data-retry' in APP
        assert "if(e.target.closest('[data-retry]'))refresh(true)" in APP

    def test_cached_snapshot_fallback_is_explicit(self):
        assert 'const cachedStatus=loadCachedStatus()' in APP
        assert 'Live connection is temporarily unavailable. Showing the last successful dashboard snapshot while reconnecting automatically.' in APP
        assert "'Last successful measurement '+clock(DATA.time)+' · reconnecting…'" in APP

    def test_partial_history_failure_is_fail_visible_without_hiding_live_state(self):
        assert "const fails=results.filter(r=>r.status==='rejected').length" in APP
        assert 'Part of the history is temporarily unavailable. Live measurements remain visible.' in APP

    def test_empty_activity_and_chart_states_are_explicit(self):
        assert 'No activity recorded yet.' in APP
        assert 'No activity for this project yet.' in APP
        assert 'No measurements in this period.' in APP
        assert 'New history is recorded every five minutes.' in APP

    def test_attention_zero_state_explains_why_nothing_is_shown(self):
        assert 'Nothing needs you right now' in APP
        assert 'Workers can keep moving without a human gate.' in APP

    def test_unknown_project_has_a_clear_escape_hatch(self):
        assert 'This project was not found.' in APP
        assert '<a href="#overview">Back to overview</a>' in APP

    def test_user_visible_errors_use_text_content_for_runtime_messages(self):
        assert "$('notice').textContent=stale?" in APP
        assert "$('notice').textContent='Could not mark the attention item done: '+(err.message||err)" in APP


if __name__ == "__main__":
    import unittest

    unittest.main()
