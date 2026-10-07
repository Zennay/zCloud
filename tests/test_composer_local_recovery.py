import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERSCRIPT = ROOT / "public" / "zcloud-worker.user.js"


class ComposerLocalRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = USERSCRIPT.read_text(encoding="utf-8")

    def function_body(self, signature: str) -> str:
        start = self.text.index(signature)
        brace = self.text.index("{", start)
        depth = 0
        quote = None
        escaped = False
        for index in range(brace, len(self.text)):
            ch = self.text[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return self.text[start:index + 1]
        self.fail(f"unterminated function: {signature}")

    def test_recovery_has_45_second_grace_and_episode_state(self):
        self.assertIn("const COMPOSER_RECOVERY_TIMEOUT_MS = 45000;", self.text)
        self.assertIn("let composerMissingSince = 0;", self.text)
        self.assertIn("let composerRecoveryAttempted = false;", self.text)
        self.assertIn('let composerRecoveryProjectId = "";', self.text)

    def test_recovery_is_one_shot_and_uses_existing_fresh_chat_guard(self):
        body = self.function_body("async function recoverMissingComposer(")
        self.assertIn("if (composerRecoveryAttempted || nowMs - composerMissingSince < COMPOSER_RECOVERY_TIMEOUT_MS) return false;", body)
        self.assertIn("composerRecoveryAttempted = true;", body)
        self.assertIn('return requestFreshConversation("composer-missing-timeout");', body)
        self.assertLess(body.index("composerRecoveryAttempted = true;"), body.index('requestFreshConversation("composer-missing-timeout")'))

    def test_recovery_resets_when_composer_returns_or_project_changes(self):
        body = self.function_body("async function recoverMissingComposer(")
        self.assertIn("if (composerRecoveryProjectId !== projectId) resetComposerRecovery(projectId);", body)
        self.assertRegex(body, re.compile(r"if \(composer\(\)\) \{\s*resetComposerRecovery\(projectId\);\s*return false;", re.S))
        refresh = self.function_body("async function refreshTarget()")
        self.assertRegex(refresh, re.compile(r"target = null;\s*draining = false;\s*resetComposerRecovery\(\);", re.S))

    def test_busy_worker_does_not_accumulate_missing_composer_timeout(self):
        body = self.function_body("async function recoverMissingComposer(")
        self.assertIn("sending || draining || generationActive() || awaitingGeneration", body)
        self.assertRegex(body, re.compile(r"composerMissingSince = 0;\s*composerRecoveryAttempted = false;", re.S))

    def test_tick_runs_recovery_without_extra_timer(self):
        tick = self.function_body("async function tick()")
        self.assertIn("if (await recoverMissingComposer()) return;", tick)
        self.assertEqual(2, self.text.count("COMPOSER_RECOVERY_TIMEOUT_MS"))
        self.assertNotIn("setInterval(recoverMissingComposer", self.text)

    def test_composer_blocked_telemetry_uses_existing_dedupe(self):
        send = self.function_body("async function sendPrompt(")
        self.assertIn('await reportSendBlocked("composer-missing");', send)
        self.assertNotIn('await status("send-blocked", {reason: "composer-missing"});', send)

    def test_existing_bounded_send_button_wait_is_preserved(self):
        wait = self.function_body("async function waitForSendButton(")
        self.assertIn("timeoutMs = 3000", wait)
        self.assertIn("await sleep(100);", wait)


if __name__ == "__main__":
    unittest.main(verbosity=2)
