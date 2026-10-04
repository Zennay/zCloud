import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ViolentmonkeyPrimaryRunnerTests(unittest.TestCase):
    def test_userscript_is_database_backed_and_soft_falls_back_on_high(self):
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")

        self.assertIn("@name         zCloud Dynamic Worker", userscript)
        self.assertIn("@match        http://*/*", userscript)
        self.assertIn("@match        https://*/*", userscript)
        self.assertIn("@grant        GM_getValue", userscript)
        self.assertIn("@grant        GM_setValue", userscript)
        self.assertIn("@grant        GM_deleteValue", userscript)
        self.assertIn('return "other";', userscript)
        self.assertIn('return "claude";', userscript)
        self.assertIn('data-testid="chat-input"', userscript)
        self.assertIn('data-testid="chat-input-send"', userscript)
        self.assertIn("generationActive", userscript)
        self.assertIn("claimCandidate", userscript)
        self.assertIn("TAB_CLAIM_LEASE_MS", userscript)
        self.assertIn("zcloud_worker", userscript)
        self.assertIn("zcloud_tab", userscript)
        self.assertIn("applyRuntimeSettings", userscript)
        self.assertIn("@grant        GM_xmlhttpRequest", userscript)
        self.assertIn("@connect      127.0.0.1", userscript)
        self.assertIn('const API = "http://127.0.0.1:8765/api"', userscript)
        self.assertIn('gmRequest("/runner-targets")', userscript)
        self.assertIn('gmRequest("/runner-commands")', userscript)
        self.assertIn('gmRequest("/runner-status"', userscript)
        self.assertIn('gmRequest("/runner-command-result"', userscript)
        self.assertIn('reportSendBlocked("push-deferred-busy")', userscript)
        self.assertIn("generationActive() || sending || awaitingGeneration", userscript)
        self.assertIn('REQUIRED_THINKING_EFFORT = "high"', userscript)
        self.assertIn("ensureHighThinking", userscript)
        self.assertIn('[role="slider"]', userscript)
        self.assertIn('data-testid="model-switcher-dropdown-button"', userscript)
        self.assertIn('aria-label="Model selector"', userscript)
        self.assertIn("THINKING_OPTION_SELECTOR", userscript)
        self.assertIn("thinkingEffortPicker", userscript)
        self.assertIn("High selector", userscript)
        self.assertIn("effortPickerShowsHigh", userscript)
        self.assertIn("think\\s+hard", userscript)
        self.assertIn("Re-open once", userscript)
        self.assertIn("compactThinkingDiagnostic", userscript)
        self.assertIn('"thinking-effort-unavailable"', userscript)
        self.assertIn('"high-thinking-picker-unavailable"', userscript)
        self.assertIn("Continue anyway - don't block", userscript)
        self.assertIn('getAttribute?.("data-testid")', userscript)
        self.assertIn('getAttribute?.("aria-label")', userscript)
        self.assertIn('return false;', userscript)
        self.assertIn('"violentmonkey-primary-runner"', userscript)
        self.assertIn('"portfolio-queue-result"', userscript)
        self.assertIn('"awaiting-vps-dispatch"', userscript)
        self.assertNotIn("Ga verder met hetzelfde vrije werkgebied", userscript)
        self.assertIn("data-zcloud-violentmonkey-ready", userscript)
        self.assertNotIn("Werk verder aan het project en voer nu een concrete volgende stap uit", userscript)
        self.assertNotIn("Gebruik High thinking", userscript)

    def test_high_thinking_failure_persists_safe_dom_diagnostics(self):
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        for source in (userscript, background):
            self.assertIn("lastThinkingDiagnostic", source)
            self.assertIn("compactThinkingDiagnostic", source)
            self.assertIn('getAttribute?.("data-testid")', source)
            self.assertIn('getAttribute?.("aria-label")', source)
            self.assertIn("aria-expanded", source)
            self.assertIn("const menus =", source)
            self.assertIn("x: clean(el.innerText || el.textContent, 420)", source)
            self.assertIn("slice(0, 3500)", source)
            self.assertIn('button[aria-label^="Switch mode"]', source)
            self.assertIn('[aria-label*="current mode"]', source)
            self.assertIn("p: snap(picker)", source)
            self.assertIn("m: menus", source)
            self.assertIn("c: scored", source)
        self.assertIn('"thinking-effort-unavailable"', userscript)
        self.assertIn('diagnostic: diagnostic', userscript)
        self.assertIn('"thinking-effort-unavailable"', background)
        self.assertIn('diagnostic: diagnostic', background)
        self.assertNotIn('"high-thinking-required|" + diagnostic', background)

    def test_bridge_required_version_matches_userscript_metadata_and_runtime(self):
        import re

        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")

        required = re.search(r'VIOLENTMONKEY_REQUIRED_VERSION = "([^"]+)"', background)
        metadata = re.search(r"^// @version\s+([^\s]+)", userscript, re.MULTILINE)
        runtime = re.search(r'SCRIPT_VERSION = "([^"]+)"', userscript)

        self.assertIsNotNone(required)
        self.assertIsNotNone(metadata)
        self.assertIsNotNone(runtime)
        self.assertEqual(required.group(1), metadata.group(1))
        self.assertEqual(required.group(1), runtime.group(1))
        self.assertEqual("1.3.7", required.group(1))

    def test_webextension_is_only_primary_tab_bridge(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("const VIOLENTMONKEY_PRIMARY_RUNNER = true;", background)
        self.assertIn('const VIOLENTMONKEY_REQUIRED_VERSION = "1.3.7";', background)
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
        self.assertIn("shouldProbeViolentmonkey(target.project_id)", background)

        start = background.index("async function inject(tabId, target)")
        end = background.index("async function commandResult(", start)
        inject_block = background[start:end]
        primary = inject_block.index("if (VIOLENTMONKEY_PRIMARY_RUNNER)")
        bridge_return = inject_block.index('return {mode: "violentmonkey"};', primary)
        legacy_injection = inject_block.index("runProject.toString()", primary)
        self.assertLess(primary, bridge_return)
        self.assertLess(bridge_return, legacy_injection)

    def test_fallback_refresh_is_stable_and_stale_commands_are_superseded(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        config_start = background.index("function runnerConfigChanged")
        config_end = background.index("async function setRecoveryTag", config_start)
        config_block = background[config_start:config_end]
        self.assertNotIn("claim_expires", config_block)
        self.assertIn("VIOLENTMONKEY_FALLBACK_REPROBE_MS = 120000", background)
        self.assertIn("function shouldProbeViolentmonkey(projectId)", background)
        self.assertIn("shouldProbeViolentmonkey(target.project_id)", background)

        sync_start = background.index("async function syncRunnerConfig")
        sync_end = background.index("async function refreshTargets", sync_start)
        sync_block = background[sync_start:sync_end]
        self.assertIn("for (let attempt = 0; attempt < 4; attempt += 1)", sync_block)
        self.assertIn("250 * (attempt + 1)", sync_block)
        self.assertIn("legacy-fallback-config-refresh-failed", sync_block)

        self.assertIn("async function supersedeMissingTarget", background)
        self.assertIn("Superseded: worker no longer allocated", background)
        poll_start = background.index("async function pollCommands")
        poll_block = background[poll_start:]
        self.assertIn("await supersedeMissingTarget(command.project_id, command.id", poll_block)

    def test_extension_fallback_canary_accepts_busy_idempotent_push(self):
        workflow = (ROOT / ".github" / "workflows" / "zcloud-extension-fallback-canary.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('busy_noop=result=="Runner is al bezig; extra prompt was niet nodig"', workflow)
        self.assertIn('"runner-config-updated","injection-success","heartbeat"', workflow)
        self.assertIn('print("CANARY_PUSH_BUSY_GREEN=1")', workflow)
        self.assertLess(
            workflow.index('busy_noop=result=="Runner is al bezig; extra prompt was niet nodig"'),
            workflow.index('raise SystemExit("CANARY_PUSH_TIMEOUT'),
        )

    def test_drain_handoff_keeps_command_pending_when_violentmonkey_takes_over(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")
        start = background.index("async function drainProject(projectId, commandId)")
        end = background.index("async function pushProject(", start)
        block = background[start:end]

        self.assertIn("const injected = await inject(tabId, target);", block)
        self.assertIn('if (injected?.mode === "violentmonkey") return;', block)
        self.assertIn("const rebound = await inject(tabId, target);", block)
        self.assertIn('if (rebound?.mode === "violentmonkey") return;', block)
        self.assertLess(
            block.index('if (injected?.mode === "violentmonkey") return;'),
            block.index('"Drain kon niet veilig worden bevestigd"'),
        )
        self.assertLess(
            block.index('if (rebound?.mode === "violentmonkey") return;'),
            block.index('"Drain kon niet veilig worden bevestigd"'),
        )
        self.assertIn('else if (VIOLENTMONKEY_PRIMARY_RUNNER) {', block)
        self.assertIn('event: "runner-drain-deferred"', block)
        self.assertIn('reason: "violentmonkey-primary-awaiting-database-ack"', block)
        self.assertLess(
            block.index('event: "runner-drain-deferred"'),
            block.index('"Drain kon niet veilig worden bevestigd"'),
        )

    def test_userscript_retries_drain_when_command_ack_is_transiently_unavailable(self):
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")
        result_start = userscript.index("async function commandResult(commandId, resultStatus, result)")
        result_end = userscript.index("function handoffKey()", result_start)
        result_block = userscript[result_start:result_end]
        self.assertIn("return true;", result_block)
        self.assertIn("return false;", result_block)

        command_start = userscript.index("async function handleCommands()")
        command_end = userscript.index("async function tick()", command_start)
        command_block = userscript[command_start:command_end]
        drain_start = command_block.index('command.action === "drain"')
        drain_block = command_block[drain_start:]
        self.assertIn("const acknowledged = await commandResult(", drain_block)
        self.assertIn("if (acknowledged) lastHandledCommandId = Math.max(lastHandledCommandId, id);", drain_block)
        self.assertNotIn(
            'await commandResult(id, "completed", "Violentmonkey worker will stop after current generation");\n        lastHandledCommandId',
            drain_block,
        )

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
        self.assertIn('path.startswith("/api/runner-live")', workflow)
        self.assertIn("attempts = 10", workflow)
        self.assertIn('method == "GET" and exc.code == 503', workflow)
        self.assertIn('call("GET", "/api/runner-live")', workflow)
        self.assertNotIn('call("GET", "/api/status?project=zcloud")', workflow)

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
