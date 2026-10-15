"""Adversarial source-only regression for deploy-ops PR collision inventory."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_deployops_pr_owner_collision_20261015 as m

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/zcloud_deployops_pr_owner_collision_20261015.py"
A = "a" * 40
B = "b" * 40
C = "c" * 40


def pr(number, files, *, base=A):
    return {
        "number": number, "state": "open", "head_sha": B if number == 1 else C,
        "base_sha": base, "head_repo": "Zennay/zCloud",
        "base_repo": "Zennay/zCloud", "changed_files": len(files),
        "files": [{"filename": file, "status": "modified"} for file in files],
    }


def snapshot(*pulls):
    return {"repo": "Zennay/zCloud", "main_sha": A, "pulls": list(pulls)}


class PRCollisionInventoryTests(unittest.TestCase):
    def test_exact_collision_only(self):
        result = m.assess(snapshot(
            pr(1, ["docs/a.md", "tests/a.py"]),
            pr(2, ["docs/b.md", "docs/a.md"]),
            pr(3, ["scripts/x.py", "docs/a.md"]),
        ))
        self.assertEqual(1, result["collision_count"])
        self.assertEqual([{"path": "docs/a.md", "prs": [1, 2, 3]}],
                         result["collisions"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual("unverified_snapshot", result["input_provenance"])
        self.assertEqual(6, result["files_scanned"])
        self.assertEqual(4, result["unique_paths_scanned"])

    def test_stale_base_pr_still_owns_files(self):
        result = m.assess(snapshot(pr(1, ["docs/a.md"], base=B),
                                   pr(2, ["docs/a.md"])))
        self.assertEqual([1], result["stale_base_prs"])
        self.assertEqual(1, result["stale_base_count"])
        self.assertEqual(1, result["collision_count"])
        self.assertFalse(result["release_authorized"])

    def test_file_disjoint_is_not_release_approval(self):
        result = m.assess(snapshot(pr(1, ["tests/x.py"]), pr(2, ["docs/y.md"])))
        self.assertEqual([], result["collisions"])
        self.assertEqual(0, result["collision_count"])
        self.assertFalse(result["release_authorized"])

    def test_collision_result_is_deterministic(self):
        s = snapshot(pr(3, ["docs/z.md", "docs/a.md"]), pr(1, ["docs/a.md", "docs/z.md"]))
        self.assertEqual(m.assess(s)["collisions"], [
            {"path": "docs/a.md", "prs": [1, 3]},
            {"path": "docs/z.md", "prs": [1, 3]},
        ])
        self.assertEqual(s, snapshot(pr(3, ["docs/z.md", "docs/a.md"]),
                                    pr(1, ["docs/a.md", "docs/z.md"])))

    def test_missing_or_partial_pr_inventory_fails_closed(self):
        for invalid in ([], "bad", [None], [pr(1, ["a"])] * 1001):
            with self.subTest(value=str(invalid)[:20]):
                with self.assertRaises(m.InvalidInventory):
                    m.assess({"repo": "Zennay/zCloud", "main_sha": A,
                              "pulls": invalid})

    def test_duplicate_pr_number_rejected(self):
        with self.assertRaisesRegex(m.InvalidInventory, "duplicate_pr"):
            m.assess(snapshot(pr(4, ["a"]), pr(4, ["b"])))

    def test_boolean_pr_number_rejected(self):
        s = snapshot(pr(1, ["a"]))
        s["pulls"][0]["number"] = True
        with self.assertRaisesRegex(m.InvalidInventory, "invalid_or_duplicate_pr"):
            m.assess(s)

    def test_changed_file_count_not_truncated_or_boolean(self):
        for count in (0, 2, True, 100, -1, "1"):
            s = snapshot(pr(1, ["docs/a.md"]))
            s["pulls"][0]["changed_files"] = count
            with self.subTest(count=count):
                with self.assertRaisesRegex(m.InvalidInventory, "incomplete_changed_file_inventory"):
                    m.assess(s)

    def test_duplicate_filename_rejected(self):
        s = snapshot(pr(1, ["docs/a.md", "docs/a.md"]))
        with self.assertRaisesRegex(m.InvalidInventory, "unsafe_or_duplicate_file_path"):
            m.assess(s)

    def test_all_four_known_change_statuses_collide(self):
        for status in ("added", "modified", "removed", "renamed"):
            s = snapshot(pr(1, ["docs/a.md"]), pr(2, ["docs/a.md"]))
            s["pulls"][0]["files"][0]["status"] = status
            if status == "renamed":
                s["pulls"][0]["files"][0]["previous_filename"] = "docs/old.md"
            with self.subTest(status=status):
                self.assertEqual(1, m.assess(s)["collision_count"])

    def test_rename_old_path_is_also_owned(self):
        s = snapshot(pr(1, ["docs/new.md"]), pr(2, ["docs/old.md"]))
        s["pulls"][0]["files"][0] = {
            "filename": "docs/new.md", "status": "renamed",
            "previous_filename": "docs/old.md",
        }
        result = m.assess(s)
        self.assertEqual([{"path": "docs/old.md", "prs": [1, 2]}],
                         result["collisions"])
        self.assertEqual(2, result["files_scanned"])
        self.assertEqual(2, result["unique_paths_scanned"])

    def test_rename_ambiguous_previous_path_is_rejected(self):
        for previous in (None, "../old", "docs/new.md", "", "docs/old\\\\secret"):
            s = snapshot(pr(1, ["docs/new.md"]))
            s["pulls"][0]["files"][0] = {
                "filename": "docs/new.md", "status": "renamed",
                "previous_filename": previous,
            }
            with self.subTest(previous=previous), self.assertRaises(m.InvalidInventory):
                m.assess(s)

    def test_rename_without_prior_path_fails_closed(self):
        s = snapshot(pr(1, ["docs/new.md"]))
        s["pulls"][0]["files"][0]["status"] = "renamed"
        with self.assertRaisesRegex(m.InvalidInventory, "invalid_file_shape"):
            m.assess(s)

    def test_unknown_change_status_rejected(self):
        s = snapshot(pr(1, ["docs/a.md"]))
        s["pulls"][0]["files"][0]["status"] = "copied"
        with self.assertRaisesRegex(m.InvalidInventory, "unknown_change_status"):
            m.assess(s)

    def test_path_escape_and_control_denied(self):
        for path in ("../x", "docs/../x", "docs//x", "/etc/passwd",
                     "docs\\secret", "docs/./x", "docs/\nx", "", " x"):
            s = snapshot(pr(1, [path]))
            with self.subTest(path=repr(path)):
                with self.assertRaisesRegex(m.InvalidInventory, "unsafe_or_duplicate_file_path"):
                    m.assess(s)

    def test_file_and_pr_schema_must_be_exact(self):
        for which in ("missing_head", "extra_pr", "missing_file", "extra_file"):
            s = snapshot(pr(1, ["a"]))
            if which == "missing_head":
                s["pulls"][0].pop("head_sha")
            elif which == "extra_pr":
                s["pulls"][0]["token"] = "injected"
            elif which == "missing_file":
                s["pulls"][0]["files"][0].pop("status")
            else:
                s["pulls"][0]["files"][0]["patch"] = "untrusted"
            with self.subTest(which=which):
                with self.assertRaises(m.InvalidInventory):
                    m.assess(s)

    def test_head_base_and_provenance_fail_closed(self):
        mutations = [
            ("repo", "Other/zCloud"),
            ("main_sha", "deadbeef"),
        ]
        for name, value in mutations:
            s = snapshot(pr(1, ["a"]))
            s[name] = value
            with self.subTest(name=name), self.assertRaises(m.InvalidInventory):
                m.assess(s)
        for field, value in (
            ("state", "closed"), ("head_sha", "short"),
            ("base_sha", "invalid"), ("head_repo", "malicious/repo"),
            ("base_repo", "malicious/repo"),
        ):
            s = snapshot(pr(1, ["a"]))
            s["pulls"][0][field] = value
            with self.subTest(field=field), self.assertRaises(m.InvalidInventory):
                m.assess(s)

    def test_json_duplicate_keys_and_nonfinite_rejected(self):
        for raw in (
            '{"repo":"a","repo":"b"}',
            '{"repo":"a","x":NaN}',
            '{"repo":"a","x":Infinity}',
            "{" + "x" * 4_000_001 + "}",
        ):
            with self.subTest(prefix=raw[:20]), self.assertRaises(m.InvalidInventory):
                m.unique_json(raw)

    def test_cli_safe_exit_codes_and_nonmutating_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            for pulls, returncode in (
                ([pr(1, ["docs/a.md"]), pr(2, ["docs/b.md"])], 0),
                ([pr(1, ["docs/a.md"]), pr(2, ["docs/a.md"])], 1),
                ([pr(1, ["docs/a.md"]), pr(1, ["docs/b.md"])], 2),
            ):
                path.write_text(json.dumps(snapshot(*pulls)), encoding="utf-8")
                original = path.read_bytes()
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--snapshot", str(path)],
                    capture_output=True, text=True, timeout=10,
                )
                with self.subTest(returncode=returncode):
                    self.assertEqual(returncode, result.returncode, result.stderr)
                    output = json.loads(result.stdout)
                    self.assertFalse(output["mutation_performed"])
                    self.assertEqual(original, path.read_bytes())

    def test_read_only_cli_contract(self):
        code = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("unlink(", "os.remove(", "git push", "systemctl",
                          "requests.post(", "urlopen(", "HTTPError", "GITHUB_TOKEN"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
