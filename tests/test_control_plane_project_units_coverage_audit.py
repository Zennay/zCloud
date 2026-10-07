import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_project_units_coverage_audit.py"

spec = importlib.util.spec_from_file_location("units_audit", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_current_main_exposes_zguard_units_gap():
    report = mod.audit(ROOT / "projects.json", ROOT / "enhancements.py")
    assert report["format"] == "zcloud-project-units-coverage-v1"
    assert "zguard" in report["missing_active_projects"]
    assert report["coverage_complete"] is False


def test_duplicate_project_ids_fail_closed(tmp_path):
    projects = tmp_path / "projects.json"
    projects.write_text(
        '[{"id":"x","status":"active"},{"id":"x","status":"active"}]',
        encoding="utf-8",
    )
    enhancements = tmp_path / "enhancements.py"
    enhancements.write_text('PROJECT_UNITS = {"x": []}\n', encoding="utf-8")
    try:
        mod.audit(projects, enhancements)
    except ValueError as exc:
        assert "duplicate project ids" in str(exc)
    else:
        raise AssertionError("duplicate project IDs must fail closed")


def test_symlink_inputs_are_rejected(tmp_path):
    source = tmp_path / "projects-real.json"
    source.write_text('[{"id":"x","status":"active"}]', encoding="utf-8")
    link = tmp_path / "projects.json"
    link.symlink_to(source)
    enhancements = tmp_path / "enhancements.py"
    enhancements.write_text('PROJECT_UNITS = {"x": []}\n', encoding="utf-8")
    try:
        mod.audit(link, enhancements)
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink input must fail closed")
