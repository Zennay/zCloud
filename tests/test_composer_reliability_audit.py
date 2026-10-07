import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_composer_reliability_audit as audit


PRIMARY_GOOD = r'''
function composer() {
  return document.querySelector("#prompt-textarea") ||
    document.querySelector('[contenteditable="true"][role="textbox"]') ||
    document.querySelector("textarea");
}
async function waitForSendButton(timeoutMs = 3000) {
  while (Date.now() <= deadline) {
    await sleep(100);
  }
}
if (lastSendBlockedReport.reason === reason && nowMs - lastSendBlockedReport.at < 60000) return;
let composerMissingSince = 0;
const COMPOSER_RECOVERY_MS = 45 * 1000;
browser.runtime.sendMessage({type: "runner-new-chat"});
'''

FALLBACK_GOOD = r'''
const CHECK_MS = 5000;
const STARTUP_IDLE_MS = 8000;
const COMPOSER_RECOVERY_MS = 45 * 1000;
function composer() {
  return document.querySelector("#prompt-textarea") ||
    document.querySelector('[contenteditable="true"][role="textbox"]') ||
    document.querySelector("textarea");
}
if (now - composerMissingSince >= COMPOSER_RECOVERY_MS && !recoveryRequested) {
  recoveryRequested = true;
  browser.runtime.sendMessage({type: "runner-new-chat", reason: "composer-missing"});
}
if (now - lastStartupAttemptAt < 5000) return;
if (!lastPromptSentAt || now - lastPromptSentAt >= 300000) {}
'''


class ComposerReliabilityAuditTests(unittest.TestCase):
    def test_complete_contract_can_be_green(self):
        result = audit.audit(PRIMARY_GOOD, FALLBACK_GOOD)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual([], result["gaps"])
        self.assertTrue(result["primary"]["send_wait_bounded"])
        self.assertTrue(result["fallback"]["bounded_recovery"])

    def test_missing_primary_local_recovery_is_explicit_gap(self):
        primary = PRIMARY_GOOD.replace("let composerMissingSince = 0;", "")
        result = audit.audit(primary, FALLBACK_GOOD)
        self.assertFalse(result["coverage_complete"])
        self.assertIn("primary_local_composer_recovery_missing", result["gaps"])

    def test_unbounded_send_wait_is_rejected(self):
        primary = PRIMARY_GOOD.replace(
            "waitForSendButton(timeoutMs = 3000)",
            "waitForSendButton(timeoutMs = 30000)",
        )
        result = audit.audit(primary, FALLBACK_GOOD)
        self.assertIn("primary_send_wait_unbounded", result["gaps"])

    def test_fast_fallback_detection_is_bounded(self):
        slow = FALLBACK_GOOD.replace("const CHECK_MS = 5000;", "const CHECK_MS = 15000;")
        result = audit.audit(PRIMARY_GOOD, slow)
        self.assertIn("fallback_detection_not_fast", result["gaps"])

    def test_recovery_requires_one_shot_guard(self):
        unsafe = FALLBACK_GOOD.replace(" && !recoveryRequested", "")
        result = audit.audit(PRIMARY_GOOD, unsafe)
        self.assertIn("fallback_recovery_not_bounded", result["gaps"])

    def test_source_reader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "runtime.js"
            target.write_text(PRIMARY_GOOD, encoding="utf-8")
            link = root / "linked.js"
            link.symlink_to(target)
            with self.assertRaises(audit.ComposerAuditError):
                audit.read_source(link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
