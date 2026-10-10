"""Exercise the global worker UI without a VPS, browser mutation or credentials."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"
CSS = ROOT / "public" / "design-system.css"


class GlobalWorkerConsoleContractTests(unittest.TestCase):
    def test_real_worker_slot_rendering_and_busy_guard(self):
        # Evaluate only declarations/functions, before document event listeners;
        # this exercises the actual browser view-builder with realistic data.
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const boot = source.slice(0, source.indexOf('let draggedProject=null;'));
assert.ok(boot.length > 50000, 'worker UI definitions missing');
const ctx = vm.createContext({});
vm.runInContext(boot, ctx, {timeout: 2000});
vm.runInContext('DATA = {dynamic_workers:{chatgpt_count:2,claude_count:1},projects:[{id:"supa",name:"Supa"},{id:"lightup",name:"LightUp"}],chatgpt_runners:{supa:{workers:[{worker_id:"supa::w1",conversation_id:"a123"}]},lightup:{workers:[{worker_id:"lightup::w1",conversation_id:"b234"}]}}};', ctx);
vm.runInContext('WORKER_DEBUG = {workers:[{global_slot:1,project:"supa",worker:"supa::w1",provider:"chatgpt",verdict:"waiting",desired_state:"running"},{global_slot:3,project:"lightup",worker:"lightup::w1",provider:"claude",verdict:"generating",desired_state:"running"}]};', ctx);
let rows = vm.runInContext('globalWorkerRows()',ctx);
assert.equal((rows.match(/class="global-worker-row"/g) || []).length,3);
assert.match(rows,/value="supa" selected/);
assert.match(rows,/data-worker-id="supa::w1"/);
assert.match(rows,/data-global-worker-assign="3" disabled/);
assert.match(rows,/https:\/\/chatgpt.com\/c\/a123/);
assert.match(rows,/https:\/\/claude.ai\/chat\/b234/);
vm.runInContext('GLOBAL_WORKER_PROJECT_REQUESTS[2]="supa"',ctx);
rows=vm.runInContext('globalWorkerRows()',ctx);
assert.match(rows,/data-global-worker-project="2"[^>]*>[\s\S]*?value="supa" selected/);
assert.match(rows,/data-global-worker-assign="2" title=/);
vm.runInContext('WORKER_DEBUG=null;',ctx);
rows=vm.runInContext('globalWorkerRows()',ctx);
assert.match(rows,/Waiting for verified status/);
assert.doesNotMatch(rows,/data-worker-id="supa::w1"/);
const ui=vm.runInContext('globalWorkerConsole()',ctx);
assert.match(ui,/data-save-dynamic-workers/);
assert.match(ui,/Worker assignments/);
assert.match(source,/fetch\('\/api\/runner-control'/);
assert.match(source,/action:'start'/);
assert.match(source,/No immediate slot switch is guaranteed|no immediate slot switch is guaranteed/);
"""
        result = subprocess.run(
            ["node", "-e", script, str(APP)], cwd=ROOT,
            capture_output=True, text=True, check=False, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_overview_control_order_and_no_project_primary_actions(self):
        source = APP.read_text(encoding="utf-8")
        overview = source[source.index("function overview(){"):source.index("\nfunction aiRunPanel",source.index("function overview(){"))]
        cards = source[source.index("function projectCard(p)"):source.index("\nfunction attentionPanel",source.index("function projectCard(p)"))]
        detail = source[source.index("function detail(p){"):source.index("\nfunction workerDetailPanel",source.index("function detail(p){"))]
        self.assertLess(overview.index("globalWorkerConsole()"),overview.index("attentionPanel()"))
        self.assertLess(overview.index("globalWorkerConsole()"),overview.index('class="project-grid"'))
        self.assertNotIn("projectPrimaryActions(p)", cards)
        self.assertNotIn("mobileRunnerControls(p)", cards)
        self.assertNotIn("projectPrimaryActions(p,'detail')", detail)
        self.assertIn("data-global-worker-project", source)
        self.assertIn("requestGlobalWorkerProject", source)

    def test_layout_breakpoints_and_panel_prominence(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(".worker-console-heading", css)
        self.assertIn(".global-worker-row", css)
        self.assertIn(".global-worker-actions", css)
        self.assertIn("@media(max-width:760px)", css)
        self.assertIn("@media(max-width:360px)", css)
        self.assertIn("min-width:0", css)
        self.assertIn("overflow-wrap:anywhere", css)


if __name__ == "__main__":
    unittest.main()
