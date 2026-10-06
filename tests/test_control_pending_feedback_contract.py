import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"


def function_block(source: str, name: str, next_name: str) -> str:
    start = source.index(f"async function {name}(")
    end = source.index(f"\nasync function {next_name}(", start)
    return source[start:end]


class ControlPendingFeedbackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP.read_text(encoding="utf-8")

    def test_project_start_pause_waits_for_backend_and_shows_pending_state(self):
        block = function_block(self.app, "setRunnerActive", "forceStartProject")
        self.assertIn("button.disabled=true", block)
        self.assertIn("'Starting…'", block)
        self.assertIn("'Pausing…'", block)
        self.assertIn("await fetch('/api/runner-control'", block)
        self.assertIn("if(!response.ok){throw new Error", block)
        self.assertIn("'Started'", block)
        self.assertIn("'Paused'", block)
        self.assertLess(block.index("await fetch('/api/runner-control'"), block.index("'Started'"))
        self.assertLess(block.index("await fetch('/api/runner-control'"), block.index("'Paused'"))
        self.assertIn("setLabel('Failed')", block)
        self.assertIn("button.disabled=false", block)

    def test_force_start_waits_for_all_backend_responses_before_success_copy(self):
        block = function_block(self.app, "forceStartProject", "pushRunner")
        self.assertIn("button.disabled=true;button.textContent='Force starting…'", block)
        self.assertIn("await Promise.all(", block)
        self.assertIn("await fetch('/api/runner-control'", block)
        self.assertIn("if(!response.ok)throw new Error", block)
        self.assertIn("button.textContent='Force started'", block)
        self.assertLess(block.index("await Promise.all("), block.index("button.textContent='Force started'"))
        self.assertIn("button.textContent='Failed'", block)
        self.assertIn("button.disabled=false", block)

    def test_push_waits_for_backend_before_success_copy(self):
        block = function_block(self.app, "pushRunner", "setRunnerWorkers")
        self.assertIn("button.disabled=true;button.textContent='Pushing…'", block)
        self.assertIn("await fetch('/api/runner-control'", block)
        self.assertIn("button.textContent='Push started'", block)
        self.assertLess(block.index("await fetch('/api/runner-control'"), block.index("button.textContent='Push started'"))
        self.assertIn("button.textContent='Failed'", block)
        self.assertIn("button.disabled=false", block)

    def test_worker_count_disables_input_and_rolls_back_on_failure(self):
        start = self.app.index("async function setRunnerWorkers(")
        block = self.app[start:self.app.index("\nfunction dynamicWorkerControl(", start)]
        self.assertIn("const previous=input.value;input.disabled=true", block)
        self.assertIn("await fetch('/api/runner-workers'", block)
        self.assertIn("input.value=String(data.worker_count)", block)
        self.assertIn("catch(error){input.value=previous", block)
        self.assertIn("finally{input.disabled=false}", block)

    def test_dynamic_worker_save_has_pending_confirmed_and_error_states(self):
        block = function_block(self.app, "saveDynamicWorkerSettings", "restartFirefoxInitiator")
        self.assertIn("button.disabled=true", block)
        self.assertIn("button.textContent='Saving…'", block)
        self.assertIn("await fetch('/api/dynamic-workers'", block)
        self.assertIn("reconciled!==true", block)
        self.assertIn("button.textContent='Saved'", block)
        self.assertLess(block.index("await fetch('/api/dynamic-workers'"), block.index("button.textContent='Saved'"))
        self.assertIn("window.alert", block)
        self.assertIn("finally{button.disabled=false}", block)

    def test_individual_worker_actions_expose_pending_success_and_failure(self):
        start = self.app.index("async function controlWorker(")
        block = self.app[start:self.app.index("\nfunction activityPage(", start)]
        self.assertRegex(block, re.compile(r"labels=\{push:'Pushing…',pause:'Pausing…',drain:'Finish task…',start:'Resuming…'\}"))
        self.assertIn("button.disabled=true", block)
        self.assertIn("await fetch('/api/runner-control'", block)
        self.assertIn("button.textContent='Failed'", block)
        self.assertIn("button.disabled=false", block)

    def test_attention_resolution_disables_until_backend_result_and_surfaces_failure(self):
        marker = "const attentionResolve=e.target.closest('[data-attention-resolve]')"
        start = self.app.index(marker)
        block = self.app[start:self.app.index("const dynamicSave=", start)]
        self.assertIn("attentionResolve.disabled=true", block)
        self.assertIn("await post('/api/portfolio-attention'", block)
        self.assertIn("DATA.attention_needed=", block)
        self.assertLess(block.index("await post('/api/portfolio-attention'"), block.index("DATA.attention_needed="))
        self.assertIn("attentionResolve.disabled=false", block)
        self.assertIn("Could not mark the attention item done:", block)

    def test_server_reported_pending_state_is_visible_on_primary_controls(self):
        self.assertIn("pending=['pending','dispatched'].includes(r.command?.status)", self.app)
        self.assertIn("$" + "{pending?'Starting…':'Start work'}", self.app)
        self.assertIn("$" + "{pending?'Working…':'Continue work'}", self.app)
        self.assertIn("pending?'Action is being executed'", self.app)


if __name__ == "__main__":
    unittest.main()
