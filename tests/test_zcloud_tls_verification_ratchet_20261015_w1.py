"""Adversarial offline tests for deploy-ops TLS-verification no-new-debt gate."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts import zcloud_tls_verification_ratchet_20261015_w1 as gate


class SourcePatterns(unittest.TestCase):
    def count(self, text, key):
        return gate.insecure_counts(text)[key]

    def test_curl_short_option(self):
        self.assertEqual(self.count("run: curl -fsSk https://example.org", "curl_insecure"), 1)

    def test_curl_long_option(self):
        self.assertEqual(self.count("run: curl --insecure https://example.org", "curl_insecure"), 1)

    def test_curl_after_url(self):
        self.assertEqual(self.count("run: curl https://example.org -k", "curl_insecure"), 1)

    def test_safe_curl(self):
        self.assertEqual(self.count("run: curl -fsSL https://example.org", "curl_insecure"), 0)

    def test_wget(self):
        self.assertEqual(self.count("run: wget --no-check-certificate https://example.org", "wget_no_certificate"), 1)

    def test_git_ssl(self):
        self.assertEqual(self.count("run: git -c http.sslVerify=false clone https://example.org", "git_ssl_verify_disabled"), 1)

    def test_git_ssl_environment(self):
        self.assertEqual(self.count("GIT_SSL_NO_VERIFY: 'true'", "git_ssl_no_verify"), 1)

    def test_node_tls(self):
        self.assertEqual(self.count("NODE_TLS_REJECT_UNAUTHORIZED=0", "node_tls_bypass"), 1)

    def test_python_tls(self):
        self.assertEqual(self.count("PYTHONHTTPSVERIFY: 0", "python_tls_bypass"), 1)

    def test_npm_strict_ssl(self):
        self.assertEqual(self.count("run: npm config set strict-ssl false", "npm_tls_bypass"), 1)

    def test_yarn_strict_ssl(self):
        self.assertEqual(self.count("run: yarn config set strict-ssl false", "npm_tls_bypass"), 1)

    def test_npm_environment(self):
        self.assertEqual(self.count("npm_config_strict_ssl=false", "npm_env_tls_bypass"), 1)

    def test_python_requests(self):
        self.assertEqual(self.count("run: python -c 'requests.get(url, verify=False)'",
                                    "python_requests_bypass"), 1)

    def test_backslash_newline(self):
        self.assertEqual(self.count("run: curl \\\n  --insecure https://example.org",
                                    "curl_insecure"), 1)

    def test_full_line_comment_ignored(self):
        self.assertEqual(self.count("# curl -k https://example.org", "curl_insecure"), 0)

    def test_unrelated_unicode_is_permitted(self):
        self.assertEqual(self.count("name: 'Controle 🟢'\nrun: curl -fsSL https://example.org",
                                    "curl_insecure"), 0)

    def test_source_size_bound(self):
        with self.assertRaises(gate.Unverifiable):
            gate.insecure_counts("x" * 100_001)


class RealGitHistory(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.dir = self.root / ".github" / "workflows"
        self.dir.mkdir(parents=True)
        self.wf = self.dir / "safe.yml"
        self.git("init", "-q")
        self.git("config", "user.email", "deployops@example.invalid")
        self.git("config", "user.name", "Deployops tests")
        self.wf.write_text("name: safe\non: pull_request\njobs: {}\n", encoding="utf-8")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def scan(self):
        return gate.evaluate(self.root, self.base, self.git("rev-parse", "HEAD"))

    def test_new_insecure_command_rejected(self):
        self.wf.write_text("name: unsafe\nrun: curl -k https://example.org\n", encoding="utf-8")
        self.commit()
        changes, problems = self.scan()
        self.assertEqual(changes, [(b"M", ".github/workflows/safe.yml")])
        self.assertTrue(any("curl_insecure:new=1" in item for item in problems))

    def test_unchanged_legacy_debt_does_not_expand(self):
        self.wf.write_text("run: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.base = self.commit()
        self.wf.write_text("name: safe\nrun: curl -k https://legacy.example.org\n",
                           encoding="utf-8")
        self.commit()
        self.assertEqual(self.scan()[1], [])

    def test_replacing_legacy_insecure_target_is_new_debt(self):
        self.wf.write_text("run: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.base = self.commit()
        self.wf.write_text("run: curl -k https://different.example.org\n", encoding="utf-8")
        self.commit()
        self.assertTrue(any("curl_insecure:new=1" in problem for problem in self.scan()[1]))

    def test_new_duplicate_insecure_line_is_new_debt(self):
        self.wf.write_text("run: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.base = self.commit()
        self.wf.write_text("run: curl -k https://legacy.example.org\nrun: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.commit()
        self.assertTrue(any("curl_insecure:new=1" in problem for problem in self.scan()[1]))

    def test_existing_debt_growth_denied(self):
        self.wf.write_text("run: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.base = self.commit()
        self.wf.write_text("run: curl -k https://legacy.example.org\nrun: curl -k https://new.example.org\n", encoding="utf-8")
        self.commit()
        self.assertTrue(self.scan()[1])

    def test_added_workflow_denied(self):
        other = self.dir / "new.yaml"
        other.write_text("run: wget --no-check-certificate https://example.org\n", encoding="utf-8")
        self.commit()
        changes, problems = self.scan()
        self.assertEqual(changes, [(b"A", ".github/workflows/new.yaml")])
        self.assertTrue(any("wget_no_certificate" in item for item in problems))

    def test_removed_insecure_command_allowed(self):
        self.wf.write_text("run: curl -k https://legacy.example.org\n", encoding="utf-8")
        self.base = self.commit()
        self.wf.write_text("run: curl -fsSL https://example.org\n", encoding="utf-8")
        self.commit()
        self.assertEqual(self.scan()[1], [])

    def test_unrelated_file_does_not_trigger(self):
        (self.root / "note.txt").write_text("curl -k\n", encoding="utf-8")
        self.commit()
        self.assertEqual(self.scan(), ([], []))

    def test_root_local_action_manifest_is_scanned(self):
        action = self.root / "action.yaml"
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: curl -k https://example.org\n",
                          encoding="utf-8")
        self.commit()
        changes, problems = self.scan()
        self.assertIn((b"A", "action.yaml"), changes)
        self.assertTrue(any("curl_insecure" in item for item in problems))

    def test_deep_local_action_manifest_is_scanned(self):
        parent = self.root.joinpath(*(["nested"] * 8))
        parent.mkdir(parents=True)
        action = parent / "action.yml"
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: wget --no-check-certificate https://example.org\n",
                          encoding="utf-8")
        self.commit()
        changes, problems = self.scan()
        self.assertTrue(any("nested/nested/nested/nested/" in path for _, path in changes))
        self.assertTrue(any("wget_no_certificate" in item for item in problems))

    def test_safe_local_action_manifest_allowed(self):
        action = self.root / "action.yml"
        action.write_text("runs:\n  using: composite\n  steps:\n    - run: curl -fsSL https://example.org\n",
                          encoding="utf-8")
        self.commit()
        changes, problems = self.scan()
        self.assertIn((b"A", "action.yml"), changes)
        self.assertEqual(problems, [])

    def test_symlink_workflow_denied(self):
        self.wf.unlink()
        self.wf.symlink_to(self.root / "missing")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.scan()

    def test_invalid_sha_fails_closed(self):
        with self.assertRaises(gate.Unverifiable):
            gate.workflow_changes(self.root, "main; rm -rf /", self.base)

    def test_nonascii_filename_fails_closed(self):
        other = self.dir / "é.yml"
        other.write_text("run: curl -k\n", encoding="utf-8")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.scan()

    def test_source_mode_executable_denied(self):
        self.wf.chmod(0o755)
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.scan()

    def test_cli_prints_no_mutation_authority(self):
        self.wf.write_text("run: curl -k https://example.org\n", encoding="utf-8")
        head = self.commit()
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = gate.main(["--root", str(self.root), "--base", self.base, "--head", head])
        self.assertEqual(status, 1)
        self.assertIn("PRODUCTION_MUTATION_AUTHORIZED=false", stream.getvalue())

    def test_unverifiable_blob_fails_closed(self):
        self.wf.write_bytes(b"\xff\xfe")
        self.commit()
        with self.assertRaises(gate.Unverifiable):
            self.scan()


if __name__ == "__main__":
    unittest.main()
