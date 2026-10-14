"""Offline tests for manual-only, bounded clean Linux page-cache recovery."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import zcloud_manual_pagecache_reclaim_policy_20261013_w2 as p

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/zcloud-safe-memory-reclaim-20261013.yml"


def meminfo(available=900 * 1024, cached=600 * 1024, dirty=1024, writeback=0,
            total=8 * 1024 * 1024):
    return "\n".join([
        f"MemTotal:       {total} kB",
        f"MemAvailable:       {available} kB",
        f"Cached:       {cached} kB",
        f"Dirty:       {dirty} kB",
        f"Writeback:       {writeback} kB",
        "HugePages_Total:       0",
        "HugePages_Free:       0",
        "Hugepagesize:       2048 kB",
    ]) + "\n"


class PolicyTests(unittest.TestCase):
    def test_actual_linux_meminfo_has_unitless_hugepage_fields(self):
        vals = p.parse_meminfo(meminfo())
        self.assertEqual(vals["HugePages_Total"], 0)
        self.assertEqual(vals["MemAvailable"], 900 * 1024)

    def test_bounded_pressure_allows_only_clean_page_cache(self):
        yes, reason = p.decision(p.parse_meminfo(meminfo()))
        self.assertTrue(yes)
        self.assertEqual(reason, "bounded-clean-cache-reclaim-eligible")

    def test_critical_memory_under_512m_does_not_drop_caches(self):
        allowed, reason = p.decision(p.parse_meminfo(meminfo(available=511 * 1024)))
        self.assertFalse(allowed)
        self.assertEqual(reason, "critical-memory-protect")

    def test_ample_memory_2g_and_above_does_not_drop_caches(self):
        for mb in (2048, 3072, 4096):
            with self.subTest(mb=mb):
                yes, reason = p.decision(p.parse_meminfo(meminfo(available=mb * 1024)))
                self.assertFalse(yes)
                self.assertEqual(reason, "no-memory-pressure")

    def test_insufficient_cached_pages_does_not_trigger(self):
        yes, why = p.decision(p.parse_meminfo(meminfo(cached=255 * 1024)))
        self.assertFalse(yes)
        self.assertEqual(why, "insufficient-clean-cache")

    def test_dirty_and_writeback_pages_deny_root_cache_drop(self):
        for dirty, writeback in ((128 * 1024, 0), (20, 1), (129 * 1024, 0)):
            with self.subTest(dirty=dirty, writeback=writeback):
                yes, why = p.decision(p.parse_meminfo(meminfo(dirty=dirty, writeback=writeback)))
                self.assertFalse(yes)
                self.assertEqual(why, "writeback-busy")

    def test_missing_required_field_denied(self):
        bad = meminfo().replace("MemAvailable:", "MemoryAvailable:")
        with self.assertRaises(p.Denied):
            p.parse_meminfo(bad)

    def test_invalid_required_units_denied(self):
        with self.assertRaises(p.Denied):
            p.parse_meminfo(meminfo().replace("MemTotal:       8388608 kB",
                                               "MemTotal:       8388608"))

    def test_invalid_duplicate_and_oversized_denied(self):
        for raw in ("", "not meminfo", meminfo() + "Dirty: 5 kB\n",
                    meminfo() + "Inject: 1; echo bad\n", "A" * (p.MAX_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(p.Denied):
                p.parse_meminfo(raw)

    def test_inconsistent_values_denied(self):
        for values in (meminfo(available=9 * 1024 * 1024),
                       meminfo(total=400 * 1024),
                       meminfo(cached=9 * 1024 * 1024)):
            with self.assertRaises(p.Denied):
                p.parse_meminfo(values)

    def test_boolean_and_malformed_types_denied(self):
        for malformed in (None, [], {}, {"MemTotal": True}, {"MemTotal": 1.5}):
            with self.subTest(value=malformed), self.assertRaises(p.Denied):
                p.decision(malformed)

    def test_file_cli_positive_is_never_root_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "meminfo"
            src.write_text(meminfo())
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = p.main(src)
            value = json.loads(buf.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(value["eligible"])
            self.assertFalse(value["mutation_performed"])
            self.assertFalse(value["release_authorized"])

    def test_cli_negative_returns_nonzero_and_sanitized(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "meminfo"
            src.write_text(meminfo(available=4 * 1024 * 1024))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = p.main(src)
            value = json.loads(buf.getvalue())
            self.assertEqual(code, 3)
            self.assertFalse(value["eligible"])
            self.assertEqual(value["reason"], "no-memory-pressure")
            self.assertNotIn(str(src), buf.getvalue())

    def test_cli_missing_symlink_and_invalid_files_deny(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            absent = root / "missing"
            src = root / "meminfo"
            src.write_text(meminfo())
            alias = root / "symlink"
            alias.symlink_to(src)
            bad = root / "bad"
            bad.write_text("secret invalid health input")
            for path in (absent, alias, bad):
                with self.subTest(path=path):
                    out = io.StringIO()
                    with redirect_stdout(out):
                        self.assertEqual(p.main(path), 2)
                    result = json.loads(out.getvalue())
                    self.assertFalse(result["release_authorized"])
                    self.assertNotIn("secret", out.getvalue())


class WorkflowTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()
        cls.validate = cls.text.split("  validate:\n", 1)[1].split("\n  reclaim:\n", 1)[0]
        cls.reclaim = cls.text.split("  reclaim:\n", 1)[1]

    def test_no_auto_push_or_workflow_run(self):
        event = self.text.split("\npermissions:", 1)[0]
        self.assertIn("\n  pull_request:\n", event)
        self.assertIn("\n  workflow_dispatch:\n", event)
        self.assertNotIn("\n  push:\n", event)
        self.assertNotIn("\n  workflow_run:\n", event)
        self.assertIn("confirm_sha:", event)
        self.assertIn("maintenance_ack:", event)
        self.assertEqual(event.count("required: true"), 2)

    def test_hosted_pull_request_validation_cannot_touch_vps(self):
        self.assertIn("runs-on: ubuntu-latest", self.validate)
        self.assertIn("github.event_name == 'pull_request'", self.validate)
        self.assertNotIn("self-hosted", self.validate)
        self.assertNotIn("sudo ", self.validate)
        self.assertNotIn("drop_caches", self.validate)
        self.assertIn("python3 -m unittest -v tests.test_zcloud_manual_pagecache_reclaim_policy_20261013_w2", self.validate)

    def test_manual_main_owner_exact_head_gate(self):
        for key in (
            "github.event_name == 'workflow_dispatch'",
            "github.repository == 'Zennay/zCloud'",
            "github.ref == 'refs/heads/main'",
            "github.actor == 'Zennay'",
            "inputs.maintenance_ack == 'I_APPROVE_PAGECACHE_RECLAIM'",
            "runs-on: [self-hosted, zcloud, vps]",
        ):
            self.assertIn(key, self.reclaim)
        self.assertEqual(self.text.count("runs-on: [self-hosted, zcloud, vps]"), 1)

    def test_root_job_rejects_same_owner_reruns_and_requires_sha(self):
        job_gate = self.reclaim.split("    runs-on:", 1)[0]
        for required in (
            "github.event_name == 'workflow_dispatch'",
            "github.triggering_actor == 'Zennay'",
            "github.run_attempt == 1",
            "inputs.confirm_sha == github.sha",
            "inputs.maintenance_ack == 'I_APPROVE_PAGECACHE_RECLAIM'",
        ):
            with self.subTest(required=required):
                self.assertIn(required, job_gate)
        self.assertNotIn("||", job_gate)

    def test_runtime_rechecks_dispatch_identity_and_attempt(self):
        trust = self.reclaim.split("      - name: Verify manual approval", 1)[1].split("      - name: Reclaim clean", 1)[0]
        for required in (
            'test "$GITHUB_TRIGGERING_ACTOR" = Zennay',
            'test "$GITHUB_RUN_ATTEMPT" = "1"',
            'test "$GITHUB_REPOSITORY" = Zennay/zCloud',
            'test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, trust)

    def test_bounded_live_main_verifications_before_privileged_kernel_write(self):
        self.assertGreaterEqual(
            self.reclaim.count("timeout 15 git ls-remote --exit-code origin refs/heads/main"),
            3,
        )
        for var in ("current_main", "last_main", "root_main"):
            with self.subTest(var=var):
                self.assertIn(f'test -n "$\u007bvar\u007d"', self.reclaim)
                self.assertIn(f'test "$\u007bvar\u007d" = "$GITHUB_SHA"', self.reclaim)
        root = self.reclaim.index('root_main="$(timeout 15 git ls-remote')
        kernel = self.reclaim.index("sudo -n sh -c 'echo 1 > /proc/sys/vm/drop_caches'")
        self.assertLess(root, kernel)
        self.assertLess(self.reclaim.index("python3 scripts/zcloud_manual_pagecache_reclaim_policy_20261013_w2.py; then"), root)
        self.assertNotIn("|| true", self.reclaim[root:kernel])

    def test_checkout_sha_and_canonical_runner_guard_precede_root_action(self):
        self.assertEqual(self.text.count("persist-credentials: false"), 2)
        self.assertEqual(self.text.count("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"), 2)
        trust = self.reclaim.index("Verify manual approval, immutable current main")
        drop = self.reclaim.index("sudo -n sh -c 'echo 1 > /proc/sys/vm/drop_caches'")
        self.assertLess(trust, drop)
        for assertion in ('test "$CONFIRMED_SHA" = "$GITHUB_SHA"',
                          "git ls-remote --exit-code origin refs/heads/main",
                          "python3 scripts/zcloud_vps_runner_guard.py --json"):
            self.assertIn(assertion, self.reclaim[trust:drop])

    def test_memory_policy_mandatory_immediately_before_root(self):
        previous = self.reclaim.index("if python3 scripts/zcloud_manual_pagecache_reclaim_policy_20261013_w2.py; then")
        action = self.reclaim.index("sudo -n sh -c 'echo 1 > /proc/sys/vm/drop_caches'")
        self.assertLess(previous, action)
        self.assertIn('SAFE_RECLAIM_SKIPPED=memory-policy-not-eligible', self.reclaim)
        # Ignore deny-only comments; inspect commands, not explanatory text.
        commands = "\n".join(line for line in self.reclaim.splitlines()
                             if not line.lstrip().startswith("#"))
        self.assertNotIn("swapoff", commands)
        self.assertNotIn("systemctl restart", commands)
        self.assertNotIn("pkill ", commands)

    def test_mutex_is_only_in_privileged_job(self):
        self.assertIn("group: zcloud-production-deploy", self.reclaim)
        self.assertNotIn("group: zcloud-production-deploy", self.validate)
        self.assertIn("cancel-in-progress: false", self.reclaim)


if __name__ == "__main__":
    unittest.main()
