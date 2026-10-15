"""Real Git adversarial source checks; no live repository mutation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/zcloud_direct_main_write_ratchet_20261015_w3.py"
SAFE = "name: safe\non: pull_request\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n"


class RatchetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI Fixture")
        self.git("config", "user.email", "ci@example.invalid")
        self.write(".github/workflows/safe.yml", SAFE)
        self.base = self.commit()

    def git(self, *args):
        p = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def write(self, path, content):
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def audit(self, base=None, head=None):
        p = subprocess.run([sys.executable, str(SCRIPT), "--base", base or self.base,
                            "--head", head or self.git("rev-parse", "HEAD")],
                           cwd=self.root, capture_output=True, text=True, timeout=30)
        return p.returncode, json.loads(p.stdout)

    def change(self, command, path=".github/workflows/safe.yml"):
        self.write(path, SAFE.replace("echo safe", command))
        self.commit()
        return self.audit()

    def test_unchanged(self):
        code, result = self.audit()
        self.assertEqual(code, 0)
        self.assertEqual(result["changed_workflows_checked"], 0)

    def test_safe_new(self):
        code, r = self.change("git fetch origin main", ".github/workflows/new.yml")
        self.assertEqual(code, 0, r)
        self.assertEqual(r["changed_workflows_checked"], 1)
        for k in ("authorizes_merge", "authorizes_deploy", "mutation_performed"):
            self.assertIs(r[k], False)

    def test_direct_push(self):
        code, r = self.change("git push origin HEAD:main")
        self.assertEqual(code, 1)
        self.assertEqual(r["findings"][0]["reasons"], ["git_push"])

    def test_force_push(self):
        self.assertEqual(self.change("git push -f origin HEAD:refs/heads/main")[0], 1)

    def test_git_c_push(self):
        self.assertEqual(self.change("git -C /tmp/repo push origin main")[0], 1)

    def test_dynamic_ref_push(self):
        self.assertEqual(self.change("git push origin HEAD:$BRANCH")[0], 1)

    def test_other_branch_push_conservatively_denied(self):
        self.assertEqual(self.change("git push origin HEAD:feature")[0], 1)

    def test_gh_refs_patch(self):
        self.assertEqual(self.change("gh api -X PATCH repos/x/y/git/refs/heads/main")[0], 1)

    def test_gh_api_dynamic_write(self):
        self.assertEqual(self.change("gh api -X POST \"$REPO/$ENDPOINT\"")[0], 1)

    def test_curl_refs_patch(self):
        self.assertEqual(self.change("curl -X PATCH https://api.github.com/repos/x/y/git/refs/heads/main")[0], 1)

    def test_legacy_byte_identical(self):
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main"))
        base = self.commit()
        self.write(".github/workflows/new.yml", SAFE)
        head = self.commit()
        code, r = self.audit(base, head)
        self.assertEqual(code, 0, r)
        self.assertEqual(r["legacy_workflows_not_rescanned"], 1)

    def test_edit_legacy_unsafe(self):
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main"))
        base = self.commit()
        self.write(".github/workflows/safe.yml", SAFE.replace("echo safe", "git push origin main # changed"))
        head = self.commit()
        self.assertEqual(self.audit(base, head)[0], 1)

    def test_non_workflow_outside_scope(self):
        self.write("docs/example.md", "git push origin main")
        self.commit()
        self.assertEqual(self.audit()[0], 0)

    def test_unpinned_sha_denied(self):
        code, r = self.audit(base="main")
        self.assertEqual((code, r["reason"]), (1, "invalid_commit_sha"))

    def test_symlink_denied(self):
        target = self.root / ".github/workflows/safe.yml"
        target.unlink()
        target.symlink_to("/etc/passwd")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "nonregular_workflow")

    def test_invalid_utf8(self):
        self.write(".github/workflows/safe.yml", b"name: broken\n\xff")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_encoding")

    def test_embedded_nul(self):
        self.write(".github/workflows/safe.yml", b"name: broken\x00")
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_blob")

    def test_large_blob(self):
        self.write(".github/workflows/safe.yml", SAFE + "# filler\n" * 80000)
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "invalid_workflow_blob")

    def test_missing_workflows(self):
        (self.root / ".github/workflows/safe.yml").unlink()
        self.commit()
        self.assertEqual(self.audit()[1]["reason"], "missing_workflows")

    def test_comment_is_ignored(self):
        self.write(".github/workflows/safe.yml", SAFE + "    # git push origin main\n")
        self.commit()
        self.assertEqual(self.audit()[0], 0)

    def test_no_raw_source_in_receipt(self):
        marker = "SECRET_TOKEN_SHOULD_NOT_BE_EMITTED"
        _, r = self.change("git push origin main # " + marker)
        self.assertNotIn(marker, json.dumps(r))


if __name__ == "__main__":
    unittest.main()
