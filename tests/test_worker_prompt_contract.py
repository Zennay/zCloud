"""Contract: what the server sends must satisfy what BOTH browser drivers validate.

Regression guard for 2026-10-01: commit 66d9aa3 dropped "Jij bent Worker X/Y." from the server prompt
while public/zcloud-worker.user.js and firefox-extension/background.js still required it, so every
driver silently refused to send and no worker generated for hours. No test covered that seam.
These tests execute the REAL validator functions from both clients against real server output.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import server
from tests._worker_env import WorkerEnv

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "worker_validators_harness.js"
PROJECTS = ("cloud", "haxlab", "raiseai", "ftmo", "supa", "ulab", "zssh")
SLOTS = ((1, 1), (1, 3), (2, 3), (3, 3))


def queue_item(project_id, slot):
    return {"queue_id": f"{project_id}-q", "project_id": project_id, "priority": "P1", "title": "Do work",
            "completion_criteria": "green evidence", "source_url": "", "worker_slot": slot}


def make_case(project_id, slot, total, **override):
    item = queue_item(project_id, slot)
    cfg = {"active": True, "assignment_ready": True, "queue_item": item,
           "global_worker_slot": slot, "global_worker_count": total,
           "prompt": server.project_worker_prompt(project_id, server.PROJECT_INDEX[project_id]["name"], "", slot, total, item)}
    cfg.update(override)
    return cfg


def run_validators(cases):
    result = subprocess.run(["node", str(HARNESS)], input=json.dumps({"cases": cases}), text=True,
                            capture_output=True, cwd=ROOT, timeout=60)
    if result.returncode != 0:
        raise AssertionError("validator harness failed: " + result.stderr[-500:])
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node is required for the JS validator contract")
class WorkerPromptContractTests(unittest.TestCase):
    def test_server_prompt_satisfies_both_real_client_validators(self):
        cases = [make_case(p, s, t) for p in PROJECTS for s, t in SLOTS]
        out = run_validators(cases)
        for case, us, ex, diag in zip(cases, out["userscript"], out["extension"], out["diagnostic"]):
            label = f"{case['queue_item']['project_id']} {case['global_worker_slot']}/{case['global_worker_count']}"
            self.assertTrue(us, f"userscript refuses server prompt for {label}: {diag}")
            self.assertTrue(ex, f"firefox extension refuses server prompt for {label}")
            self.assertEqual("", diag, label)
            self.assertEqual([], server.worker_contract_failures(case), label)

    def test_missing_worker_line_is_rejected_and_named(self):
        # exactly the 2026-10-01 regression
        case = make_case("cloud", 1, 3)
        case["prompt"] = case["prompt"].replace("Jij bent ", "")
        out = run_validators([case])
        self.assertFalse(out["userscript"][0])
        self.assertFalse(out["extension"][0])
        self.assertEqual("prompt-missing-worker-line", out["diagnostic"][0])
        self.assertEqual(["prompt-missing-worker-line"], server.worker_contract_failures(case))

    def test_python_mirror_and_js_diagnostic_agree_on_every_failure_mode(self):
        base = make_case("haxlab", 2, 3)
        cases = [
            base,
            make_case("haxlab", 2, 3, active=False),
            make_case("haxlab", 2, 3, assignment_ready=False),
            make_case("haxlab", 2, 3, queue_item=None),
            make_case("haxlab", 2, 3, global_worker_slot=0),
            make_case("haxlab", 2, 3, global_worker_count=1),
            make_case("haxlab", 2, 3, prompt=base["prompt"].replace("VPS_QUEUE_ASSIGNMENT id=", "ASSIGNMENT id=")),
            make_case("haxlab", 2, 3, prompt=""),
        ]
        out = run_validators(cases)
        for index, case in enumerate(cases):
            py = server.worker_contract_failures(case)
            js = [p for p in out["diagnostic"][index].split(",") if p]
            self.assertEqual(py, js, f"case {index}: python mirror and JS diagnostic disagree")
            self.assertEqual(not py, out["userscript"][index], f"case {index}: userscript validator vs mirror")
            # the extension filters inactive targets elsewhere, so its validator has no `active` check
            py_ext = [p for p in py if p != "not-active"]
            self.assertEqual(not py_ext, out["extension"][index], f"case {index}: extension validator vs mirror")

    def test_live_runner_worker_targets_pass_both_validators(self):
        with WorkerEnv():
            workers = [w for w in server.runner_worker_targets().values() if w["active"]]
            self.assertGreaterEqual(len(workers), 3, "fixture should allocate three active workers")
            out = run_validators(workers)
            for worker, us, ex in zip(workers, out["userscript"], out["extension"]):
                self.assertTrue(us, f"{worker['project_id']}: userscript refuses real runner-targets payload")
                self.assertTrue(ex, f"{worker['project_id']}: extension refuses real runner-targets payload")
                self.assertEqual([], server.worker_contract_failures(worker), worker["project_id"])

    def test_prompt_no_longer_promises_a_nonexistent_endpoint(self):
        self.assertNotIn("queue-update", make_case("cloud", 1, 3)["prompt"])


if __name__ == "__main__":
    unittest.main()
