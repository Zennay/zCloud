import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zcloud_trust_debt_selector.py"

sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_trust_debt_selector as selector  # noqa: E402


class TrustDebtSelectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".github" / "workflows").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write_workflow(self, name: str, body: str) -> Path:
        path = self.root / ".github" / "workflows" / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_prefers_untrusted_pr_self_hosted_candidate(self):
        self.write_workflow(
            "pr-probe.yml",
            """name: PR probe
on:
  pull_request:
jobs:
  prove:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m unittest -v tests.test_example
""",
        )
        self.write_workflow(
            "generic-manual.yml",
            """name: Manual probe
on:
  workflow_dispatch:
jobs:
  prove:
    runs-on: self-hosted
    steps:
      - run: python3 scripts/read_only_probe.py
""",
        )

        candidates = selector.select_candidates(self.root, set())

        self.assertEqual([item.path for item in candidates], [
            ".github/workflows/pr-probe.yml",
            ".github/workflows/generic-manual.yml",
        ])
        self.assertEqual(candidates[0].priority, 0)
        self.assertIn(
            "pr_self_hosted_without_owner_same_repo_guard",
            candidates[0].findings,
        )
        self.assertIn("floating_checkout_action", candidates[0].findings)

    def test_excludes_owned_and_mutating_workflows(self):
        self.write_workflow(
            "owned.yml",
            """name: Owned
on:
  workflow_dispatch:
jobs:
  prove:
    runs-on: self-hosted
    steps:
      - run: python3 scripts/read_only_probe.py
""",
        )
        self.write_workflow(
            "mutating.yml",
            """name: Mutating
on:
  workflow_dispatch:
jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - run: sudo systemctl restart zcloud.service
""",
        )
        self.write_workflow(
            "free.yml",
            """name: Free
on:
  workflow_dispatch:
jobs:
  prove:
    runs-on: self-hosted
    steps:
      - run: python3 scripts/read_only_probe.py
""",
        )

        candidates = selector.select_candidates(
            self.root, {".github/workflows/owned.yml"}
        )

        self.assertEqual(
            [item.path for item in candidates],
            [".github/workflows/free.yml"],
        )

    def test_pinned_guarded_checkout_has_no_candidate(self):
        self.write_workflow(
            "safe.yml",
            """name: Safe
on:
  workflow_dispatch:
jobs:
  prove:
    runs-on: [self-hosted, zcloud, vps]
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          ref: ${{ github.sha }}
          persist-credentials: false
      - run: python3 scripts/zcloud_vps_runner_guard.py --json
""",
        )

        self.assertEqual(selector.select_candidates(self.root, set()), [])

    def test_owned_paths_json_accepts_array_and_object(self):
        array_path = self.root / "array.json"
        object_path = self.root / "object.json"
        array_path.write_text(json.dumps(["a.yml"]), encoding="utf-8")
        object_path.write_text(json.dumps({"paths": ["b.yml"]}), encoding="utf-8")

        self.assertEqual(
            selector._load_owned_paths(array_path, ["c.yml"]),
            {"a.yml", "c.yml"},
        )
        self.assertEqual(
            selector._load_owned_paths(object_path, []),
            {"b.yml"},
        )

    def test_cli_emits_bounded_json_and_can_require_candidate(self):
        self.write_workflow(
            "candidate.yml",
            """name: Candidate
on:
  workflow_dispatch:
jobs:
  prove:
    runs-on: self-hosted
    steps:
      - run: python3 scripts/read_only_probe.py
""",
        )

        run = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(self.root),
                "--limit",
                "1",
                "--require-candidate",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(
            payload["candidates"][0]["path"],
            ".github/workflows/candidate.yml",
        )

    def test_cli_returns_two_when_candidate_is_required_but_none_exist(self):
        run = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(self.root),
                "--require-candidate",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(run.returncode, 2, run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual(payload["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
