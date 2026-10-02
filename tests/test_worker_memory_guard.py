import tempfile
import unittest
from pathlib import Path

import server


class WorkerMemoryGuardTests(unittest.TestCase):
    def write_meminfo(self, *, total_mb, available_mb, swap_total_mb, swap_free_mb):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        tmp.write(
            f"MemTotal:       {total_mb * 1024} kB\n"
            f"MemFree:        {available_mb * 512} kB\n"
            f"MemAvailable:   {available_mb * 1024} kB\n"
            f"SwapTotal:      {swap_total_mb * 1024} kB\n"
            f"SwapFree:       {swap_free_mb * 1024} kB\n"
        )
        tmp.close()
        return Path(tmp.name)

    def test_no_swap_adds_extra_headroom_before_new_workers(self):
        path = self.write_meminfo(
            total_mb=11264,
            available_mb=7168,
            swap_total_mb=0,
            swap_free_mb=0,
        )
        try:
            status = server.worker_memory_status(path)
        finally:
            path.unlink(missing_ok=True)

        self.assertFalse(status["swap_healthy"])
        self.assertEqual(server.WORKER_MEMORY_HEADROOM_MB + 512, status["effective_headroom_mb"])
        expected = max(
            0,
            (7168 - status["effective_headroom_mb"]) // server.WORKER_MEMORY_PER_NEW_SLOT_MB,
        )
        self.assertEqual(expected, status["new_worker_capacity"])
        self.assertGreaterEqual(status["new_worker_capacity"], 1)

    def test_critical_memory_blocks_new_worker_capacity(self):
        path = self.write_meminfo(
            total_mb=11264,
            available_mb=700,
            swap_total_mb=0,
            swap_free_mb=0,
        )
        try:
            status = server.worker_memory_status(path)
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual("critical", status["pressure"])
        self.assertEqual(0, status["new_worker_capacity"])
        self.assertFalse(status["healthy_for_new_worker"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
