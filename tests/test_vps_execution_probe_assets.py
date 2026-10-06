from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "zcloud_vps_execution_probe.sh"


class VpsExecutionProbeAssetsTests(unittest.TestCase):
    def test_safe_idle_diagnostic_is_read_only_and_bounded(self):
        text = PROBE.read_text(encoding="utf-8")
        start = text.index("ZCLOUD_SAFE_IDLE_DIAGNOSTIC_BEGIN")
        end = text.index("ZCLOUD_SAFE_IDLE_DIAGNOSTIC_END")
        block = text[start:end]
        self.assertIn('sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)', block)
        self.assertIn("FROM ai_global_slots ORDER BY slot", block)
        self.assertIn("FROM runner_workers ORDER BY project_id,worker_slot", block)
        self.assertIn("FROM runner_commands WHERE action='drain'", block)
        self.assertIn("ORDER BY id DESC LIMIT 40", block)
        self.assertIn("FROM runner_events", block)
        self.assertIn("ORDER BY id DESC LIMIT 160", block)
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ", "DROP ", "ALTER "):
            self.assertNotIn(forbidden, block.upper())
        self.assertLess(end, text.index("ZCLOUD_VPS_PROBE=GREEN"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
