import importlib.util
import json
import os
import sqlite3
import subprocess
import signal
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import project_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zcloud_governed_exec",
    ROOT / "scripts" / "zcloud_governed_exec.py",
)
governed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governed)


class GovernedExecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-governed-exec-")
        self.root = Path(self.tmp.name)
        self.db = self.root / "history.db"
        self.original_contract = runtime.CONTRACT_FILE
        self.contract = self.root / "project-contracts.json"
        self.contract.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "defaults": {"autonomy": {}},
                    "resource_pools": {
                        "protected": {"slots": 2},
                        "heavy": {"slots": 1},
                    },
                    "projects": {
                        "cloud": {
                            "queue_mode": "execution",
                            "lane_profile": "platform",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "control-plane",
                                "pool": "protected",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 512,
                            },
                        },
                        "ftmo": {
                            "queue_mode": "execution",
                            "lane_profile": "research-validation",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "research-heavy",
                                "pool": "heavy",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 1024,
                            },
                        },
                        "haxlab": {
                            "queue_mode": "execution",
                            "lane_profile": "ml-training",
                            "ai_worker_cap": 1,
                            "autonomy": {"mode": "ai_worker", "auto_start": True},
                            "compute": {
                                "class": "ml-heavy",
                                "pool": "heavy",
                                "cpu_soft_cores": 1,
                                "memory_soft_mb": 1024,
                            },
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        runtime.CONTRACT_FILE = self.contract

    def tearDown(self):
        runtime.CONTRACT_FILE = self.original_contract
        self.tmp.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        runtime.init_tables(conn)
        return conn

    def test_busy_heavy_pool_fails_closed_before_child_starts(self):
        conn = self._connect()
        self.assertTrue(runtime.acquire_resource(conn, "ftmo", "holder")["acquired"])
        conn.commit()
        marker = self.root / "should-not-exist.txt"

        rc = governed.run_governed(
            self.db,
            "haxlab",
            "contender",
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"],
        )

        self.assertEqual(75, rc)
        self.assertFalse(marker.exists())
        runtime.release_resource(conn, "ftmo", "holder")
        conn.commit()
        conn.close()

    def test_protected_cloud_continues_while_heavy_pool_is_occupied(self):
        # Critical zCloud/zSSH control-plane admission must not depend on
        # release of the separately capped research/training heavy slot.
        holder = self._connect()
        self.assertTrue(
            runtime.acquire_resource(holder, "ftmo", "long-heavy-owner")["acquired"]
        )
        holder.commit()
        marker = self.root / "protected-control-plane.txt"
        try:
            rc = governed.run_governed(
                self.db,
                "cloud",
                "protected-controller",
                [
                    sys.executable, "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).write_text('active')",
                ],
            )
            self.assertEqual(0, rc)
            self.assertEqual("active", marker.read_text())
            self.assertEqual(
                1, holder.execute(
                    "SELECT COUNT(*) FROM resource_leases "
                    "WHERE pool='heavy' AND owner_id='long-heavy-owner'"
                ).fetchone()[0],
            )
            self.assertEqual(
                0, holder.execute(
                    "SELECT COUNT(*) FROM resource_leases WHERE pool='protected'"
                ).fetchone()[0],
            )
        finally:
            runtime.release_resource(holder, "ftmo", "long-heavy-owner")
            holder.commit()
            holder.close()

    @unittest.skipUnless(sys.platform == "linux", "Linux process groups required")
    @unittest.skipUnless(sys.platform == "linux", "Linux process groups required")
    def test_group_memory_violation_stops_child_and_releases_lease(self):
        # No actual memory stress: force one over-budget aggregate RSS
        # observation against a bounded, sleeping test-only child.
        marker = self.root / "memory-child-pid.txt"
        program = (
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); "
            "time.sleep(8)"
        )
        with mock.patch.object(
            governed, "_group_rss_bytes", return_value=2 * 1024 * 1024 * 1024
        ) as sample:
            rc = governed.run_governed(
                self.db,
                "ftmo",
                "memory-breach-test",
                [sys.executable, "-c", program],
            )
        self.assertEqual(75, rc)
        sample.assert_called()
        with self._connect() as conn:
            self.assertEqual(
                0, conn.execute(
                    "SELECT COUNT(*) FROM resource_leases "
                    "WHERE project_id='ftmo' AND owner_id='memory-breach-test'"
                ).fetchone()[0],
            )
        if marker.exists():
            pid = int(marker.read_text())
            # A process can be awaiting reaping as a zombie, which is not
            # a running research workload.
            deadline = time.monotonic() + 2.0
            running = True
            while time.monotonic() < deadline:
                status = Path(f"/proc/{pid}/stat")
                if not status.exists():
                    running = False
                    break
                if status.read_text().rpartition(")")[2].split()[0] in {"Z", "X"}:
                    running = False
                    break
                time.sleep(0.05)
            self.assertFalse(running, "over-budget child survived release")

    @unittest.skipUnless(sys.platform == "linux", "Linux process groups required")
    def test_lost_lease_generation_stops_child_without_deleting_successor(self):
        # Emulate another writer replacing the lease generation while a
        # bounded child is running. The wrapper must terminate its child
        # at renewal rather than reacquire, and must not delete that new
        # owner's generation during cleanup.
        marker = self.root / "lost-lease-child-pid.txt"
        program = (
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); "
            "time.sleep(20)"
        )
        wrapper_code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "import project_runtime as runtime; "
            f"runtime.CONTRACT_FILE = Path({str(self.contract)!r}); "
            "from scripts import zcloud_governed_exec as governed; "
            "raise SystemExit(governed.run_governed("
            f"Path({str(self.db)!r}), 'ftmo', 'lost-generation-owner', "
            f"[sys.executable, '-c', {program!r}], "
            "lease_seconds=90, renew_seconds=5))"
        )
        wrapper = subprocess.Popen(
            [sys.executable, "-c", wrapper_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child_pid = None
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not marker.exists():
                self.assertIsNone(wrapper.poll(), "governor exited before child startup")
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "test child never started")
            child_pid = int(marker.read_text())
            with self._connect() as conn:
                old = conn.execute(
                    "SELECT acquired_at FROM resource_leases "
                    "WHERE project_id='ftmo' AND owner_id='lost-generation-owner'"
                ).fetchone()
                self.assertIsNotNone(old)
                self.assertNotEqual("2099-01-01T00:00:00+00:00", old["acquired_at"])
                # Deliberately preserve a different generation as another
                # lease holder would. No production DB is ever touched.
                conn.execute(
                    "UPDATE resource_leases SET acquired_at=? "
                    "WHERE project_id='ftmo' AND owner_id='lost-generation-owner'",
                    ("2099-01-01T00:00:00+00:00",),
                )
                conn.commit()
            stdout, stderr = wrapper.communicate(timeout=12)
            self.assertEqual(75, wrapper.returncode, (stdout, stderr))
            self.assertIn("resource_lease_generation_lost", stderr)
            deadline = time.monotonic() + 2.0
            running = True
            while time.monotonic() < deadline:
                status = Path(f"/proc/{child_pid}/stat")
                if not status.exists():
                    running = False
                    break
                if status.read_text().rpartition(")")[2].split()[0] in {"Z", "X"}:
                    running = False
                    break
                time.sleep(0.05)
            self.assertFalse(running, "lost lease left governed child running")
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT acquired_at FROM resource_leases "
                    "WHERE project_id='ftmo' AND owner_id='lost-generation-owner'"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual("2099-01-01T00:00:00+00:00", row["acquired_at"])
        finally:
            if wrapper.poll() is None:
                wrapper.terminate()
                try:
                    wrapper.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    wrapper.kill()
                    wrapper.communicate(timeout=3)
            if child_pid is not None:
                status = Path(f"/proc/{child_pid}/stat")
                try:
                    if status.exists() and status.read_text().rpartition(")")[2].split()[0] not in {"Z", "X"}:
                        os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(sys.platform == "linux", "Linux signals required")
    def test_sigterm_stops_running_child_before_releasing_lease(self):
        # Terminate the test-only wrapper, not any real VPS service.
        # Both it and its subprocess use this test's temporary contract/DB.
        marker = self.root / "signal-child-pid.txt"
        child_code = (
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getpid())); "
            "time.sleep(8)"
        )
        wrapper_code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "import project_runtime as runtime; "
            f"runtime.CONTRACT_FILE = Path({str(self.contract)!r}); "
            "from scripts import zcloud_governed_exec as governed; "
            "raise SystemExit(governed.run_governed("
            f"Path({str(self.db)!r}), 'ftmo', 'signal-test', "
            f"[sys.executable, '-c', {child_code!r}], "
            "lease_seconds=180, renew_seconds=30))"
        )
        wrapper = subprocess.Popen(
            [sys.executable, "-c", wrapper_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        child_pid = None
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not marker.exists():
                self.assertIsNone(wrapper.poll(), "governor exited before child startup")
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "test child never started")
            child_pid = int(marker.read_text())
            # This is a real active lease, not merely a mock admission.
            with self._connect() as conn:
                self.assertEqual(
                    1, conn.execute(
                        "SELECT COUNT(*) FROM resource_leases "
                        "WHERE project_id='ftmo' AND owner_id='signal-test'"
                    ).fetchone()[0],
                )
            wrapper.terminate()
            stdout, stderr = wrapper.communicate(timeout=10)
            self.assertEqual(128 + signal.SIGTERM, wrapper.returncode, (stdout, stderr))
            deadline = time.monotonic() + 2.0
            still_running = True
            while time.monotonic() < deadline:
                status = Path(f"/proc/{child_pid}/stat")
                if not status.exists():
                    still_running = False
                    break
                if status.read_text().rpartition(")")[2].split()[0] in {"Z", "X"}:
                    still_running = False
                    break
                time.sleep(0.05)
            self.assertFalse(still_running, "child survived governor termination")
            with self._connect() as conn:
                self.assertEqual(
                    0, conn.execute(
                        "SELECT COUNT(*) FROM resource_leases "
                        "WHERE project_id='ftmo' AND owner_id='signal-test'"
                    ).fetchone()[0],
                )
        finally:
            if wrapper.poll() is None:
                wrapper.terminate()
                try:
                    wrapper.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    wrapper.kill()
                    wrapper.communicate(timeout=3)
            # This test owns the child PID and may only clean it up if
            # the assertion path above failed before normal cleanup.
            if child_pid is not None:
                status = Path(f"/proc/{child_pid}/stat")
                try:
                    if status.exists() and status.read_text().rpartition(")")[2].split()[0] not in {"Z", "X"}:
                        os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_parent_exit_stops_same_group_descendant_before_lease_release(self):
        # The direct workload exits immediately, leaving a bounded test-only
        # grandchild asleep in its *same* session. It must not outlive the
        # lease release, even though the wrapper's direct child has exited.
        marker = self.root / "subprocess-pid.txt"
        command = (
            "import subprocess,sys; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import time;time.sleep(8)']); "
            f"Path({str(marker)!r}).write_text(str(child.pid))"
        )
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "parent-exit-tree-cleanup",
            [sys.executable, "-c", command],
        )
        self.assertEqual(0, rc)
        pid = int(marker.read_text())
        # A terminated grandchild may remain briefly as a zombie until its
        # init process reaps it. Zombies consume no running workload.
        deadline = time.monotonic() + 2.0
        running = True
        while time.monotonic() < deadline:
            status = Path(f"/proc/{pid}/stat")
            if not status.exists():
                running = False
                break
            if status.read_text().rpartition(")")[2].split()[0] in {"Z", "X"}:
                running = False
                break
            time.sleep(0.05)
        self.assertFalse(running, "workload descendant survived lease release")
        conn = self._connect()
        try:
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM resource_leases "
                    "WHERE project_id='ftmo' AND owner_id='parent-exit-tree-cleanup'"
                ).fetchone()[0],
            )
        finally:
            conn.close()

    def test_duplicate_owner_rejects_child_without_touching_active_lease(self):
        # The same owner key must not renew an existing generation while
        # accidentally starting a second process outside the one-slot pool.
        holder = self._connect()
        original = runtime.acquire_resource(holder, "ftmo", "stable-owner")
        self.assertTrue(original["acquired"])
        holder.commit()
        marker = self.root / "duplicate-owner-must-not-start.txt"
        try:
            rc = governed.run_governed(
                self.db,
                "ftmo",
                "stable-owner",
                [
                    sys.executable, "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')",
                ],
            )
            self.assertEqual(75, rc)
            self.assertFalse(marker.exists())
            row = holder.execute(
                "SELECT acquired_at,metadata_json FROM resource_leases "
                "WHERE project_id='ftmo' AND owner_id='stable-owner'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(
                original["lease"]["acquired_at"], row["acquired_at"]
            )
        finally:
            runtime.release_resource(holder, "ftmo", "stable-owner")
            holder.commit()
            holder.close()

    def test_successful_child_inherits_identity_and_releases_lease(self):
        marker = self.root / "identity.json"
        code = (
            "import json,os; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(json.dumps({{'project':os.environ['ZCLOUD_RESOURCE_PROJECT'],"
            "'owner':os.environ['ZCLOUD_RESOURCE_OWNER']}))"
        )
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-success",
            [sys.executable, "-c", code],
        )

        self.assertEqual(0, rc)
        self.assertEqual(
            {"project": "ftmo", "owner": "run-success"},
            json.loads(marker.read_text()),
        )
        conn = self._connect()
        self.assertEqual(0, runtime.resource_status(conn)["pools"]["heavy"]["used"])
        conn.close()

    def test_failed_child_preserves_exit_code_and_releases_lease(self):
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-failure",
            [sys.executable, "-c", "raise SystemExit(7)"],
        )

        self.assertEqual(7, rc)
        conn = self._connect()
        self.assertEqual([], runtime.resource_status(conn)["leases"])
        conn.close()

    @unittest.skipUnless(
        hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"),
        "Linux affinity APIs required",
    )
    def test_child_receives_contract_cpu_and_memory_bounds(self):
        marker = self.root / "limits.json"
        code = (
            "import json,os,resource; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(json.dumps({{'cpus':len(os.sched_getaffinity(0)),"
            "'memory':resource.getrlimit(resource.RLIMIT_AS)[0]}))"
        )
        rc = governed.run_governed(
            self.db,
            "ftmo",
            "run-limits",
            [sys.executable, "-c", code],
        )

        self.assertEqual(0, rc)
        limits = json.loads(marker.read_text())
        self.assertEqual(1, limits["cpus"])
        self.assertEqual(1024 * 1024 * 1024, limits["memory"])


    def test_dedicated_heavy_leaf_memory_does_not_hide_parent_pressure(self):
        mib = 1024 * 1024
        values = {
            "/proc/self/cgroup": "0::/user.slice/heavy.service\n",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/user.slice/memory.max": "max",
            "/sys/fs/cgroup/user.slice/heavy.service/memory.max": str(4608 * mib),
            "/sys/fs/cgroup/user.slice/heavy.service/memory.current": str(64 * mib),
        }

        class FakePath:
            def __init__(self, value):
                self.value = str(value)

            def __truediv__(self, other):
                return FakePath(self.value.rstrip("/") + "/" + str(other))

            def exists(self):
                return self.value in values

            def read_text(self):
                return values[self.value]

        with mock.patch.object(governed, "Path", FakePath):
            self.assertEqual(
                (4608, 4608, 4608),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )
            self.assertEqual(
                (4608, 4544, None),
                governed._effective_cgroup_memory_limits_mb(11000),
            )
            # A real cgroup-v2 mount root may omit memory.max entirely.
            del values["/sys/fs/cgroup/memory.max"]
            self.assertEqual(
                (4608, 4608, 4608),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )
            values["/sys/fs/cgroup/user.slice/memory.max"] = str(2048 * mib)
            values["/sys/fs/cgroup/user.slice/memory.current"] = "0"
            self.assertEqual(
                (2048, 2048, 4608),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )
            values["/sys/fs/cgroup/user.slice/memory.max"] = str(6000 * mib)
            values["/sys/fs/cgroup/user.slice/heavy.service/memory.max"] = str(3072 * mib)
            self.assertEqual(
                (3072, 3072, 3072),
                governed._effective_cgroup_memory_limits_mb(
                    11000, dedicated_heavy_leaf=True
                ),
            )

    def _expired_lease_connection(self, metadata):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        runtime.init_tables(conn)
        conn.execute(
            "INSERT INTO resource_leases ("
            "project_id,owner_id,pool,workload_class,cpu_soft_cores,"
            "memory_soft_mb,acquired_at,lease_until,metadata_json"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "ftmo", "crashed-worker", "heavy", "research-heavy", 1.0,
                1024, "2020-01-01T00:00:00+00:00",
                "2020-01-01T00:01:00+00:00", json.dumps(metadata),
            ),
        )
        conn.commit()
        return conn

    def test_expired_foreign_heavy_lease_never_uses_local_pid_probe(self):
        conn = self._expired_lease_connection({
            "pid": 43210, "host_boot_id": "different-vps-boot",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_process_group_has_live_members") as probe:
                    self.assertTrue(
                        governed._expired_heavy_group_active(
                            conn, live_authority=True
                        )
                    )
                    probe.assert_not_called()
        finally:
            conn.close()

    def test_expired_heavy_lease_detects_detached_child_in_own_service(self):
        conn = self._expired_lease_connection({
            "executor": "zcloud-governed-exec",
            "host_boot_id": "current-boot",
            "pid": 43210,
            "service_cgroup": "/user.slice/research.service",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_process_group_has_live_members", return_value=False):
                    with mock.patch.object(governed, "_live_service_members", return_value=True):
                        self.assertTrue(
                            governed._expired_heavy_group_active(
                                conn, live_authority=True
                            )
                        )
        finally:
            conn.close()

    def test_expired_governed_prelaunch_lease_checks_cgroup_without_pid(self):
        conn = self._expired_lease_connection({
            "executor": "zcloud-governed-exec",
            "host_boot_id": "current-boot",
            "service_cgroup": "/user.slice/research.service",
        })
        try:
            with mock.patch.object(governed, "_local_boot_identity", return_value="current-boot"):
                with mock.patch.object(governed, "_live_service_members", return_value=True):
                    self.assertTrue(
                        governed._expired_heavy_group_active(
                            conn, live_authority=True
                        )
                    )
        finally:
            conn.close()


    def test_bounded_heavy_wait_times_out_without_starting_child(self):
        holder = self._connect()
        self.assertTrue(
            runtime.acquire_resource(holder, "ftmo", "persistent-holder")["acquired"]
        )
        holder.commit()
        marker = self.root / "must-not-start.txt"
        try:
            started = time.monotonic()
            rc = governed.run_governed(
                self.db,
                "haxlab",
                "timed-out-contender",
                [
                    sys.executable, "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')",
                ],
                wait_seconds=1,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(75, rc)
            self.assertGreaterEqual(elapsed, 0.75)
            self.assertLess(elapsed, 4.0)
            self.assertFalse(marker.exists())
            self.assertEqual(
                1,
                holder.execute(
                    "SELECT COUNT(*) FROM resource_leases "
                    "WHERE pool='heavy' AND owner_id='persistent-holder'"
                ).fetchone()[0],
            )
        finally:
            runtime.release_resource(holder, "ftmo", "persistent-holder")
            holder.commit()
            holder.close()

    def test_waiting_heavy_contender_starts_only_after_holder_releases(self):
        # Exercise two independent SQLite users, not a mocked capacity
        # calculator. The wrapper must rollback while waiting and must
        # launch the child only after the original heavy lease is gone.
        holder = self._connect()
        self.assertTrue(
            runtime.acquire_resource(holder, "ftmo", "first-owner")["acquired"]
        )
        holder.commit()
        output = self.root / "waiter-started.txt"
        # The contender uses its own interpreter but the SAME temporary
        # contract override as the holder; runtime.CONTRACT_FILE is a
        # process-local Python variable, not an inherited environment key.
        child_code = (
            f"from pathlib import Path; Path({str(output)!r}).write_text('started')"
        )
        contender_code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "import project_runtime as runtime; "
            f"runtime.CONTRACT_FILE = Path({str(self.contract)!r}); "
            "from scripts import zcloud_governed_exec as governed; "
            "raise SystemExit(governed.run_governed("
            f"Path({str(self.db)!r}), 'haxlab', 'second-owner', "
            f"[sys.executable, '-c', {child_code!r}], "
            "lease_seconds=180, renew_seconds=30, wait_seconds=5))"
        )
        contender = subprocess.Popen(
            [sys.executable, "-c", contender_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            # The contender must have reached its wait loop before the
            # existing holder releases the one-slot heavy pool.
            time.sleep(0.8)
            self.assertIsNone(contender.poll())
            self.assertFalse(output.exists())
            runtime.release_resource(holder, "ftmo", "first-owner")
            holder.commit()
            stdout, stderr = contender.communicate(timeout=12)
            self.assertEqual(0, contender.returncode, (stdout, stderr))
            self.assertEqual("started", output.read_text())
            check = self._connect()
            try:
                self.assertEqual(
                    0, runtime.resource_status(check)["pools"]["heavy"]["used"]
                )
            finally:
                check.close()
        finally:
            if contender.poll() is None:
                contender.terminate()
                try:
                    contender.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    contender.kill()
                    contender.communicate(timeout=3)
            holder.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
