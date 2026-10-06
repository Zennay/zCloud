import tempfile
import unittest
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"
SERVER = ROOT / "server.py"


class AttentionNeededContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = server.DB
        self.original_seed = server.PORTFOLIO_QUEUE_SEED_FILE
        root = Path(self.tmp.name)
        server.DB = root / "attention-test.db"
        server.PORTFOLIO_QUEUE_SEED_FILE = root / "seed.json"
        server.PORTFOLIO_QUEUE_SEED_FILE.write_text("[]", encoding="utf-8")
        server.init_db()

    def tearDown(self):
        server.DB = self.original_db
        server.PORTFOLIO_QUEUE_SEED_FILE = self.original_seed
        self.tmp.cleanup()

    def test_attention_items_are_open_only_ordered_and_resolvable(self):
        later = server.portfolio_attention_add(
            "cloud",
            "Owner review can wait",
            "External review is needed later.",
            "https://example.invalid/later",
            "later",
            "attention-later",
        )
        urgent = server.portfolio_attention_add(
            "raiseai",
            "Physical watch validation required",
            "Complete the physical Galaxy Watch check.",
            "https://example.invalid/urgent",
            "urgent",
            "attention-urgent",
        )

        open_items = server.portfolio_attention_items()
        ids = [x["attention_id"] for x in open_items]
        self.assertLess(ids.index("attention-urgent"), ids.index("attention-later"))
        self.assertTrue(all(x["status"] == "open" for x in open_items))
        self.assertTrue(any(x["attention_id"] == "ulab-ha006-usability" for x in open_items))

        result = server.portfolio_attention_resolve(urgent["attention_id"])
        self.assertTrue(result["resolved"])
        remaining = {x["attention_id"] for x in server.portfolio_attention_items()}
        self.assertNotIn("attention-urgent", remaining)
        self.assertIn("attention-later", remaining)
        self.assertIn("ulab-ha006-usability", remaining)

        all_items = {x["attention_id"]: x for x in server.portfolio_attention_items(include_resolved=True)}
        self.assertEqual("resolved", all_items[urgent["attention_id"]]["status"])
        self.assertTrue(all_items[urgent["attention_id"]]["resolved_at"])
        self.assertEqual("open", all_items[later["attention_id"]]["status"])

    def test_attention_add_is_idempotent_for_same_project_and_action(self):
        first = server.portfolio_attention_add(
            "cloud", "Approve external gate", "first detail", severity="attention"
        )
        second = server.portfolio_attention_add(
            "cloud", "Approve external gate", "updated detail", severity="urgent"
        )
        self.assertEqual(first["attention_id"], second["attention_id"])
        rows = [x for x in server.portfolio_attention_items() if x["attention_id"] == first["attention_id"]]
        self.assertEqual(1, len(rows))
        self.assertEqual("updated detail", rows[0]["detail"])
        self.assertEqual("urgent", rows[0]["severity"])

    def test_overview_places_attention_before_project_grid(self):
        app = APP.read_text(encoding="utf-8")
        start = app.index("function overview()")
        end = app.index("function aiRunPanel", start)
        block = app[start:end]
        self.assertIn("attentionPanel()", block)
        self.assertIn('class="project-grid" id="projectGrid"', block)
        self.assertLess(block.index("attentionPanel()"), block.index('class="project-grid" id="projectGrid"'))

    def test_attention_panel_is_human_external_only_and_project_filterable(self):
        app = APP.read_text(encoding="utf-8")
        start = app.index("function attentionPanel(")
        end = app.index("function archivedPanel", start)
        block = app[start:end]
        self.assertIn("DATA?.attention_needed", block)
        self.assertIn("all.filter(item=>item.project_id===projectId)", block)
        self.assertIn("Only human, physical or external gates appear here.", block)
        self.assertIn("They never occupy a worker slot.", block)
        self.assertIn("Nothing needs you right now", block)
        self.assertIn('data-attention-resolve="${esc(item.attention_id)}"', block)

    def test_resolve_control_has_pending_and_failure_feedback(self):
        app = APP.read_text(encoding="utf-8")
        marker = "const attentionResolve=e.target.closest('[data-attention-resolve]')"
        start = app.index(marker)
        end = app.index("const dynamicSave=", start)
        block = app[start:end]
        self.assertIn("attentionResolve.disabled=true", block)
        self.assertIn("post('/api/portfolio-attention'", block)
        self.assertIn("action:'resolve'", block)
        self.assertIn("DATA.attention_needed=", block)
        self.assertIn("attentionResolve.disabled=false", block)
        self.assertIn("Could not mark the attention item done", block)

    def test_status_and_api_use_canonical_attention_backend(self):
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn("'attention_needed':portfolio_attention_items()", source)
        endpoint = source[source.index("if u.path=='/api/portfolio-attention'"):]
        endpoint = endpoint[: endpoint.index("if u.path=='/api/feature-flags'")]
        self.assertIn("if not action_request_allowed(self)", endpoint)
        self.assertIn("portfolio_attention_resolve", endpoint)
        self.assertIn("portfolio_attention_add", endpoint)
        self.assertIn("Ongeldige attention actie", endpoint)


if __name__ == "__main__":
    unittest.main()
