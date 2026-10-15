"""Synthetic snapshot/CLI adversarial coverage; never touches live zCloud state."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_lkg_snapshot_integrity_probe_20261015_w2 as probe


class LkgSnapshotProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lkg-probe-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.snap_id = "20261015T142500Z-deadbeef"
        self.snapshots = self.state / "snapshots"
        self.snap = self.snapshots / self.snap_id
        self.files = self.snap / "files"
        self.files.mkdir(parents=True)
        self.text = b"safe rollback source\n"
        (self.files / "server.py").write_bytes(self.text)
        self.hashes = {"server.py": hashlib.sha256(self.text).hexdigest()}
        self.save()

    def save(self):
        (self.state / "last-known-good.json").write_text(
            json.dumps({"snapshot_id": self.snap_id, "manifest": "/never-read/manifest.json"}),
            encoding="utf-8",
        )
        (self.snap / "manifest.json").write_text(
            json.dumps({
                "format_version": 1,
                "snapshot_id": self.snap_id,
                "hashes": self.hashes,
                "managed_paths": sorted(probe.MANAGED_ROOTS),
                "persistent_paths_never_restored": sorted(probe.PERSISTENT),
                "present_paths": ["server.py"],
                "config_hashes": {
                    name: digest for name, digest in self.hashes.items()
                    if name in {"projects.json", "project-layout.json", "resource-policy.json", "project-contracts.json"}
                },
            }),
            encoding="utf-8",
        )

    def test_typed_managed_roots_reject_file_directory_confusion(self):
        for forged_name in ("server.py/nested.txt", "public", "scripts", "project-layout.json/child"):
            with self.subTest(forged_name=forged_name):
                self.hashes = {forged_name: hashlib.sha256(self.text).hexdigest()}
                self.save()
                with self.assertRaisesRegex(probe.Denied, "invalid_hash_manifest"):
                    probe.inspect(self.state)

    def test_real_root_type_drift_denied_even_with_matching_manifest(self):
        (self.files / "public").write_text("not a directory", encoding="utf-8")
        self.save()
        manifest = self.snap / "manifest.json"
        obj = json.loads(manifest.read_text(encoding="utf-8"))
        obj["present_paths"] = ["server.py", "public"]
        manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "unexpected_snapshot_file"):
            probe.inspect(self.state)

    def test_forged_present_paths_is_denied_even_when_hashes_are_valid(self):
        manifest = self.snap / "manifest.json"
        for changed in (
            [],
            ["projects.json"],
            ["server.py", "server.py"],
            ["server.py", "history.db"],
            ["server.py", "scripts"],
        ):
            with self.subTest(changed=changed):
                self.save()
                obj = json.loads(manifest.read_text(encoding="utf-8"))
                obj["present_paths"] = changed
                manifest.write_text(json.dumps(obj), encoding="utf-8")
                with self.assertRaises(probe.Denied):
                    probe.inspect(self.state)

    def test_mismatched_config_hash_is_denied(self):
        blob = b'{"config":true}'
        (self.files / "projects.json").write_bytes(blob)
        self.hashes["projects.json"] = hashlib.sha256(blob).hexdigest()
        self.save()
        manifest = self.snap / "manifest.json"
        obj = json.loads(manifest.read_text(encoding="utf-8"))
        obj["present_paths"].append("projects.json")
        obj["config_hashes"]["projects.json"] = "0" * 64
        manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "config_hash_mismatch"):
            probe.inspect(self.state)

    def test_persistent_root_omission_and_alias_are_denied(self):
        manifest = self.snap / "manifest.json"
        for values in (
            [],
            ["history.db"],
            sorted(probe.PERSISTENT) + ["history.db"],
            sorted(probe.PERSISTENT) + ["extra"],
        ):
            with self.subTest(values=values):
                self.save()
                obj = json.loads(manifest.read_text(encoding="utf-8"))
                obj["persistent_paths_never_restored"] = values
                manifest.write_text(json.dumps(obj), encoding="utf-8")
                with self.assertRaises(probe.Denied):
                    probe.inspect(self.state)

    def test_changed_pointer_or_manifest_during_scan_is_denied(self):
        from unittest.mock import patch
        original = probe._json
        for drift_name, expected_reason in (
            ("last-known-good.json", "pointer_changed_during_scan"),
            ("manifest.json", "manifest_changed_during_scan"),
        ):
            with self.subTest(drift=drift_name):
                counter = [0]
                def changing(path):
                    result = original(path)
                    if path.name == drift_name:
                        counter[0] += 1
                        if counter[0] == 2:
                            return {**result, "scan_drift": True}
                    return result
                with patch.object(probe, "_json", side_effect=changing):
                    with self.assertRaisesRegex(probe.Denied, expected_reason):
                        probe.inspect(self.state)

    def test_empty_managed_directory_is_allowed_if_manifest_declares_it(self):
        (self.files / "scripts").mkdir()
        self.save()
        manifest = self.snap / "manifest.json"
        obj = json.loads(manifest.read_text(encoding="utf-8"))
        obj["present_paths"].append("scripts")
        manifest.write_text(json.dumps(obj), encoding="utf-8")
        result = probe.inspect(self.state)
        self.assertEqual(1, result["file_count"])
        self.assertTrue(result["ok"])

    def test_intermediate_directory_swap_to_external_symlink_fails_closed(self):
        from unittest.mock import patch

        folder = self.files / "scripts"
        folder.mkdir()
        (folder / "task.py").write_bytes(self.text)
        self.hashes["scripts/task.py"] = hashlib.sha256(self.text).hexdigest()
        self.save()
        manifest = self.snap / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["present_paths"].append("scripts")
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        external = Path(self.tmp.name) / "outside"
        external.mkdir()
        (external / "task.py").write_bytes(self.text)
        original = probe._open_snapshot_regular
        switched = [False]

        def switch_parent(root, name):
            if name == "scripts/task.py" and not switched[0]:
                switched[0] = True
                folder.rename(self.files / "scripts-original")
                folder.symlink_to(external, target_is_directory=True)
            return original(root, name)

        with patch.object(probe, "_open_snapshot_regular", side_effect=switch_parent):
            with self.assertRaisesRegex(probe.Denied, "unreadable_path"):
                probe.inspect(self.state)
        self.assertTrue(switched[0])
        self.assertEqual(self.text, (external / "task.py").read_bytes())

    def test_nonblocking_regular_open_rejects_fifo_substitution(self):
        import os
        from unittest.mock import patch

        pointer = self.state / "last-known-good.json"
        original_regular = probe._regular
        swapped = [False]

        def swap_to_fifo(path):
            result = original_regular(path)
            if path == pointer and not swapped[0]:
                swapped[0] = True
                path.unlink()
                os.mkfifo(path)
            return result

        with patch.object(probe, "_regular", side_effect=swap_to_fifo):
            with self.assertRaisesRegex(probe.Denied, "nonregular_file"):
                probe.inspect(self.state)
        self.assertTrue(swapped[0])

    def test_inflight_file_mtime_change_fails_closed_even_if_bytes_match(self):
        import os
        from unittest.mock import patch

        original = probe._open_snapshot_regular
        target = self.files / "server.py"

        class ChangedDuringRead:
            def __init__(self, handle):
                self.handle = handle
                self.touched = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def fileno(self):
                return self.handle.fileno()

            def read(self, length):
                data = self.handle.read(length)
                if data and not self.touched:
                    self.touched = True
                    before = target.stat()
                    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
                return data

        def instrument(root, name):
            file = original(root, name)
            return ChangedDuringRead(file) if name == "server.py" else file

        with patch.object(probe, "_open_snapshot_regular", side_effect=instrument):
            with self.assertRaisesRegex(probe.Denied, "source_changed_during_scan"):
                probe.inspect(self.state)

    def test_pathname_swap_after_open_fails_closed(self):
        from unittest.mock import patch

        target = self.files / "server.py"
        original = probe._open_snapshot_regular

        class ReplacedPath:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self.handle.__enter__()
            def __exit__(self, *args):
                result = self.handle.__exit__(*args)
                target.unlink()
                target.write_bytes(b"new inode same path")
                return result

        def substitute(root, name):
            handle = original(root, name)
            return ReplacedPath(handle) if name == "server.py" else handle

        with patch.object(probe, "_open_snapshot_regular", side_effect=substitute):
            with self.assertRaisesRegex(probe.Denied, "source_changed_during_scan"):
                probe.inspect(self.state)

    def test_real_recovery_capture_format_is_accepted_without_live_mutation(self):
        from unittest.mock import patch
        from scripts import zcloud_recovery as production_capture

        root = Path(self.tmp.name) / "actual-capture-root"
        state = Path(self.tmp.name) / "actual-capture-state"
        root.mkdir()
        (root / "server.py").write_text("real managed file\\n", encoding="utf-8")
        (root / "public").mkdir()
        (root / "public" / "app.js").write_text("/* release */\\n", encoding="utf-8")
        with patch.object(
            production_capture, "git_meta", return_value={"head": "deadbeef"}
        ):
            manifest = production_capture.capture(
                root, state, "synthetic offline integration",
                require_health=False,
                service_state={"active_state": "active"},
                browser_state={"active_state": "active"},
            )
        result = probe.inspect(state)
        self.assertTrue(result["ok"])
        self.assertEqual(manifest["snapshot_id"], result["snapshot_id"])
        self.assertEqual(2, result["file_count"])
        self.assertFalse(result["authorizes_rollback"])

    def test_valid_snapshot_produces_proof_but_no_authority(self):
        result = probe.inspect(self.state)
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["file_count"])
        self.assertEqual(len(self.text), result["verified_bytes"])
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["authorizes_rollback"])
        self.assertFalse(result["authorizes_deploy"])

    def test_fails_closed_when_manifest_file_is_modified(self):
        (self.files / "server.py").write_bytes(b"tampered")
        with self.assertRaisesRegex(probe.Denied, "hash_mismatch"):
            probe.inspect(self.state)

    def test_fails_closed_when_extra_file_is_not_in_manifest(self):
        (self.files / "unexpected.py").write_text("secret", encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "unexpected_snapshot_file"):
            probe.inspect(self.state)

    def test_fails_closed_when_manifest_file_is_missing(self):
        (self.files / "server.py").unlink()
        with self.assertRaisesRegex(probe.Denied, "missing_snapshot_files"):
            probe.inspect(self.state)

    def test_fails_closed_when_file_is_replaced_by_symlink(self):
        target = Path(self.tmp.name) / "external-secret"
        target.write_text("outside", encoding="utf-8")
        (self.files / "server.py").unlink()
        (self.files / "server.py").symlink_to(target)
        with self.assertRaisesRegex(probe.Denied, "symlink_in_snapshot"):
            probe.inspect(self.state)
        self.assertEqual("outside", target.read_text(encoding="utf-8"))

    def test_fails_closed_when_directory_is_symlink_even_with_no_hash(self):
        target = Path(self.tmp.name) / "external-dir"
        target.mkdir()
        (self.files / "scripts").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(probe.Denied, "symlink_in_snapshot"):
            probe.inspect(self.state)

    def test_fails_closed_when_pointer_is_symlink(self):
        pointer = self.state / "last-known-good.json"
        real = self.state / "pointer-backup.json"
        pointer.rename(real)
        pointer.symlink_to(real)
        with self.assertRaisesRegex(probe.Denied, "nonregular_file"):
            probe.inspect(self.state)

    def test_fails_closed_on_pointer_traversal_and_absolute_paths(self):
        for injected in ("../../elsewhere", "/tmp/untrusted", "20261015T142500Z-../evil"):
            with self.subTest(injected=injected):
                (self.state / "last-known-good.json").write_text(
                    json.dumps({"snapshot_id": injected}), encoding="utf-8",
                )
                with self.assertRaisesRegex(probe.Denied, "invalid_snapshot_id"):
                    probe.inspect(self.state)

    def test_fails_closed_on_duplicate_and_nonfinite_json(self):
        pointer = self.state / "last-known-good.json"
        for value in (
            '{"snapshot_id":"20261015T142500Z-deadbeef","snapshot_id":"20261015T142500Z-deadbeef"}',
            '{"snapshot_id":NaN}',
            '[{"snapshot_id":"20261015T142500Z-deadbeef"}]',
        ):
            with self.subTest(value=value):
                pointer.write_text(value, encoding="utf-8")
                with self.assertRaises(probe.Denied):
                    probe.inspect(self.state)

    def test_rejects_manifest_path_traversal_and_persistent_state(self):
        for name in ("../history.db", "/etc/passwd", "history.db", ".git/config", "a//b", "a\\b"):
            with self.subTest(name=name):
                self.hashes = {name: hashlib.sha256(self.text).hexdigest()}
                self.save()
                with self.assertRaisesRegex(probe.Denied, "invalid_hash_manifest"):
                    probe.inspect(self.state)

    def test_rejects_hash_format_mismatch_and_manifest_identity_drift(self):
        self.hashes = {"server.py": "a" * 63}
        self.save()
        with self.assertRaisesRegex(probe.Denied, "invalid_hash_manifest"):
            probe.inspect(self.state)
        self.hashes = {"server.py": hashlib.sha256(self.text).hexdigest()}
        self.save()
        manifest = self.snap / "manifest.json"
        obj = json.loads(manifest.read_text(encoding="utf-8"))
        obj["snapshot_id"] = "20261015T142501Z-deadbeef"
        manifest.write_text(json.dumps(obj), encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "manifest_identity_mismatch"):
            probe.inspect(self.state)

    def test_fails_closed_on_oversize_manifest_without_reading_unbounded_input(self):
        manifest = self.snap / "manifest.json"
        manifest.write_bytes(b"x" * (probe.MAX_JSON + 1))
        with self.assertRaisesRegex(probe.Denied, "oversized_json"):
            probe.inspect(self.state)

    def test_empty_or_entrypointless_manifest_does_not_count_as_safe_restore(self):
        self.hashes.clear()
        self.save()
        with self.assertRaisesRegex(probe.Denied, "missing_required_source"):
            probe.inspect(self.state)
        self.hashes["projects.json"] = hashlib.sha256(b"{}").hexdigest()
        self.save()
        with self.assertRaisesRegex(probe.Denied, "missing_required_source"):
            probe.inspect(self.state)

    def test_extra_validly_hashed_unmanaged_top_level_file_is_denied(self):
        rogue = b"do not restore"
        (self.files / "unmanaged.py").write_bytes(rogue)
        self.hashes["unmanaged.py"] = hashlib.sha256(rogue).hexdigest()
        self.save()
        with self.assertRaisesRegex(probe.Denied, "invalid_hash_manifest"):
            probe.inspect(self.state)

    def test_symlinked_snapshot_root_and_hardlinked_file_are_denied(self):
        original = self.snap
        renamed = self.snapshots / "real-snapshot"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        with self.assertRaisesRegex(probe.Denied, "nonregular_directory"):
            probe.inspect(self.state)
        original.unlink()
        renamed.rename(original)
        alternate = Path(self.tmp.name) / "hardlinked-source"
        alternate.hardlink_to(self.files / "server.py")
        with self.assertRaisesRegex(probe.Denied, "nonregular_file"):
            probe.inspect(self.state)

    def test_special_file_is_denied_before_open(self):
        import os
        fifo = self.files / "named-pipe"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(probe.Denied, "special_file_in_snapshot"):
            probe.inspect(self.state)

    def test_boolean_format_version_is_not_accepted_as_integer_one(self):
        manifest = self.snap / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["format_version"] = True
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "manifest_identity_mismatch"):
            probe.inspect(self.state)

    def test_directory_enumeration_is_bounded_before_sorting(self):
        from unittest.mock import patch
        for index in range(15):
            (self.files / f"extra-{index}.txt").write_text("x", encoding="utf-8")
        with patch.object(probe, "MAX_FILES", 4):
            with self.assertRaisesRegex(probe.Denied, "too_many_paths"):
                probe.inspect(self.state)

    def test_malformed_dot_only_hash_name_denied_without_traceback(self):
        self.hashes = {".": hashlib.sha256(self.text).hexdigest()}
        self.save()
        with self.assertRaisesRegex(probe.Denied, "invalid_hash_manifest"):
            probe.inspect(self.state)

    def test_pathological_json_nesting_is_denied_with_fixed_reason(self):
        pointer = self.state / "last-known-good.json"
        pointer.write_text("[" * 1300 + "0" + "]" * 1300, encoding="utf-8")
        with self.assertRaisesRegex(probe.Denied, "invalid_json"):
            probe.inspect(self.state)

    def test_cli_json_failure_is_sanitized_and_read_only(self):
        (self.files / "server.py").write_text("tampered", encoding="utf-8")
        script = Path(probe.__file__)
        proc = subprocess.run(
            [sys.executable, str(script), "--state-dir", str(self.state)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(2, proc.returncode, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual("hash_mismatch", result["reason"])
        self.assertFalse(result["mutation_performed"])
        self.assertFalse(result["authorizes_deploy"])
        self.assertFalse(result["authorizes_rollback"])
        self.assertNotIn(str(self.state), proc.stdout)

    def test_cli_valid_does_not_modify_any_snapshot_file(self):
        manifest = self.snap / "manifest.json"
        pointer = self.state / "last-known-good.json"
        before = [file.read_bytes() for file in (manifest, pointer, self.files / "server.py")]
        proc = subprocess.run(
            [sys.executable, str(Path(probe.__file__)), "--state-dir", str(self.state)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        self.assertEqual(before, [file.read_bytes() for file in (manifest, pointer, self.files / "server.py")])


if __name__ == "__main__":
    unittest.main()
