import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ZsshDeterministicReleaseVarsTest(unittest.TestCase):
    def test_seed_lane_is_exact_non_secret_and_governance_gated(self):
        text = (ROOT / ".github/workflows/zssh-seed-deterministic-release-vars.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, zcloud, vps]", text)
        self.assertIn("scripts/zcloud_vps_runner_guard.py --json", text)
        self.assertIn('test "$(hostname)" = "vps-bb300bba"', text)
        self.assertIn('repos/$repo/branches/main', text)
        self.assertIn('repos/$repo/issues/100', text)
        self.assertIn("DEFAULT_PRODUCTION_MCP_URL", text)
        self.assertIn("https://zssh.cheapgpt.shop/mcp", text)
        self.assertIn("ZSSH_REVIEW_FILE=/srv/zssh-review/sample.txt", text)
        self.assertIn("ZSSH_REVIEW_WRITE_FILE=/srv/zssh-review/output.txt", text)
        self.assertIn("environments/openai-production/variables", text)
        self.assertIn('set_exact_var ZSSH_PLUGIN_MCP_URL "https://zssh.cheapgpt.shop/mcp"', text)
        self.assertIn('set_exact_var ZSSH_REVIEW_FILE "/srv/zssh-review/sample.txt"', text)
        self.assertIn('set_exact_var ZSSH_REVIEW_WRITE_FILE "/srv/zssh-review/output.txt"', text)
        self.assertIn("ZSSH_DETERMINISTIC_RELEASE_VARS_GREEN", text)
        self.assertNotIn("CLOUDFLARE_", text)
        self.assertNotIn("AUTH0_MANAGEMENT_API_TOKEN", text)
        self.assertNotIn("ZSSH_REVIEW_ACCESS_TOKEN", text)
        self.assertNotIn("OPENAI_APPS_CHALLENGE_TOKEN", text)
        self.assertNotIn("/secrets", text)
        self.assertNotIn("gh secret set", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("set -x", text)


if __name__ == "__main__":
    unittest.main()
