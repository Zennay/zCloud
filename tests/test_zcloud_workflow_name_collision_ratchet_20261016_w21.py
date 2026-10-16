"""Offline regressions for new GitHub Actions workflow-name collisions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_workflow_name_collision_ratchet_20261016_w21.py"
spec = importlib.util.spec_from_file_location("workflow_name_collision_ratchet", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class NameCollisionRatchetTests(unittest.TestCase):
    def test_new_collision_is_denied(self):
        base = {"a.yml": "Build"}
        actual = guard.judge(base, {"a.yml": "Build", "b.yml": "Build"})
        self.assertEqual(actual["status"], "deny")
        self.assertEqual(actual["added_collision_count"], 1)
        self.assertFalse(actual["merge_authorized"])
        self.assertFalse(actual["production_mutation_authorized"])

    def test_legacy_collision_is_not_new_debt(self):
        base = {"a.yml": "Build", "b.yml": "Build"}
        self.assertEqual(guard.judge(base, dict(base))["status"], "pass")

    def test_remove_old_collision(self):
        base = {"a": "Build", "b": "Build"}
        self.assertEqual(guard.judge(base, {"a": "Build", "b": "Other"})["status"], "pass")

    def test_rename_and_changed_name_create_collision(self):
        base = {"a.yml": "Build", "b.yml": "Test"}
        self.assertEqual(guard.judge(base, {"a.yml": "Build", "b.yml": "Build"})["status"], "deny")
        self.assertEqual(guard.judge(base, {"a.yml": "Build", "c.yml": "Build"})["status"], "deny")

    def test_unique_new_name_is_safe_as_source_only(self):
        actual = guard.judge({"a": "A"}, {"a": "A", "b": "B"})
        self.assertEqual(actual["status"], "pass")
        self.assertFalse(actual["merge_authorized"])

    def test_yaml_quoted_names(self):
        path = ".github/workflows/a.yml"
        self.assertEqual(guard.workflow_name(path, b'name: "Build: fast" # comment\n'), "Build: fast")
        self.assertEqual(guard.workflow_name(path, b"name: 'Build: fast'\n"), "Build: fast")
        self.assertEqual(guard.workflow_name(path, b"name: 'CI''s check'\n"), "CI's check")

    def test_missing_name_uses_filename(self):
        self.assertEqual(guard.workflow_name(".github/workflows/a.yml", b"on: push\n"), "a.yml")

    def test_ambiguous_names_denied(self):
        invalid = (b"name: A\nname: B\n", b"name: \xff\n",
                   b"name: [foo]\n", b"name: ${{ github.ref }}\n",
                   b'name: "unterminated\n', b"name: Build: fast\n")
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(guard.InvalidInventory):
                guard.workflow_name(".github/workflows/a.yml", source)

    def test_bad_revision_denied(self):
        with self.assertRaises(guard.InvalidInventory):
            guard.read_names("main", cwd=".")

    def test_real_git_graph_deny_and_repair(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: Stable CI\non: push\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            (folder / "b.yml").write_text("name: Stable CI\non: workflow_dispatch\n")
            git("add", ".")
            git("commit", "-qm", "collision")
            colliding = git("rev-parse", "HEAD")
            self.assertEqual(
                guard.judge(guard.read_names(base, cwd=root),
                            guard.read_names(colliding, cwd=root))["status"], "deny")
            (folder / "b.yml").write_text("name: Separate check\n")
            git("add", ".")
            git("commit", "-qm", "fixed")
            repaired = git("rev-parse", "HEAD")
            self.assertEqual(
                guard.judge(guard.read_names(base, cwd=root),
                            guard.read_names(repaired, cwd=root))["status"], "pass")

    def test_symlink_workflow_refused(self):
        with tempfile.TemporaryDirectory() as root:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()
            git("init", "-q")
            git("config", "user.name", "CI Test")
            git("config", "user.email", "test@example.com")
            folder = Path(root) / ".github/workflows"
            folder.mkdir(parents=True)
            (folder / "a.yml").write_text("name: Build\n")
            (folder / "b.yml").symlink_to("a.yml")
            git("add", ".")
            git("commit", "-qm", "symlink")
            with self.assertRaises(guard.InvalidInventory):
                guard.read_names(git("rev-parse", "HEAD"), cwd=root)


if __name__ == "__main__":
    unittest.main()
