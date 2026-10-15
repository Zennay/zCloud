"""Real Git and adversarial regressions for a read-only artifact exposure gate."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

src = Path(__file__).resolve().parents[1] / "scripts/zcloud_artifact_upload_exposure_ratchet_20261015_w1.py"
spec = importlib.util.spec_from_file_location("gate", src)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SAFE = """name: CI
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@v4
        with:
          path: dist/result.log
          include-hidden-files: false
"""

class SourceTests(unittest.TestCase):
    def check(self, text, okay):
        if okay:
            gate.examine(text)
        else:
            with self.assertRaises(gate.Denied):
                gate.examine(text)

    def test_good_narrow(self): self.check(SAFE, True)
    def test_bad_hidden(self): self.check(SAFE.replace("files: false", "files: true"), False)
    def test_bad_dynamic_hidden(self): self.check(SAFE.replace("files: false", "files: " + "$" + "{{ inputs.include }}"), False)
    def test_bad_no_path(self): self.check(SAFE.replace("path:", "other:"), False)
    def test_bad_dotenv(self): self.check(SAFE.replace("dist/result.log", ".env.prod"), False)
    def test_bad_key(self): self.check(SAFE.replace("dist/result.log", ".ssh/id_rsa"), False)
    def test_bad_credentials(self): self.check(SAFE.replace("dist/result.log", "tmp/credentials.json"), False)
    def test_bad_root(self): self.check(SAFE.replace("dist/result.log", "."), False)
    def test_bad_glob(self): self.check(SAFE.replace("dist/result.log", "**/*"), False)
    def test_bad_expression(self): self.check(SAFE.replace("dist/result.log", "$" + "{{ runner.temp }}"), False)
    def test_bad_multiline(self): self.check(SAFE.replace("dist/result.log", "|"), False)
    def test_bad_traversal(self): self.check(SAFE.replace("dist/result.log", "../config"), False)
    def test_bad_anchor(self): self.check(SAFE.replace("dist/result.log", "&anchor dist/result.log"), False)
    def test_bad_duplicate(self): self.check(SAFE.replace("path: dist/result.log", "path: dist/result.log\n          path: build.txt"), False)
    def test_bad_alias(self): self.check(SAFE.replace("uses: actions/upload-artifact@v4", "uses: *upload # upload-artifact"), False)
    def test_single_glob(self): self.check(SAFE.replace("dist/result.log", "dist/*.log"), False)
    def test_yaml_backslash_escape(self): self.check(SAFE.replace("dist/result.log", "dist/\\u002eenv"), False)
    def test_url_encoded_dotenv(self): self.check(SAFE.replace("dist/result.log", "dist/%2eenv"), False)
    def test_token_report_filename(self): self.check(SAFE.replace("dist/result.log", "dist/token-report.log"), False)
    def test_secret_basename(self): self.check(SAFE.replace("dist/result.log", "dist/secret_backup.log"), False)
    def test_unicode_path(self): self.check(SAFE.replace("dist/result.log", "dist/résumé.log"), False)
    def test_directory_upload_denied(self): self.check(SAFE.replace("dist/result.log", "dist/reports/"), False)

    def test_unrelated_safe(self): self.check("name: safe\njobs:\n  build:\n    run: echo hi", True)

    def test_git_diff_real_and_head_guard(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git","-C",d,*args], stderr=subprocess.DEVNULL).decode().strip()
            git("init","-q")
            git("config","user.name","test")
            git("config","user.email","test@example.com")
            w=Path(d)/".github/workflows"
            w.mkdir(parents=True)
            f=w/"a.yml"
            f.write_text(SAFE)
            git("add",".")
            git("commit","-qm","base")
            base=git("rev-parse","HEAD")
            f.write_text(SAFE.replace("files: false","files: true"))
            git("add",".")
            git("commit","-qm","head")
            head=git("rev-parse","HEAD")
            old=os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base,head),[".github/workflows/a.yml"])
                self.assertEqual(gate.main(["--base",base,"--head",head]),2)
                self.assertEqual(gate.main(["--base",head,"--head",head]),0)
                with self.assertRaises(gate.Denied):gate.workflow_paths(base,"f"*40)
            finally: os.chdir(old)

    def test_symlink_denial(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git","-C",d,*args],stderr=subprocess.DEVNULL).decode().strip()
            git("init","-q")
            git("config","user.name","test")
            git("config","user.email","test@example.com")
            p=Path(d)/".github/workflows"
            p.mkdir(parents=True)
            (Path(d)/"target").write_text(SAFE)
            (p/"a.yml").symlink_to("../../target")
            git("add",".")
            git("commit","-qm","symlink")
            old=os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaises(gate.Denied):
                    gate.read_workflow(git("rev-parse","HEAD"),".github/workflows/a.yml")
            finally: os.chdir(old)

if __name__ == "__main__": unittest.main()
