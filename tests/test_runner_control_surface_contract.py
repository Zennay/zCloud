import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"


def section(text: str, start: str, end: str) -> str:
    left = text.index(start)
    right = text.index(end, left + len(start))
    return text[left:right]


class RunnerControlSurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = APP.read_text(encoding="utf-8")

    def test_project_control_surface_exposes_current_state_and_primary_actions(self):
        block = section(self.text, "function projectPrimaryActions", "function projectCard")
        self.assertIn("r.generating?'Working now':'Ready for the next step'", block)
        self.assertIn(":'Paused'", block)
        self.assertIn('data-runner-action="start"', block)
        self.assertIn("Start work", block)
        self.assertIn('data-runner-push="${esc(p.id)}"', block)
        self.assertIn("Continue work", block)
        self.assertIn('data-runner-action="pause"', block)
        self.assertIn(">Pause</button>", block)
        self.assertIn("['pending','dispatched'].includes(r.command?.status)", block)
        self.assertIn("Starting…", block)
        self.assertIn("Working…", block)

    def test_project_controls_render_on_card_and_detail(self):
        card = section(self.text, "function projectCard", "function attentionPanel")
        detail_start = self.text.index("function detail(")
        worker_start = self.text.index("function workerDetailPanel", detail_start)
        detail = self.text[detail_start:worker_start]
        self.assertIn("${projectPrimaryActions(p)}", card)
        self.assertIn("${projectPrimaryActions(p,'detail')}", detail)
        self.assertIn("${workerDetailPanel(p)}", detail)

    def test_worker_surface_exposes_push_drain_pause_resume_by_desired_state(self):
        block = section(self.text, "function workerDetailPanel", "async function controlWorker")
        for marker in (
            "paused:'Paused'",
            "draining:'Finishing current task'",
            'data-worker-action="start"',
            ">Resume</button>",
            'data-worker-action="push"',
            ">Push now</button>",
            'data-worker-action="drain"',
            ">Finish current task</button>",
            'data-worker-action="pause"',
            ">Pause</button>",
            "Finishing current task…",
        ):
            self.assertIn(marker, block)
        self.assertRegex(
            block,
            re.compile(
                r"if\(w\.desired_state==='paused'\).*?data-worker-action=\"start\".*?"
                r"else if\(w\.desired_state==='draining'\).*?disabled.*?"
                r"else actions=.*?data-worker-action=\"push\".*?"
                r"data-worker-action=\"drain\".*?data-worker-action=\"pause\"",
                re.S,
            ),
        )

    def test_worker_actions_share_canonical_control_endpoint_and_visible_feedback(self):
        block = section(self.text, "async function controlWorker", "function activityPage")
        self.assertIn("fetch('/api/runner-control'", block)
        self.assertIn("JSON.stringify({project_id:workerId,action})", block)
        self.assertIn("button.disabled=true", block)
        for marker in (
            "push:'Pushing…'",
            "pause:'Pausing…'",
            "drain:'Finish task…'",
            "start:'Resuming…'",
            "action==='drain'?'Finishing task'",
            "action==='pause'?'Paused'",
            "action==='start'?'Resume'",
            "'Push started'",
            "button.textContent='Failed'",
        ):
            self.assertIn(marker, block)

    def test_event_router_keeps_project_and_worker_actions_on_canonical_handlers(self):
        click = self.text[self.text.index("document.addEventListener('click'"):]
        self.assertIn("const workerAction=e.target.closest('[data-worker-action]')", click)
        self.assertIn(
            "await controlWorker(workerAction.dataset.workerId,workerAction.dataset.workerAction,workerAction)",
            click,
        )
        self.assertIn("const toggleAction=e.target.closest('[data-runner-toggle]')", click)
        self.assertIn(
            "await setRunnerActive(toggleAction.dataset.runnerToggle,toggleAction.dataset.runnerAction,toggleAction)",
            click,
        )
        self.assertIn("const pushAction=e.target.closest('[data-runner-push]')", click)
        self.assertIn(
            "await pushRunner(pushAction.dataset.runnerPush,pushAction)",
            click,
        )

    def test_project_actions_use_same_backend_control_route(self):
        start = section(self.text, "async function setRunnerActive", "async function forceStartProject")
        push = section(self.text, "async function pushRunner", "async function setRunnerWorkers")
        self.assertIn("fetch('/api/runner-control'", start)
        self.assertIn("JSON.stringify({project_id:projectId,action})", start)
        self.assertIn("fetch('/api/runner-control'", push)
        self.assertIn("JSON.stringify({project_id:projectId,action:'push'})", push)

    def test_contract_is_read_only_test_of_existing_dashboard(self):
        workflow = (ROOT / ".github/workflows/zcloud-runner-control-surface-contract.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", workflow)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        for forbidden in (
            "sudo ",
            "systemctl ",
            "sqlite3 ",
            "curl -X",
            "gh api --method",
            "/api/runner-control",
        ):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
