"""Adversarial source-only regression for deploy-ops PR collision inventory."""
from __future__ import annotations

import copy
import json
import os
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

    def test_transitive_conflict_clusters_not_independent_merge_permission(self):
        # PR 1 conflicts with 2, and 2 with 3, despite no shared path 1↔3.
        s = snapshot(
            pr(3, ["docs/b.md"]),
            pr(1, ["docs/a.md"]),
            pr(4, ["docs/unique.md"]),
            pr(2, ["docs/a.md", "docs/b.md"]),
            pr(6, ["docs/z.md"]),
            pr(5, ["docs/z.md"]),
        )
        result = m.assess(s)
        self.assertEqual(5, result["conflicted_pr_count"])
        self.assertEqual(2, result["conflict_group_count"])
        self.assertEqual([
            {"pr_count": 3, "prs": [1, 2, 3], "pr_ids_truncated": False},
            {"pr_count": 2, "prs": [5, 6], "pr_ids_truncated": False},
        ], result["conflict_groups"])
        self.assertEqual(1, result["uncontested_pr_count"])
        self.assertEqual([4], result["uncontested_pr_ids"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_file_directory_cross_pr_collision_without_exact_same_path(self):
        result = m.assess(snapshot(
            pr(1, ["docs/ops"]),
            pr(2, ["docs/ops/report.md"]),
            pr(3, ["docs/unrelated.txt"]),
        ))
        self.assertEqual(0, result["collision_count"])
        self.assertEqual(1, result["hierarchy_collision_count"])
        self.assertEqual([{
            "ancestor": "docs/ops", "descendant": "docs/ops/report.md",
            "pr_count": 2, "prs": [1, 2], "pr_ids_truncated": False,
        }], result["hierarchy_collisions"])
        self.assertEqual([{"pr_count": 2, "prs": [1, 2],
                           "pr_ids_truncated": False}], result["conflict_groups"])
        self.assertEqual([3], result["uncontested_pr_ids"])

    def test_filename_prefix_without_slash_is_not_false_conflict(self):
        result = m.assess(snapshot(
            pr(1, ["docs/ops"]),
            pr(2, ["docs/ops-old/report.md"]),
        ))
        self.assertEqual(0, result["hierarchy_collision_count"])
        self.assertEqual([1, 2], result["uncontested_pr_ids"])

    def test_file_to_folder_replacement_within_one_pr_is_not_cross_pr(self):
        result = m.assess(snapshot(pr(1, [
            "docs/legacy", "docs/legacy/report.md",
        ])))
        self.assertEqual(0, result["hierarchy_collision_count"])
        self.assertEqual([], result["conflict_groups"])
        self.assertEqual([1], result["uncontested_pr_ids"])

    def test_file_hierarchy_edges_extend_exact_path_conflict_group(self):
        result = m.assess(snapshot(
            pr(1, ["docs/tree"]),
            pr(2, ["docs/tree", "docs/tree/a.md"]),
            pr(3, ["docs/tree/a.md"]),
        ))
        self.assertEqual(2, result["collision_count"])
        self.assertEqual(1, result["hierarchy_collision_count"])
        self.assertEqual([{"pr_count": 3, "prs": [1, 2, 3],
                           "pr_ids_truncated": False}], result["conflict_groups"])

    def test_hierarchy_only_collision_returns_cli_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / "snapshot.json"
            snapshot_path.write_text(json.dumps(snapshot(
                pr(1, ["docs/ops"]), pr(2, ["docs/ops/data.json"]))),
                encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--snapshot", str(snapshot_path)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(1, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(0, payload["collision_count"])
            self.assertEqual(1, payload["hierarchy_collision_count"])
            self.assertFalse(payload["release_authorized"])

    def test_uncontested_does_not_mean_reviewed_or_mergeable(self):
        s = snapshot(pr(10, ["tests/a.py"]), pr(20, ["tests/b.py"]))
        result = m.assess(s)
        self.assertEqual(0, result["conflicted_pr_count"])
        self.assertEqual(0, result["conflict_group_count"])
        self.assertEqual([], result["conflict_groups"])
        self.assertEqual([10, 20], result["uncontested_pr_ids"])
        self.assertEqual("unverified_snapshot", result["input_provenance"])
        self.assertFalse(result["release_authorized"])

    def test_stale_base_pr_remains_in_conflicting_group(self):
        result = m.assess(snapshot(
            pr(11, ["docs/a.md"], base=B),
            pr(12, ["docs/a.md"]),
        ))
        self.assertEqual([11], result["stale_base_prs"])
        self.assertEqual([{"pr_count": 2, "prs": [11, 12],
                           "pr_ids_truncated": False}],
                         result["conflict_groups"])

    def test_conflict_groups_bounded_without_hiding_total(self):
        s = snapshot(*[pr(i, ["docs/shared.md"]) for i in range(1, 103)])
        result = m.assess(s)
        self.assertEqual(102, result["conflicted_pr_count"])
        self.assertEqual(1, result["conflict_group_count"])
        self.assertEqual(102, result["conflict_groups"][0]["pr_count"])
        self.assertEqual(50, len(result["conflict_groups"][0]["prs"]))
        self.assertTrue(result["conflict_groups"][0]["pr_ids_truncated"])
        self.assertEqual(0, result["uncontested_pr_count"])

    def test_uncontested_pr_list_is_bounded_and_count_honest(self):
        s = snapshot(*[pr(i, [f"docs/{i}.md"]) for i in range(1, 102)])
        result = m.assess(s)
        self.assertEqual(101, result["uncontested_pr_count"])
        self.assertEqual(80, len(result["uncontested_pr_ids"]))
        self.assertTrue(result["uncontested_pr_ids_truncated"])
        self.assertEqual(0, result["collision_count"])

    def test_snapshot_fingerprint_is_stable_across_dictionary_key_order(self):
        record = snapshot(pr(1, ["docs/a.md"]), pr(2, ["docs/b.md"]))
        baseline = m.assess(record)["snapshot_sha256"]
        self.assertRegex(baseline, r"^[0-9a-f]{64}$")
        reordered = {
            "pulls": [{key: value for key, value in reversed(list(item.items()))}
                      for item in record["pulls"]],
            "main_sha": record["main_sha"],
            "repo": record["repo"],
        }
        self.assertEqual(baseline, m.assess(reordered)["snapshot_sha256"])
        changed = snapshot(pr(1, ["docs/a.md"]), pr(2, ["docs/c.md"]))
        self.assertNotEqual(baseline, m.assess(changed)["snapshot_sha256"])
        self.assertFalse(m.assess(record)["release_authorized"])

    def test_snapshot_fingerprint_is_sensitivity_to_main_revision(self):
        record = snapshot(pr(1, ["docs/a.md"]))
        baseline = m.assess(record)["snapshot_sha256"]
        record["main_sha"] = "f" * 40
        self.assertNotEqual(baseline, m.assess(record)["snapshot_sha256"])

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

    def test_bounded_input_rejects_symlink_fifo_and_oversize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "snapshot.json"
            real.write_text(json.dumps(snapshot(pr(1, ["docs/a.md"]))),
                            encoding="utf-8")
            self.assertEqual(real.read_text(encoding="utf-8"), m.read_snapshot(real))
            alias = root / "alias.json"
            alias.symlink_to(real)
            with self.assertRaises(m.InvalidInventory):
                m.read_snapshot(alias)
            if hasattr(os, "mkfifo"):
                fifo = root / "pipe.json"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(m.InvalidInventory, "unsafe_or_oversized_snapshot"):
                    m.read_snapshot(fifo)
            huge = root / "oversized.json"
            huge.write_bytes(b"x" * (m.MAX_SNAPSHOT_BYTES + 1))
            with self.assertRaisesRegex(m.InvalidInventory, "unsafe_or_oversized_snapshot"):
                m.read_snapshot(huge)
            invalid = root / "invalid-utf8.json"
            invalid.write_bytes(bytes([0xff]))
            with self.assertRaisesRegex(m.InvalidInventory, "snapshot_read_failed"):
                m.read_snapshot(invalid)

    def test_snapshot_rejects_symlinked_ancestor_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            source = real / "inventory.json"
            source.write_text(json.dumps(snapshot(pr(1, ["docs/a.md"]))),
                              encoding="utf-8")
            symlink = root / "alias"
            symlink.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(m.InvalidInventory, "snapshot_read_failed"):
                m.read_snapshot(symlink / "inventory.json")
            self.assertEqual(source.read_text(encoding="utf-8"),
                             m.read_snapshot(source))
            # Nested ancestors must be protected individually.
            nested = root / "nested"
            nested.mkdir()
            (nested / "second").symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(m.InvalidInventory, "snapshot_read_failed"):
                m.read_snapshot(nested / "second" / "inventory.json")

    def test_snapshot_rejects_parent_traversal_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "sub"
            sub.mkdir()
            source = root / "inventory.json"
            source.write_text(json.dumps(snapshot(pr(1, ["docs/a.md"]))),
                              encoding="utf-8")
            before = source.read_bytes()
            with self.assertRaisesRegex(m.InvalidInventory, "invalid_snapshot_path"):
                m.read_snapshot(sub / ".." / "inventory.json")
            self.assertEqual(before, source.read_bytes())

    def test_snapshot_directory_chain_is_readonly_and_supports_nested_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "one" / "two"
            nested.mkdir(parents=True)
            source = nested / "inventory.json"
            source.write_text(json.dumps(snapshot(pr(1, ["docs/a.md"]))),
                              encoding="utf-8")
            before = source.read_bytes()
            self.assertEqual(source.read_text(encoding="utf-8"),
                             m.read_snapshot(source))
            self.assertEqual(before, source.read_bytes())

    def test_bounded_input_cli_refuses_symlink_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "actual.json"
            original.write_text(json.dumps(snapshot(pr(1, ["docs/a.md"]))),
                                encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--snapshot", str(link)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(2, result.returncode)
            self.assertFalse(json.loads(result.stdout)["mutation_performed"])

    def test_read_only_cli_contract(self):
        code = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("unlink(", "os.remove(", "git push", "systemctl",
                          "requests.post(", "urlopen(", "HTTPError", "GITHUB_TOKEN"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
