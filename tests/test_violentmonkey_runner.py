import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ViolentmonkeyPrimaryRunnerTests(unittest.TestCase):
    def test_userscript_is_database_backed_and_fail_closed_on_high(self):
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")

        self.assertIn("@name         zCloud Dynamic Worker", userscript)
        self.assertIn("@match        https://chatgpt.com/*", userscript)
        self.assertIn("@grant        GM_xmlhttpRequest", userscript)
        self.assertIn("@connect      127.0.0.1", userscript)
        self.assertIn('const API = "http://127.0.0.1:8765/api"', userscript)
        self.assertIn('gmRequest("/runner-targets")', userscript)
        self.assertIn('gmRequest("/runner-commands")', userscript)
        self.assertIn('gmRequest("/runner-status"', userscript)
        self.assertIn('gmRequest("/runner-command-result"', userscript)
        self.assertIn('REQUIRED_THINKING_EFFORT = "high"', userscript)
        self.assertIn("ensureHighThinking", userscript)
        self.assertIn('[role="slider"]', userscript)
        self.assertIn('data-testid="model-switcher-dropdown-button"', userscript)
        self.assertIn('aria-label="Model selector"', userscript)
        self.assertIn("THINKING_OPTION_SELECTOR", userscript)
        self.assertIn("think\\s+hard", userscript)
        self.assertIn("Re-open once", userscript)
        self.assertIn("compactThinkingDiagnostic", userscript)
        self.assertIn('"high-thinking-required|" + diagnostic', userscript)
        self.assertIn('getAttribute?.("data-testid")', userscript)
        self.assertIn('getAttribute?.("aria-label")', userscript)
        self.assertIn('return false;', userscript)
        self.assertIn('"violentmonkey-primary-runner"', userscript)
        self.assertIn('"portfolio-queue-result"', userscript)
        self.assertIn('"awaiting-vps-dispatch"', userscript)
        self.assertIn("BEWUSTE WORKER-HANDOFF", userscript)
        self.assertIn("ZCLOUD_QUALITY_RETRY", userscript)
        self.assertIn("same-assignment-non-stopping-execution-recovery", userscript)
        self.assertIn("data-zcloud-violentmonkey-ready", userscript)
        self.assertIn("DOE HET NU ECHT", userscript)
        self.assertNotIn("Gebruik High thinking", userscript)

    def test_high_thinking_failure_persists_safe_dom_diagnostics(self):
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        for source in (userscript, background):
            self.assertIn("lastThinkingDiagnostic", source)
            self.assertIn("compactThinkingDiagnostic", source)
            self.assertIn('"high-thinking-required|" + diagnostic', source)
            self.assertIn('getAttribute?.("data-testid")', source)
            self.assertIn('getAttribute?.("aria-label")', source)
            self.assertIn("aria-expanded", source)\n            self.assertIn("menuRoots", source)\n            self.assertIn("outerHTML", source)\n            self.assertIn("slice(0, 3500)", source)\n        self.assertIn('error: diagnostic', userscript)
        self.assertIn('error: diagnostic', background)

    def test_webextension_is_only_primary_tab_bridge(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("const VIOLENTMONKEY_PRIMARY_RUNNER = true;", background)
        self.assertIn('const VIOLENTMONKEY_REQUIRED_VERSION = "1.1.2";', background)
        self.assertIn("ChatGPT DOM execution is owned by the Violentmonkey userscript", background)
        self.assertIn("data-zcloud-worker-id", background)
        self.assertIn("data-zcloud-worker-config", background)
        self.assertIn("data-zcloud-force-initial-dispatch", background)
        self.assertIn("violentmonkey-binding-ready", background)
        self.assertIn("webextension-tab-bridge-only", background)
        self.assertIn("violentmonkey-missing-fallback", background)
        self.assertIn("legacy-extension-runner-temporarily-retained", background)
        self.assertIn("violentmonkey-primary-takeover", background)
        self.assertIn("violentmonkey-version-mismatch", background)
        self.assertIn("vmVersions.includes(VIOLENTMONKEY_REQUIRED_VERSION)", background)
        self.assertIn("legacy-fallback-config-refresh-failed", background)
        self.assertIn('type:"runner-config-update"', background)
        self.assertIn("const violentmonkeyReadyProjects = new Set();", background)
        self.assertIn("const vmOwnsCommand = VIOLENTMONKEY_PRIMARY_RUNNER", background)
        self.assertIn("violentmonkeyReadyProjects.has(key)", background)
        self.assertIn("positively announced readiness", background)
        self.assertIn("!violentmonkeyReadyProjects.has(target.project_id)", background)

        start = background.index("async function inject(tabId, target)")
        end = background.index("async function commandResult(", start)
        inject_block = background[start:end]
        primary = inject_block.index("if (VIOLENTMONKEY_PRIMARY_RUNNER)")
        bridge_return = inject_block.index('return {mode: "violentmonkey"};', primary)
        legacy_injection = inject_block.index("runProject.toString()", primary)
        self.assertLess(primary, bridge_return)
        self.assertLess(bridge_return, legacy_injection)

    def test_start_command_for_existing_tab_forces_initial_dispatch(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        start = background.index("async function startProject(projectId, commandId)")
        end = background.index("async function pauseProject(", start)
        block = background[start:end]

        self.assertIn("pendingInitialDispatches.add(projectId);", block)
        self.assertIn("await inject(current, target)", block)
        self.assertIn("initial dispatch geforceerd", block)
        self.assertNotIn('"Project draait al"', block)

    def test_activation_requires_real_dispatch_evidence(self):
        workflow = (ROOT / ".github" / "workflows" / "zcloud-one-worker-testing-activation.yml").read_text(encoding="utf-8")

        self.assertIn("DISPATCH_EVENTS", workflow)
        self.assertIn("def dispatched_worker(status, key, command_id):", workflow)
        self.assertIn('command_status != "completed"', workflow)
        self.assertIn('event == "send-blocked"', workflow)
        self.assertIn('"prompt-sent"', workflow)
        self.assertIn('"generation-started"', workflow)

    def test_userscript_is_served_and_deployed(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        promote = (ROOT / "scripts" / "zcloud_transactional_promote.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "zcloud-vps-deploy.yml").read_text(encoding="utf-8")

        self.assertIn("'/zcloud-worker.user.js':'zcloud-worker.user.js'", server)
        self.assertIn('"public/zcloud-worker.user.js"', promote)
        self.assertIn("--path public/zcloud-worker.user.js", workflow)
        self.assertIn("Promote Violentmonkey worker", workflow)


if __name__ == "__main__":
    unittest.main()
