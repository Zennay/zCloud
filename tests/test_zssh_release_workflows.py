from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts import zssh_release_coordination as coordination

ROOT = Path(__file__).resolve().parents[1]


class ZsshReleaseWorkflowTests(unittest.TestCase):
    def test_public_plugin_queue_stays_below_ftmo_p0(self):
        text = (ROOT / ".github/workflows/zssh-public-plugin-priority.yml").read_text(encoding="utf-8")
        self.assertIn('"priority": "P1"', text)
        self.assertIn('!= expected["priority"]', text)
        self.assertNotIn('"priority": "P0"', text)

    def test_release_coordination_uses_control_plane_health_profile(self):
        text = (ROOT / "scripts/zssh_release_coordination.py").read_text(encoding="utf-8")
        self.assertIn('"vps_profile": "control_plane"', text)
        self.assertIn('get_json(BASE + "/api/status")', text)

    def test_release_only_main_advance_allows_review_evidence_drift(self):
        self.assertTrue(coordination.release_only_main_advance([
            ".github/workflows/public-release-gate.yml",
            "scripts/check-public-release-config.mjs",
            "docs/openai-plugin-review.md",
        ]))

    def test_release_only_main_advance_rejects_runtime_drift(self):
        self.assertFalse(coordination.release_only_main_advance([
            ".github/workflows/public-release-gate.yml",
            "server.mjs",
        ]))
        self.assertFalse(coordination.release_only_main_advance([]))

    def test_vps_release_proves_public_listing_site_without_switching_live_profile(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("ZSSH_RELEASE_SHA: c1a249e4995605b025496a0178cacbc4cfcecf41", text)
        self.assertIn("Verify isolated public review site on exact release", text)
        self.assertIn("ZSSH_PUBLIC_LISTING_SITE_VPS_GREEN", text)
        self.assertIn("ZSSH_PLUGIN_PROFILE=public", text)
        self.assertIn("ZSSH_PUBLIC_AUTH_MODE=legacy", text)
        self.assertIn("Your Linux target stays yours.", text)
        self.assertIn("/support /privacy /terms", text)

    def test_public_release_finalizer_is_exact_and_success_gated(self):
        text = (ROOT / ".github/workflows/finalize-zssh-public-release-20261002.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("zssh-openai-public-plugin-release", text)
        self.assertIn("c1a249e4995605b025496a0178cacbc4cfcecf41", text)
        self.assertIn("ZCLOUD_ZSSH_PUBLIC_RELEASE_QUEUE_DONE_GREEN=1", text)
        self.assertNotIn("portfolio_queue_drop", text)

    def test_vps_release_uses_permanent_runner_guard(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: self-hosted", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        match = re.search(r"ZSSH_RELEASE_SHA:\s*([0-9a-f]{40})", text)
        self.assertIsNotNone(match)

    def test_legacy_vps_release_runner_is_pinned_to_current_green_zssh_and_permanent_runner(self):
        text = (ROOT / ".github/workflows/zssh-vps-release-runner.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("ZSSH_RELEASE_SHA: aa50412cc057fb65744809de34942bf9603e0319", text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn("ref: ${{ env.ZSSH_RELEASE_SHA }}", text)
        self.assertIn('test "$(git -C zssh-source rev-parse HEAD)" = "$ZSSH_RELEASE_SHA"', text)
        self.assertIn("ZSSH_LIVE_PROVENANCE_GREEN", text)
        self.assertIn("ZSSH_HOSTED_CLIENT_CANARIES_GREEN", text)

    def test_vps_release_records_evidence_backed_zssh_receipt(self):
        text = (ROOT / ".github/workflows/zssh-standalone-vps-release.yml").read_text(encoding="utf-8")
        self.assertIn("Record evidence-backed zSSH release state receipt", text)
        self.assertIn("--project zssh", text)
        self.assertIn('--commit "$ZSSH_RELEASE_SHA"', text)
        self.assertIn("--ci-status success", text)
        self.assertIn('"live_marker": "ZSSH_STANDALONE_M1_LIVE_GREEN"', text)
        self.assertIn('"public_listing_marker": "ZSSH_PUBLIC_LISTING_SITE_VPS_GREEN"', text)
        self.assertIn('--source "github-actions:zssh-standalone-vps-release"', text)
        receipt_pos = text.index("Record evidence-backed zSSH release state receipt")
        release_pos = text.index("Release coordination claim")
        self.assertLess(receipt_pos, release_pos)

    def test_public_gateway_validate_only_proof_uses_zcloud_runner_lane(self):
        text = (ROOT / ".github/workflows/zssh-public-gateway-vps-preflight.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertNotRegex(text, r"ZSSH_SHA:\s*[0-9a-f]{40}")
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("id: zssh_target", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn('echo "ZSSH_SHA=$zssh_sha" >> "$GITHUB_ENV"', text)
        self.assertIn('ref: ${{ steps.zssh_target.outputs.sha }}', text)
        self.assertIn("ZSSH_CANONICAL_MAIN_RESOLVED", text)
        self.assertIn('test "$live_main_after" = "$ZSSH_SHA"', text)
        self.assertIn("ZSSH_CANONICAL_MAIN_RECONFIRMED", text)
        self.assertIn('test "$(git -C zssh-source rev-parse HEAD)" = "$ZSSH_SHA"', text)
        self.assertIn('bash "$source_root/deploy/prepare-reviewer-target.sh" "$source_root" > "$reviewer_report"', text)
        self.assertNotIn('npm --prefix "$source_root" run review:target', text)
        self.assertIn('ZSSH_PUBLIC_BASE_URL="https://mcp.zssh-preflight.invalid"', text)
        self.assertIn('ZSSH_PUBLIC_RATE_LIMIT_PER_MINUTE=120', text)
        self.assertIn('ZSSH_PUBLIC_RATE_LIMIT_MAX_PROFILES=10000', text)
        self.assertIn("Exercise zSSH public gateway rollback regression on VPS", text)
        self.assertIn("node --test test/public-gateway-installer.test.mjs", text)
        self.assertIn("ZSSH_PUBLIC_GATEWAY_ROLLBACK_REGRESSION_GREEN", text)
        self.assertIn("public_gateway_rollback_regression_self_hosted: true", text)
        self.assertIn('public_rate_limit_per_minute: Number(process.env.RATE_LIMIT_PER_MINUTE)', text)
        self.assertIn('public_rate_limit_max_profiles: Number(process.env.RATE_LIMIT_MAX_PROFILES)', text)
        self.assertIn('ZSSH_PUBLIC_GATEWAY_VALIDATE_ONLY=1', text)
        self.assertIn('test "$after_env" = "$before_env"', text)
        self.assertIn('test "$after_current" = "$before_current"', text)
        self.assertIn('test "$after_active" = "$before_active"', text)
        self.assertIn('test "$after_enabled" = "$before_enabled"', text)
        self.assertIn("ZSSH_PUBLIC_GATEWAY_VPS_PREFLIGHT_GREEN", text)
        self.assertIn('--project zssh', text)
        self.assertIn('--commit "$ZSSH_SHA"', text)
        self.assertIn('--source "github-actions:zssh-public-gateway-vps-preflight"', text)


    def test_public_gateway_activation_is_explicit_exact_main_and_mutating(self):
        text = (ROOT / ".github/workflows/zssh-public-gateway-activate.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("ACTIVATE_ZSSH_PUBLIC_GATEWAY", text)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("expected_zssh_sha:", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn('test "$live_main" = "$EXPECTED_ZSSH_SHA"', text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn('ref: ${{ steps.zssh_target.outputs.sha }}', text)
        self.assertIn("node --test test/public-gateway-installer.test.mjs", text)
        self.assertIn("scripts/render-public-caddy.mjs", text)
        self.assertIn("reverse_proxy 127.0.0.1:8789", text)
        self.assertNotIn("ZSSH_PUBLIC_GATEWAY_VALIDATE_ONLY=1", text)
        self.assertIn('bash "$source_root/deploy/install-public-gateway.sh" "$source_root"', text)
        self.assertIn("ZSSH_PUBLIC_GATEWAY_INSTALL_GREEN", text)
        self.assertIn("systemctl --user is-active --quiet zssh-public.service", text)
        self.assertIn("systemctl --user is-enabled --quiet zssh-public.service", text)
        self.assertIn('curl --fail --silent --show-error "http://127.0.0.1:8789/health"', text)
        self.assertIn('test "$live_main_after" = "$ZSSH_SHA"', text)
        self.assertIn("ZSSH_PUBLIC_GATEWAY_ACTIVATION_GREEN", text)
        self.assertIn('--project zssh', text)
        self.assertIn('--source "github-actions:zssh-public-gateway-activate"', text)
        self.assertIn("challenge_token_configured_by_this_workflow: false", text)
        self.assertNotIn("OPENAI_APPS_CHALLENGE_TOKEN:", text)


    def test_production_origin_readiness_is_exact_and_non_mutating(self):
        text = (ROOT / ".github/workflows/zssh-production-origin-readiness.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("ZSSH_PRODUCTION_ORIGIN: https://zssh.cheapgpt.shop", text)
        self.assertIn("ZSSH_EXPECTED_IPV4: 198.244.191.182", text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn('ZSSH_PUBLIC_GATEWAY_VALIDATE_ONLY=1', text)
        self.assertIn('node "$source_root/scripts/render-public-caddy.mjs"', text)
        self.assertIn('grep -Fx "    reverse_proxy 127.0.0.1:8789"', text)
        self.assertIn('"dns_ready": dns_ready', text)
        self.assertIn('"public_gateway_state_mutated": False', text)
        self.assertIn('"oauth_external_gate_remaining": True', text)
        self.assertIn("ZSSH_PRODUCTION_ORIGIN_DNS_PENDING", text)
        self.assertIn("ZSSH_PRODUCTION_ORIGIN_DNS_GREEN", text)
        self.assertIn("--project zssh", text)
        self.assertIn('--source "github-actions:zssh-production-origin-readiness"', text)
        self.assertIn("statuses: write", text)
        self.assertIn("zssh/production-origin-readiness-audit", text)
        self.assertIn("ZSSH_PRODUCTION_ORIGIN_STATUS_PUBLISHED", text)
        self.assertIn('DNS_READY: ${{ steps.readiness.outputs.dns_ready }}', text)
        self.assertNotIn("systemctl --user enable --now zssh-public.service", text)


    def test_caddy_topology_audit_is_non_mutating_and_evidence_backed(self):
        text = (ROOT / ".github/workflows/zssh-caddy-topology-audit.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("ZSSH_PRODUCTION_HOST: zssh.cheapgpt.shop", text)
        self.assertIn("ZSSH_PUBLIC_UPSTREAM: 127.0.0.1:8789", text)
        self.assertIn("sudo -n true", text)
        self.assertIn("systemctl is-active --quiet caddy", text)
        self.assertIn("caddy validate --config", text.replace('\"$caddy_binary\"', "caddy"))
        self.assertIn("caddyfile_imports_conf_d", text)
        self.assertIn("caddyfile_imports_sites_enabled", text)
        self.assertIn('"promotion_strategy": os.environ["STRATEGY"]', text)
        self.assertIn('"configuration_content_exposed": False', text)
        self.assertIn('"configuration_mutated": False', text)
        self.assertIn("ZSSH_CADDY_TOPOLOGY_AUDIT_GREEN", text)
        self.assertIn('--project zssh', text)
        self.assertIn('--source "github-actions:zssh-caddy-topology-audit"', text)
        self.assertNotIn("sudo -n install", text)
        self.assertNotIn("systemctl reload caddy", text)
        self.assertNotIn("systemctl restart caddy", text)


    def test_public_ingress_bootstrap_is_dns_gateway_and_exact_main_gated(self):
        text = (ROOT / ".github/workflows/zssh-public-ingress-bootstrap.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("INSTALL_ZSSH_PUBLIC_INGRESS", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("ZSSH_PRODUCTION_ORIGIN: https://zssh.cheapgpt.shop", text)
        self.assertIn("ZSSH_EXPECTED_IPV4: 198.244.191.182", text)
        self.assertIn("if found != {expected}:", text)
        self.assertIn("systemctl --user is-active --quiet zssh-public.service", text)
        self.assertIn('http://127.0.0.1:$ZSSH_PUBLIC_GATEWAY_PORT/health', text)
        self.assertIn("expected_zssh_sha:", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn('test "$live_main" = "$EXPECTED_ZSSH_SHA"', text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn('ref: ${{ steps.zssh_target.outputs.sha }}', text)
        self.assertIn("test/public-caddy-installer.test.mjs", text)
        self.assertIn("https://dl.cloudsmith.io/public/caddy/stable/gpg.key", text)
        self.assertIn("https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt", text)
        self.assertIn("apt-get install --yes caddy", text)
        self.assertIn("deploy/install-public-caddy.sh", text)
        self.assertIn("systemctl is-active --quiet caddy", text)
        self.assertIn('--resolve "$ZSSH_PRODUCTION_HOST:443:127.0.0.1"', text)
        self.assertIn("ZSSH_PUBLIC_INGRESS_BOOTSTRAP_GREEN", text)
        self.assertIn('--source "github-actions:zssh-public-ingress-bootstrap"', text)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", text)
        self.assertNotIn("CF_API_TOKEN", text)


    def test_vps_github_admin_capability_probe_is_read_only_and_zssh_scoped(self):
        text = (ROOT / ".github/workflows/zssh-gh-admin-capability-probe.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn("gh auth status --hostname github.com", text)
        self.assertIn("gh api repos/Zennay/zSSH --jq '.permissions.admin // false'", text)
        self.assertIn("gh api repos/Zennay/zSSH/branches/main --jq '.protected // false'", text)
        self.assertIn("repos/Zennay/zSSH/branches/main/protection", text)
        self.assertIn('"mutation_attempted": False', text)
        self.assertIn("ZSSH_VPS_GH_ADMIN_CAPABILITY_PRESENT", text)
        self.assertIn("ZSSH_VPS_GH_ADMIN_CAPABILITY_ABSENT", text)
        self.assertNotIn("--method PUT", text)
        self.assertNotIn("--method PATCH", text)
        self.assertNotIn("-X PUT", text)
        self.assertNotIn("-X PATCH", text)
        self.assertNotIn("apply-main-protection", text)


    def test_vps_main_protection_apply_is_exact_admin_gated_and_secret_safe(self):
        text = (ROOT / ".github/workflows/zssh-main-protection-vps-apply.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("workflow_run:", text)
        self.assertIn('"zSSH GitHub admin capability probe"', text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("PROTECT_ZSSH_MAIN", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn('ref: ${{ steps.zssh_target.outputs.sha }}', text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn("gh auth status --hostname github.com", text)
        self.assertIn("gh api repos/Zennay/zSSH --jq '.permissions.admin // false'", text)
        self.assertIn("gh auth token --hostname github.com", text)
        self.assertIn("node --test test/main-protection*.test.mjs", text)
        self.assertIn("scripts/apply-main-protection.mjs --apply", text)
        self.assertIn("scripts/check-main-protection.mjs", text)
        self.assertIn("ZSSH_MAIN_PROTECTION_VPS_APPLY_GREEN", text)
        self.assertIn('test "$live_main_after" = "$ZSSH_SHA"', text)
        self.assertNotIn('echo "$token"', text)
        self.assertNotIn("set -x", text)
        self.assertNotIn("ZSSH_REPO_ADMIN_TOKEN: ${{", text)


    def test_zssh_governance_runner_priority_only_cancels_older_queued_read_only_audits(self):
        text = (ROOT / ".github/workflows/zssh-governance-runner-priority.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertIn("actions: write", text)
        self.assertIn("contents: read", text)
        self.assertIn("-f status=queued", text)
        self.assertIn('"zSSH main protection VPS apply"', text)
        self.assertIn('"zSSH production origin readiness (zCloud lane)"', text)
        self.assertIn('"zSSH Caddy topology audit (zCloud lane)"', text)
        self.assertIn('"zSSH public gateway VPS preflight (zCloud lane)"', text)
        self.assertIn('run.get("status") == "queued"', text)
        self.assertIn('(run.get("created_at") or "") < target_created', text)
        self.assertIn('actions/runs/$run_id/cancel', text)
        self.assertIn("queued-only older allowlisted zSSH read-only VPS audits", text)
        self.assertNotIn("runs-on: [self-hosted", text)
        self.assertNotIn("runs-on: self-hosted", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertIn("name: zssh-governance-runner-priority-${{ github.run_id }}", text)
        self.assertIn("path: ${{ runner.temp }}/zssh-governance-runner-priority.json", text)
        self.assertNotIn("\\${{", text)
        self.assertNotIn("zSSH main protection VPS apply\",\\n              \"", text)



    def test_zssh_main_protection_attestation_is_evidence_gated_and_single_variable_scoped(self):
        text = (ROOT / ".github/workflows/zssh-attest-main-protection.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn('repos/$repo/branches/main/protection', text)
        self.assertIn('check.get("context") == "test"', text)
        self.assertIn("15368", text)
        self.assertIn('issue_state="$(gh api "repos/$repo/issues/100"', text)
        self.assertIn("284dadd80a7e46fc9203ed5e5caa9a5709e3f24b", text)
        self.assertIn("HTTP 422", text)
        self.assertIn("Changes must be made through a pull request", text)
        self.assertIn('parent_tree="$(gh api "repos/$repo/git/commits/$canary_parent"', text)
        self.assertIn('test "$canary_tree" = "$parent_tree"', text)
        self.assertIn('repos/$repo/compare/$canary_parent...$main_sha', text)
        self.assertIn('ahead|identical', text)
        self.assertNotIn('test "$canary_parent" = "$main_sha"', text)
        self.assertNotIn('test "$canary_tree" = "$main_tree"', text)
        self.assertIn("environments/openai-production/variables", text)
        self.assertIn("ZSSH_MAIN_PROTECTION_VERIFIED", text)
        self.assertIn("-f value=1", text)
        self.assertIn("ZSSH_MAIN_PROTECTION_ATTESTATION_GREEN", text)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", text)
        self.assertNotIn("AUTH0_MANAGEMENT_API_TOKEN", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("git push", text)



    def test_cloudflare_credential_probe_reads_metadata_only_and_never_secret_values(self):
        text = (ROOT / ".github/workflows/zssh-cloudflare-credential-probe.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn("actions/secrets?per_page=100", text)
        self.assertIn("actions/variables?per_page=100", text)
        self.assertIn("environments/{encoded}/secrets?per_page=100", text)
        self.assertIn("environments/{encoded}/variables?per_page=100", text)
        self.assertIn('"secret_values_read": False', text)
        self.assertIn("CLOUDFLARE_API_TOKEN", text)
        self.assertIn("CLOUDFLARE_ZONE_ID", text)
        self.assertIn('"reusable_exact_pair_present"', text)
        self.assertNotIn("gh secret set", text)
        self.assertNotIn("gh variable set", text)
        self.assertNotIn("--method PUT", text)
        self.assertNotIn("--method PATCH", text)
        self.assertNotIn("--method POST", text)
        self.assertNotIn("cat ~/.config", text)



    def test_vps_cloudflare_dns_apply_is_exact_inherited_env_and_secret_safe(self):
        text = (ROOT / ".github/workflows/zssh-cloudflare-vps-dns-apply.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("types: [closed]", text)
        self.assertIn(".github/zssh-production-dns-vps-trigger", text)
        self.assertIn("github.event.pull_request.merged == true", text)
        self.assertIn("PUBLISH_ZSSH_PRODUCTION_DNS_VIA_VPS", text)
        self.assertIn("ZSSH_DNS_VPS_MERGED_PR_ACTIVATION_GREEN", text)
        self.assertNotIn("\n  push:\n", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn("repository: Zennay/zSSH", text)
        self.assertIn("git ls-remote https://github.com/Zennay/zSSH.git refs/heads/main", text)
        self.assertIn("https://zssh.cheapgpt.shop", text)
        self.assertIn("198.244.191.182", text)
        self.assertIn("CLOUDFLARE_ZONE_NAME: cheapgpt.shop", text)
        self.assertIn("CLOUDFLARE_API_TOKEN", text)
        self.assertIn("CLOUDFLARE_ZONE_ID", text)
        self.assertIn("cloudflare_zone_id_required", text)
        self.assertIn('if [ "$token_present" != "true" ]; then', text)
        self.assertNotIn('|| [ "$zone_present" != "true" ]', text)
        self.assertIn("zone_discovery=$CLOUDFLARE_ZONE_NAME", text)
        self.assertIn("runner-process-environment", text)
        self.assertIn("credential_values_emitted", text)
        self.assertIn("scripts/publish-cloudflare-dns.mjs", text)
        self.assertIn('ZSSH_DNS_APPLY: "0"', text)
        self.assertIn('ZSSH_DNS_APPLY: "1"', text)
        self.assertIn("scripts/observe-public-origin-readiness.mjs", text)
        self.assertIn("ZSSH_PRODUCTION_DNS_VPS_GREEN", text)
        self.assertNotIn("set -x", text)
        self.assertNotIn('echo "$CLOUDFLARE_API_TOKEN"', text)
        self.assertNotIn('echo "$CLOUDFLARE_ZONE_ID"', text)
        self.assertNotIn("gh secret", text)
        self.assertNotIn("cat ~/.config", text)


    def test_auth0_reviewer_metadata_probe_reads_names_only_and_never_secret_values(self):
        text = (ROOT / ".github/workflows/zssh-auth0-reviewer-metadata-probe.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("types: [closed]", text)
        self.assertIn(".github/zssh-auth0-reviewer-probe-trigger", text)
        self.assertIn("github.event.pull_request.merged == true", text)
        self.assertIn("PROBE_ZSSH_AUTH0_REVIEWER_METADATA", text)
        self.assertIn("ZSSH_AUTH0_REVIEWER_PROBE_MERGED_PR_ACTIVATION_GREEN", text)
        self.assertNotIn("\n  push:\n", text)
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn("unset GH_TOKEN GITHUB_TOKEN", text)
        self.assertIn("actions/secrets?per_page=100", text)
        self.assertIn("actions/variables?per_page=100", text)
        self.assertIn("environments/{encoded}/secrets?per_page=100", text)
        self.assertIn("environments/{encoded}/variables?per_page=100", text)
        self.assertIn("AUTH0_MANAGEMENT_API_TOKEN", text)
        self.assertIn("AUTH0_MANAGEMENT_BASE_URL", text)
        self.assertIn("ZSSH_OAUTH_ISSUER", text)
        self.assertIn("ZSSH_PLUGIN_MCP_URL", text)
        self.assertIn("ZSSH_REVIEW_ACCESS_TOKEN", text)
        self.assertIn("ZSSH_REVIEW_LOGIN_URL", text)
        self.assertIn("ZSSH_REVIEW_FILE", text)
        self.assertIn("ZSSH_REVIEW_WRITE_FILE", text)
        self.assertIn("ZSSH_PLUGIN_DEMO_RECORDING_URL", text)
        self.assertIn('"secret_values_read": False', text)
        self.assertIn('"mutation_attempted": False', text)
        self.assertIn("openai_production_auth0_missing", text)
        self.assertIn("openai_production_reviewer_missing", text)
        self.assertNotIn("gh secret set", text)
        self.assertNotIn("gh variable set", text)
        self.assertNotIn("--method PUT", text)
        self.assertNotIn("--method PATCH", text)
        self.assertNotIn("--method POST", text)
        self.assertNotIn("cat ~/.config", text)


    def test_current_m5_vps_lanes_pin_checkout_to_immutable_commit(self):
        expected = "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2"
        workflows = [
            "zssh-cloudflare-vps-dns-apply.yml",
            "zssh-cloudflare-capability-probe.yml",
            "zssh-cloudflare-credential-probe.yml",
            "zssh-public-gateway-vps-preflight.yml",
            "zssh-public-gateway-preflight.yml",
            "zssh-public-gateway-activate.yml",
            "zssh-caddy-topology-audit.yml",
            "zssh-public-ingress-bootstrap.yml",
            "zssh-production-origin-readiness.yml",
            "zssh-seed-deterministic-release-vars.yml",
        ]
        for workflow in workflows:
            with self.subTest(workflow=workflow):
                text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
                self.assertIn(expected, text)
                self.assertNotRegex(text, r"uses:\\s+actions/checkout@v\\d")


if __name__ == "__main__":
    unittest.main()
