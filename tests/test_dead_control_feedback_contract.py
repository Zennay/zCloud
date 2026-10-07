import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app.js"
ENHANCEMENTS = ROOT / "public" / "enhancements.js"


def section(text: str, start: str, end: str) -> str:
    left = text.index(start)
    right = text.index(end, left + len(start))
    return text[left:right]


class DeadControlFeedbackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP.read_text(encoding="utf-8")
        cls.enhancements = ENHANCEMENTS.read_text(encoding="utf-8")

    def test_disabled_controls_explain_pending_or_unavailable_state(self):
        toggle = section(self.app, "function runnerToggle", "function runnerEventLabel")
        self.assertIn("ChatGPT automation is stopping", toggle)
        self.assertIn("ChatGPT automation is starting", toggle)
        self.assertIn("${pending?'disabled':''}", toggle)
        self.assertIn("${pending?'…':active?'Stop':'Start'}", toggle)

        primary = section(self.app, "function projectPrimaryActions", "function projectCard")
        for marker in ("Starting…", "Working…", "disabled>Working…</button>"):
            self.assertIn(marker, primary)

        workers = section(self.app, "function workerDetailPanel", "async function controlWorker")
        self.assertIn("disabled>Finishing current task…</button>", workers)

        debug = section(self.app, "function workerRow", "function workerScalingPanel")
        self.assertIn('disabled title="Worker is not running"', debug)

    def test_project_runner_actions_show_pending_success_failure_and_reenable(self):
        active = section(self.app, "async function setRunnerActive", "async function forceStartProject")
        for marker in (
            "button.disabled=true",
            "Starting…",
            "Pausing…",
            "setLabel('Failed')",
            "window.alert",
            "button.disabled=false",
        ):
            self.assertIn(marker, active)

        push = section(self.app, "async function pushRunner", "async function setRunnerWorkers")
        for marker in (
            "button.disabled=true",
            "button.textContent='Pushing…'",
            "button.textContent='Push started'",
            "button.textContent='Failed'",
            "window.alert",
            "button.disabled=false",
        ):
            self.assertIn(marker, push)

        force = section(self.app, "async function forceStartProject", "async function restartRunner")
        for marker in (
            "button.disabled=true",
            "button.textContent='Force starting…'",
            "button.textContent='Force started'",
            "button.textContent='Failed'",
            "window.alert",
            "button.disabled=false",
        ):
            self.assertIn(marker, force)

    def test_worker_actions_show_pending_success_failure_and_reenable(self):
        block = section(self.app, "async function controlWorker", "function activityPage")
        for marker in (
            "button.disabled=true",
            "push:'Pushing…'",
            "pause:'Pausing…'",
            "drain:'Finish task…'",
            "start:'Resuming…'",
            "action==='drain'?'Finishing task'",
            "action==='pause'?'Paused'",
            "button.textContent='Failed'",
            "window.alert",
            "button.disabled=false",
        ):
            self.assertIn(marker, block)

    def test_worker_count_and_dynamic_settings_cannot_silently_stall(self):
        count = section(self.app, "async function setRunnerWorkers", "function dynamicWorkerControl")
        for marker in (
            "input.disabled=true",
            "window.alert",
            "input.value=previous",
            "finally{input.disabled=false}",
        ):
            self.assertIn(marker, count)

        dynamic = section(self.app, "async function saveDynamicWorkerSettings", "async function setRunnerActive")
        for marker in (
            "button.disabled=true",
            "button.textContent='Saving…'",
            "button.textContent='Saved'",
            "window.alert",
            "finally{button.disabled=false}",
        ):
            self.assertIn(marker, dynamic)

    def test_attention_resolution_has_failure_feedback_and_recovery(self):
        click = self.app[self.app.index("document.addEventListener('click'"):]
        for marker in (
            "attentionResolve.disabled=true",
            "attentionResolve.disabled=false",
            "$('notice').hidden=false",
            "Could not mark the attention item done:",
        ):
            self.assertIn(marker, click)

    def test_resource_priority_has_pending_degraded_and_failure_feedback(self):
        block = self.enhancements[
            self.enhancements.index("document.addEventListener('change'") :
        ]
        for marker in (
            "el.disabled=true",
            "Priority saved. The live VPS weight could not be applied yet",
            "Project priority could not be saved:",
            "finally{",
            "el.disabled=false",
        ):
            self.assertIn(marker, block)

    def test_restart_actions_have_confirmation_and_visible_outcome(self):
        chat = section(self.app, "async function restartRunner", "async function restartFirefoxInitiator")
        for marker in (
            "window.confirm",
            "button.disabled=true",
            "button.textContent='Restart queued'",
            "window.alert",
            "button.disabled=false",
        ):
            self.assertIn(marker, chat)

        firefox = section(self.app, "async function restartFirefoxInitiator", "/*wd:start*/")
        for marker in (
            "window.confirm",
            "button.disabled=true",
            "button.textContent='Herstarten…'",
            "button.textContent='Initiator restart'",
            "window.alert",
            "button.disabled=false",
            "button.textContent='Restart Firefox initiator'",
        ):
            self.assertIn(marker, firefox)

    def test_proof_workflow_is_read_only_and_exact_head(self):
        workflow = (
            ROOT / ".github/workflows/zcloud-dead-controls-contract.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", workflow)
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
