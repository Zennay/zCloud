import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"
SERVER = ROOT / "server.py"


class WorkerCountSurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP.read_text(encoding="utf-8")
        cls.server = SERVER.read_text(encoding="utf-8")

    def test_worker_count_control_is_rendered_on_card_and_project_detail(self):
        self.assertIn("function mobileRunnerControls(p,placement='card')", self.app)
        self.assertIn("${mobileRunnerControls(p)}</article>", self.app)
        self.assertIn("${mobileRunnerControls(p,'detail')}<details", self.app)
        self.assertIn('data-mobile-runner-project="${esc(p.id)}"', self.app)
        self.assertIn("<small>Workers</small>", self.app)
        self.assertIn("${activeWorkers} active · ${desiredWorkers} desired", self.app)

    def test_worker_count_input_is_bounded_and_accessible(self):
        block = self.app.split("function mobileRunnerControls(p,placement='card'){", 1)[1].split(
            "function projectPrimaryActions", 1
        )[0]
        self.assertIn('type="number"', block)
        self.assertIn('inputmode="numeric"', block)
        self.assertIn('min="1"', block)
        self.assertIn('max="8"', block)
        self.assertIn('step="1"', block)
        self.assertIn('data-runner-workers="${esc(p.id)}"', block)
        self.assertIn('aria-label="Number of ChatGPT workers for ${esc(p.name)}"', block)

    def test_change_handler_routes_both_surfaces_to_same_mutation_path(self):
        self.assertIn(
            "document.addEventListener('change',async e=>{const workerInput=e.target.closest?.('[data-runner-workers]');",
            self.app,
        )
        self.assertIn(
            "await setRunnerWorkers(workerInput.dataset.runnerWorkers,workerInput.value,workerInput);return",
            self.app,
        )

    def test_worker_count_mutation_has_pending_success_and_rollback_behavior(self):
        block = self.app.split("async function setRunnerWorkers(projectId,count,input){", 1)[1].split(
            "function dynamicWorkerControl", 1
        )[0]
        self.assertIn("const previous=input.value;input.disabled=true;", block)
        self.assertIn("fetch('/api/runner-workers'", block)
        self.assertIn("project_id:projectId,worker_count:Number(count)", block)
        self.assertIn("input.value=String(data.worker_count)", block)
        self.assertIn("catch(error){input.value=previous;window.alert", block)
        self.assertIn("finally{input.disabled=false}", block)

    def test_backend_endpoint_validates_and_persists_worker_count(self):
        block = self.server.split("if u.path=='/api/runner-workers':", 1)[1].split(
            "if u.path=='/api/resource-priority':", 1
        )[0]
        self.assertIn("action_request_allowed(self)", block)
        self.assertIn("worker_count=int(payload.get('worker_count'))", block)
        self.assertIn("if project_id not in runner_targets()", block)
        self.assertIn("if worker_count<1 or worker_count>MAX_CHATGPT_WORKERS", block)
        self.assertIn(
            "UPDATE runner_targets SET worker_count=? WHERE project_id=?",
            block,
        )
        self.assertIn(
            "INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id)",
            block,
        )
        self.assertIn("'runner.worker_count'", block)
        self.assertIn("'worker_count':worker_count,'max_workers':MAX_CHATGPT_WORKERS", block)

    def test_contract_does_not_depend_on_mobile_only_navigation(self):
        card = self.app.split("function projectCard(p)", 1)[1].split(
            "function attentionPanel", 1
        )[0]
        detail = self.app.split("function detail(p)", 1)[1].split(
            "function workerDetailPanel", 1
        )[0]
        self.assertRegex(card, re.compile(r"mobileRunnerControls\(p\)"))
        self.assertRegex(detail, re.compile(r"mobileRunnerControls\(p,'detail'\)"))


if __name__ == "__main__":
    unittest.main()
