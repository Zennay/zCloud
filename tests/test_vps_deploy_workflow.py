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
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("cancel-in-progress: false", text)
        deploy_block = text[text.index("jobs:\n  deploy:"):text.index("    steps:")]
        self.assertIn("    concurrency:\n      group: zcloud-production-deploy", deploy_block)
        self.assertNotIn("\nconcurrency:\n  group: zcloud-production-deploy", text)
        self.assertIn("Resolve latest green main revision", text)
        self.assertIn("actions: read", text)
        self.assertIn("ref: main", text)
        self.assertIn("git ls-remote origin refs/heads/main", text)
        self.assertIn("zcloud-regression-smoke.yml/runs", text)
        self.assertIn("?branch=main&status=success&per_page=100", text)
        self.assertIn('str(run.get("event") or "") in {"push", "workflow_dispatch"}', text)
        regression = (ROOT / ".github/workflows/zcloud-regression-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", regression)
        self.assertIn("GREEN_MAIN_CONFIRMED", text)
        self.assertIn("CURRENT_MAIN_NOT_GREEN", text)
        self.assertIn("Reconfirm green main before VPS writes", text)
        self.assertIn("PREWRITE_MAIN_CONFIRMED", text)
        self.assertIn("steps.freshness.outputs.deploy == 'true'", text)
        self.assertIn("steps.prewrite.outputs.deploy == 'true'", text)
        self.assertIn("fetch-depth: 513", text)
        self.assertLess(
            text.index("- name: Promote backend runtime core"),
            text.index("- name: Promote Violentmonkey worker"),
        )
        backend = text[
            text.index("- name: Promote backend runtime core"):
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
        self.assertIn("--path lane_generator.py", backend)
        recovery = (ROOT / "scripts/zcloud_recovery.py").read_text(encoding="utf-8")
        prechange = (ROOT / "scripts/zcloud_prechange_guard.py").read_text(encoding="utf-8")
        self.assertIn('"lane_generator.py"', recovery)
        self.assertIn('"lane_generator.py"', prechange)
        self.assertEqual(
            2,
            text.count("--allow-recent-ancestor-prechange-drift"),
        )

    def test_deploy_recovers_unhealthy_zcloud_before_freezing_self_heal(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        recover = "Recover zCloud health before guarded promotion"
        suspend = "Suspend external self-heal during guarded deploy"
        promote = "Promote backend runtime core"
        quarantine = "Quarantine stale non-runtime userscript backups before guarded promotion"
        block = text[text.index(recover):text.index(quarantine)]
        self.assertIn('service="zennay-cloud.service"', block)
        self.assertIn('url="http://127.0.0.1:8765/"', block)
        self.assertEqual(2, block.count('url="http://127.0.0.1:8765/"'))
        self.assertNotIn('/api/status', block)
        self.assertIn('systemctl restart "$service"', block)
        self.assertIn("curl -fsS --max-time 4", block)
        self.assertIn("for _ in $(seq 1 20)", block)
        self.assertIn("ZCLOUD_PREDEPLOY_HEALTH=recovered", block)
        self.assertIn("ZCLOUD_PREDEPLOY_HEALTH=failed", block)
        self.assertIn('lock_file="/run/zcloud-self-heal.lock"', block)
        self.assertIn('sudo -n flock -w 240 "$lock_file" bash -s', block)
        self.assertNotIn('exec 9>"$lock_file"', block)
        self.assertLess(text.index(suspend), text.index(recover))
        self.assertLess(text.index(recover), text.index(promote))

    def test_deploy_quarantines_non_runtime_userscript_backups_before_prechange(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        quarantine = "Quarantine stale non-runtime userscript backups before guarded promotion"
        promote = "Promote backend runtime core"
        self.assertIn(quarantine, text)
        self.assertIn('zcloud-worker.user.js.bak-*', text)
        self.assertIn('zcloud-worker.user.js.pre-effort-selector.bak', text)
        self.assertIn('$HOME/.local/state/zcloud/runtime-backups', text)
        self.assertIn('mv -- "$backup" "$target"', text)
        self.assertNotIn('rm -f "$backup"', text)
        self.assertLess(text.index(quarantine), text.index(promote))

    def test_deploy_quarantines_generated_python_bytecode_before_prechange(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        quarantine = "Quarantine generated Python bytecode before guarded promotion"
        promote = "Promote backend runtime core"
        self.assertIn(quarantine, text)
        self.assertIn('/home/ubuntu/zennay-cloud/scripts/__pycache__', text)
        self.assertIn('$HOME/.local/state/zcloud/runtime-backups', text)
        self.assertIn('mv -- "$live" "$target"', text)
        self.assertNotIn('rm -rf "$live"', text)
        self.assertLess(text.index(quarantine), text.index(promote))

    def test_temporary_haxlab_live_deploy_bridge_is_removed(self):
        self.assertFalse(
            (ROOT / ".github/workflows/haxlab-priority-scoped-live.yml").exists()
        )

    def test_violentmonkey_only_mode_skips_inactive_firefox_extension_promotion(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        block = text[
            text.index("- name: Promote Violentmonkey worker"):
            text.index("- name: Promote mobile UI")
        ]
        self.assertIn("worker_paths=(--path public/zcloud-worker.user.js)", block)
        self.assertIn("10-legacy-disabled.conf", block)
        self.assertIn("Violentmonkey only", block)
        self.assertIn("ExecCondition=/bin/false", block)
        self.assertIn("WORKER_RUNTIME_MODE=violentmonkey_only", block)
        self.assertIn("worker_paths+=(--path firefox-extension/background.js)", block)
        self.assertIn("\"${worker_paths[@]}\"", block)

    def test_one_time_live_server_migration_is_exactly_reviewed(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        marker = (
            "--reconcile-known-live-sha "
            "server.py=fb3af74f2b1f77127f2fbbda0f29efab0f65308e6a98f6b357d30a8acd26dbe0"
        )
        self.assertIn(marker, text)
        self.assertEqual(2, text.count("--reconcile-known-live-sha server.py="))
        catalog = text[
            text.index("- name: Bootstrap validated project catalog"):
            text.index("- name: Promote backend runtime core")
        ]
        core = text[
            text.index("- name: Promote backend runtime core"):
            text.index("- name: Promote autonomy policies and queue seed")
        ]
        self.assertIn(marker, catalog)
        self.assertIn(marker, core)
    def test_deploy_rejects_temporary_haxlab_runner_before_writes(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        guard = "Enforce permanent zCloud VPS runner identity"
        self.assertIn(guard, text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertLess(text.index(guard), text.index("Mark production deploy pending"))
        self.assertLess(text.index(guard), text.index("Promote backend runtime core"))

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
        self.assertIn('str(run.get("event") or "") in {"push", "workflow_dispatch"}', resolve)
        self.assertIn('str(run.get("head_branch") or "") == "main"', resolve)
        self.assertIn('deadline=$((SECONDS + 180))', resolve)
        self.assertIn('poll_seconds=8', resolve)
        self.assertIn('git fetch --no-tags --depth=64 origin main', resolve)
        self.assertIn('git checkout --detach "$current"', resolve)
        self.assertIn("MAIN_MOVED_DURING_GREEN_WAIT", resolve)
        self.assertIn("COALESCED_CURRENT_MAIN", resolve)
        self.assertIn("GREEN_WAIT_CURRENT_MAIN", resolve)
        self.assertIn("CURRENT_MAIN_NOT_GREEN_TIMEOUT", resolve)
        self.assertIn('echo "deploy_sha=$resolved"', resolve)
        self.assertIn('echo "regression_run_id=$regression_id"', resolve)
        self.assertNotIn("MAIN_MOVED_BEFORE_GREEN_CHECK", resolve)

    def test_deploy_uses_transactional_promotions_without_chat_activation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(5, text.count("scripts/zcloud_transactional_promote.py"))
        for path in (
            "server.py",
            "lane_generator.py",
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

    def test_project_catalog_bootstraps_before_backend_schema_validation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        catalog = text[
            text.index("- name: Bootstrap validated project catalog"):
            text.index("- name: Promote backend runtime core")
        ]
        self.assertLess(
            text.index("- name: Bootstrap validated project catalog"),
            text.index("- name: Promote backend runtime core"),
        )
        self.assertIn("--path projects.json", catalog)
        self.assertIn("--preserve-prechange-drift public/zcloud-worker.user.js", catalog)
        self.assertIn("--preserve-prechange-drift firefox-extension/background.js", catalog)
        self.assertIn("--allow-recent-ancestor-prechange-drift", catalog)
        self.assertIn(
            "--reconcile-known-live-sha server.py=fb3af74f2b1f77127f2fbbda0f29efab0f65308e6a98f6b357d30a8acd26dbe0",
            catalog,
        )
        self.assertEqual(1, catalog.count("scripts/zcloud_transactional_promote.py"))
        self.assertNotIn("- name: Promote translated project catalog", text)

    def test_backend_backlog_is_promoted_in_bounded_transactions(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        core = text[
            text.index("- name: Promote backend runtime core"):
            text.index("- name: Promote autonomy policies and queue seed")
        ]
        policy = text[
            text.index("- name: Promote autonomy policies and queue seed"):
            text.index("- name: Promote Violentmonkey worker")
        ]

        self.assertEqual(1, core.count("scripts/zcloud_transactional_promote.py"))
        self.assertEqual(1, policy.count("scripts/zcloud_transactional_promote.py"))
        for path in ("server.py", "lane_generator.py", "scripts/zcloud_recovery.py"):
            self.assertIn(f"--path {path}", core)
            self.assertNotIn(f"--path {path}", policy)
        for path in ("autonomy-policy.json", "vps-execution-policy.json", "portfolio_queue.seed.json"):
            self.assertIn(f"--path {path}", policy)
            self.assertNotIn(f"--path {path}", core)
        self.assertNotIn("high_blast_radius_promotion", core + policy)

    def test_publish_status_requires_actual_prewrite_confirmation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        publish = text[text.index("- name: Publish production deploy result"):]
        self.assertIn(
            "if: always() && steps.freshness.outputs.deploy == 'true' && steps.prewrite.outputs.deploy == 'true'",
            publish,
        )
        self.assertIn("MAIN_MOVED_BEFORE_VPS_WRITE", text)

    def test_execution_probe_claims_cloud_task_on_self_hosted_runner(self):
        text = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
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
