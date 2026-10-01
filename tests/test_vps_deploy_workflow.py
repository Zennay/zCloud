from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VpsDeployWorkflowTests(unittest.TestCase):
    def test_deploy_only_follows_green_main_regression(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn('workflows: ["zCloud regression smoke"]', text)
        self.assertIn("branches: [main]", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("github.run_attempt == 1", text)
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("cancel-in-progress: false", text)
        deploy_block = text[text.index("jobs:\n  deploy:"):text.index("    steps:")]
        self.assertIn("    concurrency:\n      group: zcloud-production-deploy", deploy_block)
        self.assertNotIn("\nconcurrency:\n  group: zcloud-production-deploy", text)
        self.assertIn("Resolve latest green main revision", text)
        self.assertIn("actions: read", text)
        self.assertIn("ref: main", text)
        self.assertIn("git ls-remote origin refs/heads/main", text)
        self.assertIn("zcloud-regression-smoke.yml/runs", text)
        self.assertIn("?branch=main&event=push&status=success&per_page=100", text)
        self.assertIn("GREEN_MAIN_CONFIRMED", text)
        self.assertIn("CURRENT_MAIN_NOT_GREEN", text)
        self.assertIn("Reconfirm green main before VPS writes", text)
        self.assertIn("PREWRITE_MAIN_CONFIRMED", text)
        self.assertIn("steps.freshness.outputs.deploy == 'true'", text)
        self.assertIn("steps.prewrite.outputs.deploy == 'true'", text)
        self.assertIn("fetch-depth: 64", text)
        self.assertLess(
            text.index("- name: Promote backend and autonomy policy"),
            text.index("- name: Promote Violentmonkey worker"),
        )
        backend = text[
            text.index("- name: Promote backend and autonomy policy"):
            text.index("- name: Promote Violentmonkey worker")
        ]
        self.assertIn(
            "--preserve-prechange-drift public/zcloud-worker.user.js",
            backend,
        )
        self.assertIn(
            "--preserve-prechange-drift firefox-extension/background.js",
            backend,
        )
        self.assertIn("--allow-recent-ancestor-prechange-drift", backend)
        self.assertEqual(
            1,
            text.count("--allow-recent-ancestor-prechange-drift"),
        )

    def test_deploy_quarantines_non_runtime_userscript_backups_before_prechange(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        quarantine = "Quarantine stale non-runtime userscript backups before guarded promotion"
        promote = "Promote backend and autonomy policy"
        self.assertIn(quarantine, text)
        self.assertIn('zcloud-worker.user.js.bak-*', text)
        self.assertIn('zcloud-worker.user.js.pre-effort-selector.bak', text)
        self.assertIn('$HOME/.local/state/zcloud/runtime-backups', text)
        self.assertIn('mv -- "$backup" "$target"', text)
        self.assertNotIn('rm -f "$backup"', text)
        self.assertLess(text.index(quarantine), text.index(promote))

    def test_deploy_rejects_temporary_haxlab_runner_before_writes(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        guard = "Enforce permanent zCloud VPS runner identity"
        self.assertIn(guard, text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertLess(text.index(guard), text.index("Mark production deploy pending"))
        self.assertLess(text.index(guard), text.index("Promote backend and autonomy policy"))

        probe = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn(guard, probe)
        self.assertLess(probe.index(guard), probe.index("Acquire cloud deploylane coordination claim"))
        self.assertIn("zcloud-vps-runner-guard.json", probe)

    def test_deploy_coalesces_only_to_current_green_main(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        resolve = text[
            text.index("- name: Resolve latest green main revision"):
            text.index("- name: Reconfirm green main before VPS writes")
        ]
        self.assertIn('str(run.get("head_sha") or "") == sha', resolve)
        self.assertIn('str(run.get("conclusion") or "") == "success"', resolve)
        self.assertIn('str(run.get("event") or "") == "push"', resolve)
        self.assertIn('str(run.get("head_branch") or "") == "main"', resolve)
        self.assertIn('handle.write(f"deploy_sha={sha}\\n")', resolve)
        self.assertIn('handle.write(f"regression_run_id={int(matched[\'id\'])}\\n")', resolve)
        self.assertIn("MAIN_MOVED_BEFORE_GREEN_CHECK", resolve)

    def test_deploy_uses_transactional_promotions_without_chat_activation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(4, text.count("scripts/zcloud_transactional_promote.py"))
        for path in (
            "server.py",
            "scripts/zcloud_recovery.py",
            "autonomy-policy.json",
            "portfolio_queue.seed.json",
            "firefox-extension/background.js",
            "public/app.js",
            "public/index.html",
            "public/style.css",
            "public/enhancements.js",
            "public/enhancements.css",
            "public/zcloud-worker.user.js",
            "projects.json",
        ):
            self.assertIn(f"--path {path}", text)
        self.assertIn("deploy/chatgpt-firefox.service", text)
        self.assertIn("Ensure Firefox automation service for guarded canaries", text)
        self.assertIn("systemctl --user start chatgpt-firefox.service", text)
        self.assertIn("chatgpt-display.service chatgpt-openbox.service", text)
        self.assertIn('systemctl --user reset-failed "$dependency"', text)
        self.assertIn("FIREFOX_AUTOMATION_SERVICE=intentionally_disabled_violentmonkey", text)
        self.assertIn("ExecCondition=/bin/false", text)
        self.assertIn("FIREFOX_AUTOMATION_SERVICE=recovered", text)
        self.assertIn("FIREFOX_AUTOMATION_SERVICE=failed", text)
        self.assertIn("Suspend external self-heal during guarded deploy", text)
        self.assertIn('touch "$sentinel"', text)
        self.assertIn("steps.self_heal_guard.outputs.created == 'true'", text)
        self.assertIn("rm -f /home/ubuntu/zennay-cloud/.disable-self-heal", text)
        self.assertIn("Install aligned self-heal probe", text)
        self.assertIn('sudo -n install -m 0755 "$GITHUB_WORKSPACE/scripts/zcloud-self-heal.sh" /usr/local/sbin/zcloud-self-heal', text)
        self.assertIn("systemctl --user set-property --runtime chatgpt-firefox.service CPUWeight=100", text)
        for forbidden in ("runner-control", "action: start", "chatgpt.com"):
            self.assertNotIn(forbidden, text)

    # Commit status is the durable, connector-readable production evidence surface.\n    def test_deploy_publishes_sanitized_green_evidence_for_exact_revision(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("statuses: write", text)
        self.assertIn("Mark production deploy pending", text)
        self.assertIn("Capture green production deploy evidence", text)
        self.assertIn("scripts/zcloud_healthcheck.py --json", text)
        self.assertIn("last-known-good.json", text)
        self.assertIn("POSTDEPLOY_GREEN", text)
        self.assertIn("zcloud-production-deploy-evidence-", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn('"context": "zcloud/vps-production"', text)
        self.assertIn("Publish production deploy result", text)
        self.assertIn("steps.freshness.outputs.deploy_sha", text)
        self.assertNotIn("DEPLOY_SHA: ${{ github.event.workflow_run.head_sha }}", text)

    def test_execution_probe_claims_cloud_task_on_self_hosted_runner(self):
        text = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py acquire", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py verify", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py release", text)
        self.assertIn("if: always()", text)
        self.assertIn("actions/upload-artifact@v4", text)

        coordination = (ROOT / "scripts/zcloud_vps_deploylane_coordination.py").read_text(encoding="utf-8")
        self.assertIn('PROJECT = "cloud"', coordination)
        self.assertIn('WORKER = "cloud::w1"', coordination)
        self.assertIn('CLAIM = "cloud-permanent-vps-first-deploylane"', coordination)
        self.assertIn('"action": "heartbeat"', coordination)
        self.assertIn('"action": "release"', coordination)
        self.assertIn('"vps_profile": "control_plane"', coordination)
        self.assertIn("claim verification failed", coordination)


if __name__ == "__main__":
    unittest.main()
