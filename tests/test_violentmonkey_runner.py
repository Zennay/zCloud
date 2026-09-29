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
        self.assertIn('reason: "high-thinking-required"', userscript)
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

    def test_webextension_is_only_primary_tab_bridge(self):
        background = (ROOT / "firefox-extension" / "background.js").read_text(encoding="utf-8")

        self.assertIn("const VIOLENTMONKEY_PRIMARY_RUNNER = true;", background)
        self.assertIn("ChatGPT DOM execution is owned by the Violentmonkey userscript", background)
        self.assertIn("data-zcloud-worker-id", background)
        self.assertIn("data-zcloud-worker-config", background)
        self.assertIn("data-zcloud-force-initial-dispatch", background)
        self.assertIn("violentmonkey-binding-ready", background)
        self.assertIn("webextension-tab-bridge-only", background)
        self.assertIn("violentmonkey-missing-fallback", background)
        self.assertIn("legacy-extension-runner-temporarily-retained", background)
        self.assertIn("violentmonkey-primary-takeover", background)
        self.assertIn(
            'VIOLENTMONKEY_PRIMARY_RUNNER && (command.action === "push" || command.action === "drain")',
            background,
        )
        self.assertIn("Leave database push/drain commands pending for the bound Violentmonkey worker", background)

        start = background.index("async function inject(tabId, target)")
        end = background.index("async function commandResult(", start)
        inject_block = background[start:end]
        primary = inject_block.index("if (VIOLENTMONKEY_PRIMARY_RUNNER)")
        bridge_return = inject_block.index("return;", primary)
        legacy_injection = inject_block.index("runProject.toString()", primary)
        self.assertLess(primary, bridge_return)
        self.assertLess(bridge_return, legacy_injection)

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
