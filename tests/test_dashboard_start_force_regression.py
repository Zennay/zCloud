import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardStartForceRegressionTests(unittest.TestCase):
    def test_project_start_fans_out_to_dynamic_workers_even_with_legacy_target(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        worker_keys_start = background.index("function workerKeysFor(projectId, activeOnly = false)")
        worker_keys_end = background.index("\n\nfunction runProject", worker_keys_start)
        worker_keys = background[worker_keys_start:worker_keys_end]

        self.assertIn("t.project_id !== projectId && t.base_project_id === projectId", worker_keys)
        self.assertLess(worker_keys.index("if (childKeys.length) return childKeys"), worker_keys.index("if (targets[projectId])"))

        for name in ("newProjectChat", "startProject", "pauseProject", "pushProject"):
            start = background.index(f"async function {name}")
            next_start = background.find("\nasync function ", start + 1)
            block = background[start: next_start if next_start >= 0 else len(background)]
            self.assertIn("workerKeys.some(key => key !== projectId)", block, name)

        start = background.index("async function startProject(projectId, commandId)")
        pause = background.index("async function pauseProject(", start)
        start_block = background[start:pause]
        self.assertIn("const workerKeys = workerKeysFor(projectId);", start_block)
        self.assertNotIn("workerKeysFor(projectId, true)", start_block)

    def test_dashboard_exposes_force_start_and_targets_worker_slots(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-runner-force-start="', app)
        self.assertIn(">Force start</button>", app)
        self.assertIn("async function forceStartProject(projectId,button)", app)
        self.assertIn("const workers=[...new Set((runner.workers||[]).map(w=>String(w.worker_id||'').trim()).filter(Boolean))];", app)
        self.assertIn("JSON.stringify({project_id:workerId,action:'start',force:true})", app)
        self.assertIn("forceStartAction.dataset.runnerForceStart", app)

    def test_backend_force_start_bypasses_start_dedupe_and_cooldown(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("force_start=action=='start' and payload.get('force') is True", server)
        self.assertIn("if force_start and inflight and inflight['action']==action:", server)
        self.assertIn("Superseded by explicit Force start", server)
        self.assertIn("if recent and not force_start:", server)
        self.assertIn("'forced':force_start", server)

    def test_dashboard_asset_revision_changes(self):
        index = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/app.js?v=r4", index)


if __name__ == "__main__":
    unittest.main()
