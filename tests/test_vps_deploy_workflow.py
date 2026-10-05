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
        self.assertIn("\nconcurrency:\n  group: zcloud-production-deploy", text)
        preflight = text[
            text.index("jobs:\n  preflight:"):
            text.index("\n  deploy:", text.index("jobs:\n  preflight:"))
        ]
        self.assertIn("runs-on: ubuntu-latest", preflight)
        self.assertIn("Skip current main when production is already green", preflight)
        self.assertIn("/commits/main/status", preflight)
        self.assertIn('"context") or "") == "zcloud/vps-production"', preflight)
        self.assertIn('latest.get("state") or "") == "success"', preflight)
        deploy_block = text[text.index("\n  deploy:"):text.index("    steps:", text.index("\n  deploy:"))]
        self.assertIn("needs: preflight", deploy_block)
        self.assertIn("needs.preflight.outputs.deploy == 'true'", deploy_block)
        self.assertNotIn("    concurrency:\n      group: zcloud-production-deploy", deploy_block)
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
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertIn("ZCLOUD_RUN_STATE_DIR", text)
        self.assertIn("Prepare stable deploy state", text)
        self.assertNotIn("$RUNNER_TEMP/", text)
        self.assertIn("Cleanup stable deploy state", text)
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
        self.assertIn("--path project_runtime.py", backend)
        self.assertIn("--path project-contracts.json", backend)
        self.assertIn("--path lane_generator.py", backend)
        recovery = (ROOT / "scripts/zcloud_recovery.py").read_text(encoding="utf-8")
        prechange = (ROOT / "scripts/zcloud_prechange_guard.py").read_text(encoding="utf-8")
        self.assertIn('"project_runtime.py"', recovery)
        self.assertIn('"project_runtime.py"', prechange)
        self.assertIn('"lane_generator.py"', recovery)
        self.assertIn('"lane_generator.py"', prechange)
        self.assertIn('"project-contracts.json"', recovery)
        self.assertIn('"project-contracts.json"', prechange)
        self.assertEqual(
            1,
            text.count("--allow-recent-ancestor-prechange-drift"),
        )

    def test_deploy_bootstraps_backend_dependencies_only_for_exact_candidate_server(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        bootstrap = "Bootstrap aligned backend dependencies before health gate"
        recover = "Recover zCloud health before guarded promotion"
        rollback = "Roll back failed pre-health dependency bootstrap"
        promote = "Promote backend runtime core"
        self.assertIn(bootstrap, text)
        self.assertIn('"project_runtime.py:runtime"', text)
        self.assertIn('"project-contracts.json:contract"', text)
        self.assertIn('"lane_generator.py:lane"', text)
        self.assertIn('"vps-execution-policy.json:policy"', text)
        self.assertIn('if [[ "$live_server_sha" != "$candidate_server_sha" ]]', text)
        self.assertIn("BACKEND_DEPENDENCY_BOOTSTRAP=skipped_server_not_candidate", text)
        self.assertIn('candidate="$GITHUB_WORKSPACE/$rel"', text)
        self.assertIn('install -m 0644 "$candidate" "$stage"', text)
        self.assertIn("BACKEND_DEPENDENCY_${label^^}=aligned", text)
        self.assertIn(rollback, text)
        self.assertIn("HAD_LIVE_RUNTIME", text)
        self.assertIn("HAD_LIVE_CONTRACT", text)
        self.assertIn("HAD_LIVE_LANE", text)
        self.assertIn("HAD_LIVE_POLICY", text)
        self.assertIn("BACKEND_DEPENDENCY_${label^^}_ROLLBACK=restored", text)
        self.assertIn("BACKEND_DEPENDENCY_${label^^}_ROLLBACK=removed", text)
        self.assertLess(text.index(bootstrap), text.index(recover))
        self.assertLess(text.index(recover), text.index(promote))

    def test_deploy_reconciles_stale_browser_commands_before_health_and_promotion(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        reconcile = "Reconcile stale browser commands before health gate"
        recover = "Recover zCloud health before guarded promotion"
        promote = "Promote backend runtime core"
        self.assertIn(reconcile, text)
        self.assertIn("scripts/zcloud_worker_watchdog.py", text)
        self.assertIn("--clear-stale-pending-only", text)
        self.assertIn("zcloud-stale-command-reconcile.json", text)
        self.assertLess(text.index(reconcile), text.index(recover))
        self.assertLess(text.index(reconcile), text.index(promote))

        final_reconcile = "Reconcile stale browser commands before final health evidence"
        restore = "Restore browser workers after guarded promotion"
        evidence = "Capture green production deploy evidence"
        self.assertIn(final_reconcile, text)
        self.assertIn("zcloud-stale-command-final-reconcile.json", text)
        self.assertEqual(3, text.count("--clear-stale-pending-only"))
        self.assertLess(text.index(restore), text.index(final_reconcile))
        self.assertLess(text.index(final_reconcile), text.index(evidence))

        evidence_block = text[text.index(evidence):text.index("Upload production deploy evidence")]
        self.assertIn('zcloud-stale-command-health-attempt-${attempt}.json', evidence_block)
        self.assertIn("--clear-stale-pending-only", evidence_block)
        self.assertLess(
            evidence_block.index("--clear-stale-pending-only"),
            evidence_block.index("scripts/zcloud_healthcheck.py --json"),
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

    def test_deploy_holds_browser_safe_idle_across_all_promotions(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        enter = text.index("Enter browser safe-idle for guarded promotion")
        first_promotion = text.index("Promote backend runtime core")
        last_guarded_step = text.index("Install aligned self-heal probe")
        restore = text.index("Restore browser workers after guarded promotion")
        evidence = text.index("Capture green production deploy evidence")
        self.assertLess(enter, first_promotion)
        self.assertLess(first_promotion, last_guarded_step)
        self.assertLess(last_guarded_step, restore)
        self.assertLess(restore, evidence)
        self.assertIn("scripts/zcloud_deploy_safe_idle.py enter", text)
        self.assertIn("--timeout-seconds 480", text)
        self.assertIn("steps.safe_idle.outcome == 'success'", text)
        self.assertIn("scripts/zcloud_deploy_safe_idle.py restore", text)
        self.assertIn("zcloud-deploy-safe-idle.json", text)

    def test_deploy_safe_idle_budget_covers_long_worker_generations(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 25", text)
        self.assertIn("--timeout-seconds 480", text)
        userscript = (ROOT / "public" / "zcloud-worker.user.js").read_text(encoding="utf-8")
        self.assertIn('command.action === "drain"', userscript)
        self.assertIn('status("runner-drained"', userscript)

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

    def test_known_live_server_reconcile_is_one_shot_and_lkg_bound(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        authorize = text[
            text.index("- name: Authorize one-shot known live server reconciliation"):
            text.index("- name: Promote backend runtime core")
        ]
        core = text[
            text.index("- name: Promote backend runtime core"):
            text.index("- name: Promote autonomy policies and queue seed")
        ]
        known_sha = "160c82219aca441644edb6acebbbfa92914e7f5e71736dbc75481576a815c80f"
        known_lkg = "20261002T105256Z-a57439eb"
        self.assertIn(f'expected_live_sha="{known_sha}"', authorize)
        self.assertIn(f'expected_lkg="{known_lkg}"', authorize)
        self.assertIn('last-known-good.json', authorize)
        self.assertIn('sha256sum "$root/server.py"', authorize)
        self.assertIn(
            'if [[ "$live_sha" == "$expected_live_sha" && "$current_lkg" == "$expected_lkg" ]]',
            authorize,
        )
        self.assertIn('echo "enabled=true" >> "$GITHUB_OUTPUT"', authorize)
        self.assertIn("KNOWN_LIVE_SERVER_RECONCILE=authorized", authorize)
        self.assertIn('if [[ "${{ steps.server_drift_reconcile.outputs.enabled }}" == "true" ]]', core)
        self.assertIn("--reconcile-known-live-sha", core)
        self.assertIn(f'"server.py={known_sha}"', core)
        self.assertIn('"${reconcile_args[@]}"', core)
        self.assertNotIn("- name: Bootstrap validated project catalog", text)


    def test_direct_live_server_prompt_writers_are_removed(self):
        for rel in (
            ".github/workflows/zcloud-live-prompt-surgical-patch.yml",
            ".github/workflows/repair-live-exact-worker-prompt.yml",
            ".github/workflows/zcloud-live-prompt-contract-repair.yml",
        ):
            self.assertFalse(
                (ROOT / rel).exists(),
                f"direct live server writer must stay removed: {rel}",
            )


    def test_deploy_rejects_temporary_haxlab_runner_before_writes(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        guard = "Enforce permanent zCloud VPS runner identity"
        self.assertIn(guard, text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertLess(text.index(guard), text.index("Mark production deploy pending"))
        self.assertLess(text.index(guard), text.index("Promote backend runtime core"))

        probe = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", probe)
        self.assertNotIn("actions/checkout@v4", probe)
        self.assertIn(guard, probe)
        self.assertLess(probe.index(guard), probe.index("Acquire cloud deploylane coordination claim"))
        self.assertIn("zcloud-vps-runner-guard.json", probe)

    def test_deploy_coalesces_to_current_main_with_runtime_equivalent_green_evidence(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        resolve = text[
            text.index("- name: Resolve latest green main revision"):
            text.index("- name: Reconfirm green main before VPS writes")
        ]
        self.assertIn('str(run.get("head_sha") or "")', resolve)
        self.assertIn('str(run.get("conclusion") or "") == "success"', resolve)
        self.assertIn('str(run.get("event") or "") in {"push", "workflow_dispatch"}', resolve)
        self.assertIn('str(run.get("head_branch") or "") == "main"', resolve)
        self.assertIn('deadline=$((SECONDS + 180))', resolve)
        self.assertIn('poll_seconds=8', resolve)
        self.assertIn('git fetch --no-tags --depth=513 origin main', resolve)
        self.assertIn('git checkout --detach "$current"', resolve)
        self.assertIn("MAIN_MOVED_DURING_GREEN_WAIT", resolve)
        self.assertIn("COALESCED_CURRENT_MAIN", resolve)
        self.assertIn("GREEN_WAIT_CURRENT_MAIN", resolve)
        self.assertIn("CURRENT_MAIN_NOT_GREEN_TIMEOUT", resolve)
        self.assertIn("GREEN_RUNTIME_EQUIVALENT", resolve)
        self.assertIn("runtime-equivalent", resolve)
        self.assertIn('"git", "merge-base", "--is-ancestor"', resolve)
        self.assertIn('"git", "diff", "--quiet"', resolve)
        for protected in (
            ".github/workflows/zcloud-vps-deploy.yml",
            ".github/workflows/zcloud-regression-smoke.yml",
            "tests",
            "server.py",
            "enhancements.py",
            "project_runtime.py",
            "lane_generator.py",
            "project-contracts.json",
            "autonomy-policy.json",
            "vps-execution-policy.json",
            "portfolio_queue.seed.json",
            "firefox-extension",
            "public",
            "deploy",
            "scripts",
        ):
            self.assertIn(f'"{protected}"', resolve)
        self.assertIn('echo "deploy_sha=$resolved"', resolve)
        self.assertIn('echo "regression_sha=$regression_sha"', resolve)
        self.assertIn('echo "regression_mode=$regression_mode"', resolve)
        self.assertNotIn("MAIN_MOVED_BEFORE_GREEN_CHECK", resolve)

    def test_deploy_uses_transactional_promotions_without_chat_activation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertEqual(4, text.count("scripts/zcloud_transactional_promote.py"))
        for path in (
            "server.py",
            "project_runtime.py",
            "lane_generator.py",
            "scripts/zcloud_recovery.py",
            "project-contracts.json",
            "autonomy-policy.json",
            "portfolio_queue.seed.json",
            "firefox-extension/background.js",
            "public/app.js",
            "public/index.html",
            "public/style.css",
            "public/enhancements.js",
            "public/enhancements.css",
            "public/zcloud-worker.user.js",
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
        self.assertNotIn("--path projects.json", text)
        self.assertNotIn("--path project-layout.json", text)
        self.assertEqual(4, text.count("--preserve-schema-validated-runtime-config"))
        for forbidden in ("runner-control", "action: start", "chatgpt.com"):
            self.assertNotIn(forbidden, text)

    # Commit status is the durable, connector-readable production evidence surface.\n    def test_deploy_publishes_sanitized_green_evidence_for_exact_revision(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("statuses: write", text)
        self.assertIn("Mark production deploy pending", text)
        self.assertIn("Capture green production deploy evidence", text)
        self.assertIn("scripts/zcloud_healthcheck.py --json", text)
        self.assertIn("deadline=$((SECONDS + 120))", text)
        self.assertIn("PRODUCTION_HEALTH_RETRY", text)
        self.assertIn("PRODUCTION_HEALTH_SETTLED", text)
        self.assertIn("PRODUCTION_HEALTH_BLOCKED", text)
        self.assertIn("last-known-good.json", text)
        self.assertIn("POSTDEPLOY_GREEN", text)
        self.assertIn("zcloud-production-deploy-evidence-", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn('"context": "zcloud/vps-production"', text)
        self.assertIn("Publish production deploy result", text)
        self.assertIn("steps.freshness.outputs.deploy_sha", text)
        self.assertNotIn("DEPLOY_SHA: ${{ github.event.workflow_run.head_sha }}", text)

    def test_code_deploy_preserves_runtime_owned_project_catalog(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        self.assertNotIn("- name: Bootstrap validated project catalog", text)
        self.assertNotIn("--path projects.json", text)
        self.assertNotIn("--path project-layout.json", text)
        self.assertEqual(4, text.count("--preserve-schema-validated-runtime-config"))
        core = text[
            text.index("- name: Promote backend runtime core"):
            text.index("- name: Promote autonomy policies and queue seed")
        ]
        self.assertIn("--preserve-schema-validated-runtime-config", core)
        self.assertIn("--allow-recent-ancestor-prechange-drift", core)
        self.assertIn("--reconcile-known-live-sha", core)
        self.assertIn("steps.server_drift_reconcile.outputs.enabled", core)

    def test_deploy_repairs_only_legacy_runtime_project_schema_before_promotion(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        repair = "Repair legacy runtime project schema"
        promote = "Promote backend runtime core"
        self.assertIn(repair, text)
        self.assertIn("scripts/zcloud_runtime_project_schema_repair.py", text)
        self.assertIn('--candidate "$GITHUB_WORKSPACE"', text)
        core = text[text.index("- name: Promote backend runtime core"):text.index("- name: Promote autonomy policies and queue seed")]
        self.assertNotIn("--path scripts/zcloud_runtime_project_schema_repair.py", core)
        self.assertLess(text.index(repair), text.index(promote))
        self.assertNotIn("--path projects.json", text)

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
        for path in (
            "server.py",
            "project_runtime.py",
            "project-contracts.json",
            "lane_generator.py",
            "vps-execution-policy.json",
        ):
            self.assertIn(f"--path {path}", core)
            self.assertNotIn(f"--path {path}", policy)
        for path in (
            "scripts/zcloud_recovery.py",
            "autonomy-policy.json",
            "portfolio_queue.seed.json",
        ):
            self.assertIn(f"--path {path}", policy)
            self.assertNotIn(f"--path {path}", core)
        self.assertLess(core.count("--path "), 6)
        self.assertLess(policy.count("--path "), 6)
        self.assertIn("--path vps-execution-policy.json", core)
        self.assertNotIn("--path vps-execution-policy.json", policy)
        self.assertNotIn("high_blast_radius_promotion", core + policy)

    def test_server_boot_policy_is_promoted_before_backend_restart(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        core = text[
            text.index("- name: Promote backend runtime core"):
            text.index("- name: Promote autonomy policies and queue seed")
        ]
        policy = text[
            text.index("- name: Promote autonomy policies and queue seed"):
            text.index("- name: Promote Violentmonkey worker")
        ]
        self.assertIn("--path server.py", core)
        self.assertIn("--path vps-execution-policy.json", core)
        self.assertNotIn("--path vps-execution-policy.json", policy)
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("VPS_EXECUTION_POLICY = _load_vps_execution_policy()", server)

    def test_successful_deploy_records_and_verifies_live_state_receipt(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        step = "Record evidence-backed production state receipt"
        self.assertIn(step, text)
        receipt = text[text.index("- name: " + step):text.index("- name: Publish production deploy result")]
        self.assertIn("if: success()", receipt)
        self.assertIn("scripts/zcloud_runtime.py", receipt)
        self.assertIn("--db /home/ubuntu/zennay-cloud/history.db", receipt)
        self.assertIn("receipt \\", receipt)
        self.assertIn("--project cloud", receipt)
        self.assertIn('--commit "$DEPLOY_SHA"', receipt)
        self.assertIn("--ci-status success", receipt)
        self.assertIn('github-actions:zcloud-vps-deploy', receipt)
        self.assertIn("lkg_snapshot_id", receipt)
        self.assertIn("REGRESSION_RUN_ID", receipt)
        self.assertIn("http://127.0.0.1:8765/api/status", receipt)
        self.assertIn("timeout=40", receipt)
        self.assertIn("max_attempts = 8", receipt)
        self.assertIn("for attempt in range(1, max_attempts + 1)", receipt)
        self.assertIn("ZCLOUD_PRODUCTION_RECEIPT_STATUS_RETRY", receipt)
        self.assertIn("import http.client", receipt)
        self.assertIn("urllib.error.HTTPError", receipt)
        self.assertIn("exc.code != 503", receipt)
        self.assertIn("http.client.RemoteDisconnected", receipt)
        self.assertIn("time.sleep(5)", receipt)
        self.assertIn('project.get("state_source") != "evidence_receipt"', receipt)
        self.assertIn('state.get("commit_sha") != expected_sha', receipt)
        self.assertIn("ZCLOUD_PRODUCTION_RECEIPT_GREEN", receipt)
        self.assertLess(text.index("Resume external self-heal"), text.index(step))
        self.assertLess(text.index(step), text.index("Publish production deploy result"))

    def test_publish_status_requires_actual_prewrite_confirmation(self):
        text = (ROOT / ".github/workflows/zcloud-vps-deploy.yml").read_text(encoding="utf-8")
        publish = text[text.index("- name: Publish production deploy result"):]
        self.assertIn(
            "if: always() && steps.freshness.outputs.deploy == 'true' && steps.prewrite.outputs.deploy == 'true'",
            publish,
        )
        self.assertIn("MAIN_MOVED_BEFORE_VPS_WRITE", text)

    def test_live_deploy_diagnostics_use_permanent_zcloud_vps_runner(self):
        text = (ROOT / ".github/workflows/zcloud-live-debug.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", text)
        self.assertNotIn("actions/checkout@v4", text)
        self.assertNotIn("runs-on: self-hosted\n", text)

    def test_execution_probe_uploads_sanitized_prechange_evidence(self):
        text = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("Inspect sanitized deploy pre-change evidence", text)
        self.assertIn("scripts/zcloud_prechange_evidence.py", text)
        self.assertIn("zcloud-prechange-evidence.json", text)
        self.assertNotIn("cat /home/ubuntu/zennay-cloud/projects.json", text)

    def test_execution_probe_claims_cloud_task_on_self_hosted_runner(self):
        text = (ROOT / ".github/workflows/zcloud-vps-execution-probe.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py acquire", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py verify", text)
        self.assertIn("scripts/zcloud_vps_deploylane_coordination.py release", text)
        self.assertIn("if: always()", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("Prepare stable probe state", text)
        self.assertIn("Diagnose runner workspace isolation", text)
        self.assertIn("zcloud-runner-topology.txt", text)
        self.assertNotIn("$RUNNER_TEMP/", text)
        self.assertIn("Cleanup stable probe state", text)

        audit = (ROOT / ".github/workflows/zcloud-state-receipt-coverage-audit.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6", audit)
        self.assertNotIn("actions/checkout@v4", audit)

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
