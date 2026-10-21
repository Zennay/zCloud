import ast
import json
import logging
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"


def layout_functions():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"), filename=str(SERVER))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"load_project_layout", "save_project_layout"}
    ]
    if {node.name for node in selected} != {"load_project_layout", "save_project_layout"}:
        raise AssertionError("layout functions missing from server.py")
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"json": json, "logging": logging, "Path": Path}
    exec(compile(module, str(SERVER), "exec"), namespace)
    return namespace["load_project_layout"], namespace["save_project_layout"], namespace


class ConfigRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zcloud-layout-roundtrip-")
        self.layout_path = Path(self.tmp.name) / "project-layout.json"
        self.projects = [{"id": "cloud"}, {"id": "ftmo"}, {"id": "supa"}]
        self.load, self.save, self.namespace = layout_functions()
        self.namespace["LAYOUT_FILE"] = self.layout_path

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_layout_uses_registry_order(self):
        self.assertEqual(
            {"order": ["cloud", "ftmo", "supa"], "archived": []},
            self.load(self.projects),
        )

    def test_save_load_roundtrip_canonicalizes_duplicates_and_unknown_ids(self):
        requested = {
            "order": ["ftmo", "cloud", "ftmo", "ghost"],
            "archived": ["supa", "ghost", "supa"],
        }
        saved = self.save(requested, self.projects)
        loaded = self.load(self.projects)
        expected = {
            "order": ["ftmo", "cloud", "supa"],
            "archived": ["supa"],
        }
        self.assertEqual(expected, saved)
        self.assertEqual(expected, loaded)
        self.assertEqual(
            expected,
            json.loads(self.layout_path.read_text(encoding="utf-8")),
        )

    def test_canonical_roundtrip_is_idempotent(self):
        first = self.save(
            {"order": ["supa"], "archived": ["cloud"]},
            self.projects,
        )
        second = self.save(self.load(self.projects), self.projects)
        self.assertEqual(
            {"order": ["supa", "cloud", "ftmo"], "archived": ["cloud"]},
            first,
        )
        self.assertEqual(first, second)
        self.assertFalse(self.layout_path.with_suffix(".tmp").exists())

    def test_malformed_persisted_layout_falls_back_without_mutation(self):
        self.layout_path.write_text("{not-json", encoding="utf-8")
        before = self.layout_path.read_bytes()
        loaded = self.load(self.projects)
        self.assertEqual(
            {"order": ["cloud", "ftmo", "supa"], "archived": []},
            loaded,
        )
        self.assertEqual(before, self.layout_path.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
