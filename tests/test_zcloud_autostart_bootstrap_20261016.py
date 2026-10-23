"""Regressions for the real autonomous worker roster and a nonblank dashboard."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = {"mixlab", "fuckball", "tuddy"}


class AutostartAndBootstrapTests(unittest.TestCase):
    def test_new_projects_have_claimable_autostart_work_with_bounded_caps(self):
        contracts = json.loads((ROOT / "project-contracts.json").read_text())["projects"]
        queue = json.loads((ROOT / "portfolio_queue.seed.json").read_text())
        ids = [task["queue_id"] for task in queue]
        self.assertEqual(len(ids), len(set(ids)), "seed queue IDs must remain idempotent")
        for project_id in AUTOSTART:
            with self.subTest(project_id=project_id):
                contract = contracts[project_id]
                self.assertEqual("ai_worker", contract["autonomy"]["mode"])
                self.assertTrue(contract["autonomy"]["auto_start"])
                self.assertEqual(1, contract["ai_worker_cap"])
                self.assertEqual("background", contract["compute"]["priority"])
                tasks = [item for item in queue if item["project_id"] == project_id]
                self.assertEqual(1, len(tasks), "exactly one new ready seed avoids duplicated work")
                task = tasks[0]
                self.assertTrue(task["eligible"])
                self.assertEqual("queued", task["status"])
                self.assertTrue(task["completion_criteria"])
                self.assertTrue(task["source_url"].startswith("https://app.notion.com/"))
        self.assertTrue(contracts["zsentinel"]["autonomy"]["auto_start"])
        self.assertTrue(contracts["tradengine"]["autonomy"]["auto_start"])

    def test_shipped_dashboard_scripts_are_allowed_and_cache_busted(self):
        index = (ROOT / "public/index.html").read_text()
        server = (ROOT / "server.py").read_text()
        self.assertIn('/app.js?v=20261016-auto', index)
        self.assertIn('/enhancements.js?v=20261016-auto', index)
        self.assertNotIn('/zcloud-boot-guard.js', index)
        self.assertIn("'/app.js':'app.js'", server)
        self.assertIn("'/enhancements.js':'enhancements.js'", server)
        self.assertIn("zcloudBootstrapRecovery", (ROOT / "public/enhancements.js").read_text())
        for script in ("app.js", "enhancements.js"):
            with self.subTest(script=script):
                result = subprocess.run(["node", "--check", str(ROOT / "public" / script)],
                                        capture_output=True, text=True, check=False, timeout=20)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_fallback_renders_on_stuck_loader_but_never_replaces_healthy_ui(self):
        script = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const boot=source.slice(0,source.indexOf("'use strict';\n(function(){"));
assert.match(boot,/zcloudBootstrapRecovery/);
function run(loading){
  const registered={},timers=[];
  let replacement=null;
  const view={
    textContent:loading?'Loading workspace...':'Projects and workers',
    querySelector:()=>loading?{}:null,
    replaceChildren:(child)=>{replacement=child}
  };
  const connection={textContent:'Connected'};
  const document={
    getElementById:(id)=>id==='view'?view:id==='connection'?connection:null,
    createElement:(tag)=>({
      tagName:tag.toUpperCase(),children:[],setAttribute(){},
      appendChild(child){this.children.push(child)},addEventListener(){},
      set type(v){this.buttonType=v},set className(v){this.cls=v}
    })
  };
  const window={addEventListener:(n,fn)=>registered[n]=fn,setTimeout:(fn)=>timers.push(fn),location:{reload(){}}};
  vm.runInNewContext(boot,{window,document},{timeout:1000});
  assert.ok(timers.length);
  timers.shift()();
  return {replacement,connection,registered,timers};
}
const broken=run(true);
assert.ok(broken.replacement,'stuck loading screen should expose an accessible recovery option');
assert.equal(broken.replacement.children[0].textContent,'zCloud kon het dashboard niet openen');
assert.equal(broken.connection.textContent,'Dashboardfout');
assert.ok(broken.replacement.children[2].children.length===0,'reload control should be a standalone button');
const healthy=run(false);
assert.equal(healthy.replacement,null,'a healthy screen must never be replaced');
"""
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "public/enhancements.js")],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=20
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
