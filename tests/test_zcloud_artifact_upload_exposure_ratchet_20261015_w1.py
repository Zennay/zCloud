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

    def test_encoded_action_unicode_bypass(self):
        self.check(SAFE.replace("uses: actions/upload-artifact@v4",
                                r'uses: "actions/up\u006coad-artifact@v4"'), False)

    def test_encoded_action_hex_bypass(self):
        self.check(SAFE.replace("uses: actions/upload-artifact@v4",
                                r'uses: "actions/\x75pload-artifact@v4"'), False)

    def test_encoded_action_upper_unicode_bypass(self):
        self.check(SAFE.replace("uses: actions/upload-artifact@v4",
                                r'uses: "actions/u\U00000070load-artifact@v4"'), False)

    def test_encoded_mapping_key_and_action_bypass(self):
        self.check(SAFE.replace("uses: actions/upload-artifact@v4",
                                r'"u\u0073es": "actions/up\u006coad-artifact@v4"'), False)

    def test_quoted_multiline_upload_action_escape_denied(self):
        # Double-quoted YAML backslash-newline concatenates the two tokens.
        # The original source lacks the complete 'upload-artifact' substring.
        payload = SAFE.replace("uses: actions/upload-artifact@v4",
                               "uses: \"actions/upload-\\\n          artifact@v4\"")
        self.assertNotIn("upload-artifact", payload)
        self.check(payload, False)

    def test_folded_uses_action_reference_denied_without_literal(self):
        payload = SAFE.replace("uses: actions/upload-artifact@v4",
                               "uses: >-\n          actions/upload-\n          artifact@v4")
        self.assertNotIn("upload-artifact", payload)
        self.check(payload, False)

    def test_uses_alias_and_anchor_denied_without_literal(self):
        for value in ("*uploadref", "&uploadref actions/checkout@v4",
                      "!custom actions/checkout@v4", "{foo: bar}", "[foo]"):
            with self.subTest(value=value):
                payload = SAFE.replace("uses: actions/upload-artifact@v4",
                                       "uses: " + value)
                self.assertNotIn("upload-artifact", payload)
                self.check(payload, False)

    def test_safe_explicit_quoted_action_reference_preserved(self):
        for value in ('"actions/upload-artifact@v4"',
                      "'actions/upload-artifact@v4'",
                      "actions/upload-artifact@v4 # an explanatory comment"):
            with self.subTest(value=value):
                self.check(SAFE.replace("uses: actions/upload-artifact@v4",
                                        "uses: " + value), True)
        self.check("name: smoke\njobs:\n  test:\n    steps:\n"
                   "      - uses: actions/checkout@v4\n", True)

    def test_workflow_unicode_controls_and_separators_fail_closed(self):
        for char in ("\u0085", "\u200b", "\u202e", "\u2066",
                     "\ufeff", "\u2028", "\u2029", "\ud800"):
            with self.subTest(codepoint=hex(ord(char))):
                # Insert into an otherwise safe workflow comment; even control
                # characters outside a recognized input are ambiguous source.
                self.check(SAFE + "# note:" + char + "\n", False)

    def test_printable_unicode_comment_is_not_blocked(self):
        self.check(SAFE + "# résumé: ordinary UTF-8 comment\n", True)

    def test_cloud_identity_folder_denied(self):
        for path in ("reports/.aws/config", "out/.kube/config", "out/.azure/profile",
                     "out/.gnupg/pubring", "out/.gcloud/settings"):
            with self.subTest(path=path):
                self.check(SAFE.replace("dist/result.log", path), False)

    def test_registry_cloud_and_local_identity_files_denied(self):
        unsafe = (".docker/config.json", "build/.terraform/environment",
                  "out/.npm/_auth", "cfg/.m2/settings.xml",
                  "cfg/.config/gh/hosts.yml", ".netrc", "cfg/.pypirc",
                  "cfg/.git-credentials", "dist/id_ed25519",
                  "dist/id_ecdsa", "dist/id_dsa", "build/gradle.properties")
        for path in unsafe:
            with self.subTest(path=path):
                self.check(SAFE.replace("dist/result.log", path), False)

    def test_noncredential_manifest_and_reports_remain_allowed(self):
        for path in ("dist/docker-build.log", "reports/npm-test.txt",
                     "out/configuration-report.json", "tmp/maven-summary.log"):
            with self.subTest(path=path):
                self.check(SAFE.replace("dist/result.log", path), True)

    def test_private_key_container_extensions_denied(self):
        for suffix in ("pem", "p12", "pfx", "key", "jks", "keystore", "asc"):
            with self.subTest(suffix=suffix):
                self.check(SAFE.replace("dist/result.log", "out/public." + suffix), False)

    def test_normal_artifact_extension_preserved(self):
        for suffix in ("txt", "log", "json", "xml"):
            with self.subTest(suffix=suffix):
                self.check(SAFE.replace("dist/result.log", "out/build." + suffix), True)

    def test_named_upload_step_sibling_with_is_valid(self):
        named = SAFE.replace("      - uses: actions/upload-artifact@v4",
                             "      - name: Upload report\n"
                             "        uses: actions/upload-artifact@v4")
        self.check(named, True)

    def test_named_upload_step_unsafe_artifact_denied(self):
        named = SAFE.replace("      - uses: actions/upload-artifact@v4",
                             "      - name: Upload report\n"
                             "        uses: actions/upload-artifact@v4")
        self.check(named.replace("dist/result.log", ".ssh/id_ed25519"), False)

    def test_nested_env_with_does_not_satisfy_upload_inputs(self):
        replaced = SAFE.replace(
            "        with:\n          path: dist/result.log\n"
            "          include-hidden-files: false",
            "        env:\n          with:\n"
            "            path: dist/result.log\n"
            "            include-hidden-files: false")
        self.check(replaced, False)

    def test_named_upload_step_nested_with_is_denied(self):
        named = SAFE.replace("      - uses: actions/upload-artifact@v4",
                             "      - name: Upload report\n"
                             "        uses: actions/upload-artifact@v4")
        nested = named.replace(
            "        with:\n          path: dist/result.log\n"
            "          include-hidden-files: false",
            "        env:\n          with:\n"
            "            path: dist/result.log\n"
            "            include-hidden-files: false")
        self.check(nested, False)

    def test_duplicate_with_section_is_not_silently_accepted(self):
        payload = SAFE.replace("include-hidden-files: false",
                               "include-hidden-files: false\n        with:\n          path: .env")
        self.check(payload, False)

    def test_nested_yaml_path_is_not_an_action_input(self):
        payload = SAFE.replace("path: dist/result.log",
                               "meta:\n            path: dist/result.log")
        self.check(payload, False)

    def test_sibling_step_property_does_not_change_upload_input(self):
        payload = SAFE.replace("include-hidden-files: false",
                               "include-hidden-files: false\n        if: always()")
        self.check(payload, True)

    def test_safe_normal_uses_action_not_rejected(self):
        self.check(SAFE.replace("actions/upload-artifact@v4",
                                "actions/upload-artifact@v4.4.3"), True)

    def test_unrelated_safe(self): self.check("name: safe\njobs:\n  build:\n    run: echo hi", True)

    def test_git_output_bound_kills_stdout_holder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            marker = root / "orphan-wrote"
            git = root / "git"
            child = "import pathlib,sys,time;time.sleep(0.55);pathlib.Path(sys.argv[1]).write_text('bad')"
            import json
            git.write_text("#!/usr/bin/env python3\nimport subprocess,sys,time\n"
                           + "subprocess.Popen([sys.executable,'-c',"
                           + repr(child) + "," + repr(str(marker)) + "])\n"
                           + "time.sleep(0.06)\nsys.stdout.write('z'*4096)\nsys.stdout.flush()\n")
            git.chmod(0o755)
            old = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = d + os.pathsep + old
                with self.assertRaises(gate.Denied):
                    gate.git("status", cap=128)
            finally:
                os.environ["PATH"] = old
            import time
            time.sleep(0.65)
            self.assertFalse(marker.exists(), "orphan Git child survived bound failure")

    def test_git_benign_no_kill(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake = root / "git"
            fake.write_text("#!/usr/bin/env python3\nprint('ok')\n")
            fake.chmod(0o755)
            old = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = d + os.pathsep + old
                self.assertEqual(gate.git("status", cap=8), b"ok\n")
            finally:
                os.environ["PATH"] = old

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

    def test_divergent_git_history_fails_ancestry_closed(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            f = Path(d) / ".github/workflows/a.yml"
            f.parent.mkdir(parents=True)
            f.write_text(SAFE)
            git("add", ".")
            git("commit", "-qm", "common")
            common = git("rev-parse", "HEAD")
            f.write_text(SAFE.replace("result.log", "first.log"))
            git("commit", "-qam", "left")
            unrelated_base = git("rev-parse", "HEAD")
            git("checkout", "-q", "-b", "other", common)
            f.write_text(SAFE.replace("result.log", "second.log"))
            git("commit", "-qam", "right")
            right = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaisesRegex(gate.Denied, "invalid_ancestry"):
                    gate.workflow_paths(unrelated_base, right)
                self.assertEqual(gate.workflow_paths(common, right),
                                 [".github/workflows/a.yml"])
            finally:
                os.chdir(old)

    def test_artifact_admission_triggers_for_all_pull_requests(self):
        # GitHub changed-path filters inspect only a bounded diff file set.
        # A risky local Action can be hidden beyond the provider's path cap.
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/zcloud-artifact-upload-exposure-ratchet-20261015-w1.yml").read_text()
        self.assertIn("on:\n  pull_request:\n", workflow)
        self.assertNotIn("\n    paths:", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_deep_composite_manifest_real_git_source_checked(self):
        # A legitimate local composite Action may be more than 6 directories
        # deep. The previous regex classified it as a non-manifest and skipped.
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            action = Path(d) / "actions/a/b/c/d/e/f/g/h/action.yaml"
            action.parent.mkdir(parents=True)
            action.write_text(SAFE)
            git("add", ".")
            git("commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            action.write_text(SAFE.replace("dist/result.log", ".aws/credentials"))
            git("add", ".")
            git("commit", "-qm", "secret-bearing upload")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head),
                                 ["actions/a/b/c/d/e/f/g/h/action.yaml"])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 2)
            finally:
                os.chdir(old)

    def test_unbounded_nested_manifest_name_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            (Path(d) / "README").write_text("base\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            action = Path(d) / ("actions/" + "/".join(["deep"] * 110) + "/action.yml")
            action.parent.mkdir(parents=True)
            action.write_text(SAFE)
            git("add", ".")
            git("commit", "-qm", "excessive source path")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaisesRegex(gate.Denied, "source_path_too_long"):
                    gate.workflow_paths(base, head)
            finally:
                os.chdir(old)

    def test_arbitrary_tools_tree_action_manifest_is_inspected(self):
        # Local workflow reference to ./tools/deploy/probe is legitimate.
        # A change to its action.yaml must not be skipped just because the
        # path is outside root actions/ and .github/actions/.
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            action = Path(d) / "tools/deploy/probe/action.yaml"
            action.parent.mkdir(parents=True)
            action.write_text(SAFE)
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            action.write_text(SAFE.replace("dist/result.log", "out/.docker/config.json"))
            git("add", ".")
            git("commit", "-qm", "credential upload")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head),
                                 ["tools/deploy/probe/action.yaml"])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 2)
            finally:
                os.chdir(old)

    def test_root_manifest_is_checked_and_unrelated_unicode_code_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            action = Path(d) / "action.yml"
            action.write_text(SAFE)
            unrelated = Path(d) / "notes" / "résumé.txt"
            unrelated.parent.mkdir()
            unrelated.write_text("safe\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            action.write_text(SAFE.replace("dist/result.log", "artifact/.ssh/id_rsa"))
            unrelated.write_text("still safe\n")
            git("add", ".")
            git("commit", "-qm", "mixed inputs")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head), ["action.yml"])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 2)
            finally:
                os.chdir(old)

    def test_composite_manifest_real_git_delta(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            action = Path(d) / ".github/actions/archive/action.yml"
            action.parent.mkdir(parents=True)
            good = ("name: Archive\nruns:\n  using: composite\n  steps:\n"
                    "    - uses: actions/upload-artifact@v4\n"
                    "      with:\n        path: dist/result.log\n"
                    "        include-hidden-files: false\n")
            action.write_text(good)
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            action.write_text(good.replace("result.log", "private.pem"))
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head),
                                 [".github/actions/archive/action.yml"])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 2)
                gate.examine(good)
            finally:
                os.chdir(old)

    def test_root_actions_composite_action_yaml_exposure(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            anchor = Path(d) / "README.md"
            anchor.write_text("repo\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            action = Path(d) / "actions/archive/action.yaml"
            action.parent.mkdir(parents=True)
            action.write_text("name: Local\nruns:\n  using: composite\n  steps:\n" +
                              "    - uses: actions/upload-artifact@v4\n" +
                              "      with:\n        path: secrets.env\n")
            git("add", ".")
            git("commit", "-qm", "bad manifest")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head),
                                 ["actions/archive/action.yaml"])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 2)
            finally:
                os.chdir(old)

    def test_composite_non_manifest_script_not_source_entrypoint(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            f = Path(d) / ".github/actions/archive/run.sh"
            f.parent.mkdir(parents=True)
            f.write_text("echo build\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            f.write_text("echo test\n")
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(gate.workflow_paths(base, head), [])
                self.assertEqual(gate.main(["--base", base, "--head", head]), 0)
            finally:
                os.chdir(old)

    def test_untrusted_composite_action_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            root = Path(d) / ".github/actions/local"
            root.mkdir(parents=True)
            (root / "run.sh").write_text("echo safe\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "extra.sh").symlink_to("../../outside")
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaisesRegex(gate.Denied, "opaque_local_action_entry"):
                    gate.workflow_paths(base, head)
            finally:
                os.chdir(old)

    def test_untrusted_composite_action_gitlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*args):
                return subprocess.check_output(["git", "-C", d, *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "test")
            git("config", "user.email", "test@example.com")
            root = Path(d) / ".github"
            root.mkdir()
            (root / "placeholder").write_text("base\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            # A Gitlink is an opaque commit reference, not inspectable Action source.
            git("update-index", "--add", "--cacheinfo", "160000", base,
                ".github/actions/external")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            old = os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaisesRegex(gate.Denied, "opaque_local_action_entry"):
                    gate.workflow_paths(base, head)
            finally:
                os.chdir(old)

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
