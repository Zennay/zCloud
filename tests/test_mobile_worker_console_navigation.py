"""Phone-specific navigation and touch layout regression checks.

The Node VM exercises the actual registered shortcut listener with mocked DOM
events. These are non-mutating UI tests, not live production VPS tests.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MobileWorkerConsoleNavigationTests(unittest.TestCase):
    def test_mobile_shortcut_routes_to_verified_workers_panel(self):
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf("document.addEventListener('click',async e=>{");
const end = src.indexOf("document.addEventListener('focusin'", start);
assert.ok(start > 0 && end > start, 'delegated click handler missing');
const handlerSource = src.slice(start, end);
let scrolled = 0, focused = 0, prevented = 0, stopped = 0;
const panel = {
  scrollIntoView: args => {assert.equal(args.block,'start'); scrolled++},
  focus: args => {assert.equal(args.preventScroll,true);focused++}
};
const registered = {};
const ctx = vm.createContext({
  document: {addEventListener: (name, fn) => {registered[name] = fn}},
  window: {scrollTo: () => {}, requestAnimationFrame: fn => fn()},
  location: {hash: '#overview'},
  URLSearchParams,
  $: id => id === 'workers' ? panel : null,
  route: 'overview',
  filter: '',
  routeVersion: 0,
  DATA: null,
  scrollToWorkersOnOverview: false,
});
vm.runInContext(handlerSource, ctx, {timeout:2000});
assert.equal(typeof registered.click, 'function');
const click = () => registered.click({
  target: {closest: sel => sel === '[data-jump-workers]' ? {dataset:{jumpWorkers:''}} : null},
  preventDefault: () => prevented++,
  stopPropagation: () => stopped++,
});
(async() => {
  await click();
  assert.equal(scrolled,1,'Overview shortcut must scroll to worker panel');
  assert.equal(focused,1,'Overview shortcut must focus worker panel');
  vm.runInContext("route='project/supa';location.hash='#project/supa'",ctx);
  await click();
  assert.equal(ctx.location.hash,'#overview');
  assert.equal(ctx.scrollToWorkersOnOverview,true);
  assert.equal(scrolled,1,'Do not scroll before view change');
  const nstart=src.indexOf('function navigate(){');
  const nend=src.indexOf('let draggedProject=null;',nstart);
  vm.runInContext(src.slice(nstart,nend)+';navigate()',ctx,{timeout:2000});
  assert.equal(scrolled,2,'Project → Overview navigation must scroll');
  assert.equal(focused,2,'New Overview panel receives focus');
  assert.equal(ctx.scrollToWorkersOnOverview,false,'One-shot navigation flag resets');
  assert.equal(prevented,2);
  assert.equal(stopped,2);
})().catch(err=>{console.error(err);process.exitCode=1});
"""
        run = subprocess.run(
            ["node", "-e", script, str(ROOT / "public" / "app.js")],
            capture_output=True, text=True, cwd=ROOT,
            timeout=20, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_phone_navigation_is_four_reachable_destinations(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        nav = re.search(r'<nav class="mobile-nav"[^>]*>(.*?)</nav>',html,re.S)
        self.assertIsNotNone(nav)
        self.assertEqual(len(re.findall(r'<a\s',nav.group(1))),4)
        self.assertIn('data-jump-workers',nav.group(1))
        self.assertIn('aria-label="Jump to worker management"',nav.group(1))
        self.assertIn('viewport-fit=cover',html)
        self.assertIn('/design-system.css?v=r2',html)
        self.assertIn('/app.js?v=r6',html)

    def test_phone_workers_remain_visible_and_readable(self):
        css = (ROOT / "public" / "design-system.css").read_text(encoding="utf-8")
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn(".mobile-project-nav.is-overview {display:none}",css)
        self.assertIn("scroll-padding-bottom:",css)
        self.assertIn("safe-area-inset-bottom",css)
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))",css)
        self.assertIn("main {",css)
        self.assertIn("@media (max-width: 760px)",css)
        self.assertIn("@media (max-width: 380px)",css)
        self.assertIn("#workers {scroll-margin-top:12px}",css)
        self.assertIn("font-size:16px; /* prevent iOS select zoom */",css)
        self.assertIn("min-height:48px",css)
        self.assertIn("overflow-wrap:anywhere",css)
        self.assertIn("mobileProjects.classList.toggle('is-overview',route==='overview')",app)
        self.assertIn("tabindex=\"-1\"",app)
        self.assertIn("data-global-worker-project",app)
        self.assertNotIn("min-height:32px",css)


if __name__ == "__main__":
    unittest.main()
