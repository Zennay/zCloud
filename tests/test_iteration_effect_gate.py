import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_iteration_effect_gate as policy


REV_A = "a" * 40
REV_B = "b" * 40


def measurement(
    *,
    before=10,
    after=12,
    desired_direction="increase",
    validation_outcome="passed",
):
    return {
        "metric_id": "throughput",
        "before": before,
        "after": after,
        "desired_direction": desired_direction,
        "validation_outcome": validation_outcome,
    }


class IterationEffectGateTests(unittest.TestCase):
    def evaluate(self, iterations):
        return policy.evaluate_payload({"iterations": iterations})

    def test_no_prior_iteration_allows_first_iteration(self):
        result = self.evaluate([])
        self.assertEqual("NEXT_ITERATION_ALLOWED", result["decision"])

    def test_completed_iteration_without_measurement_blocks_next(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": None,
        }])
        self.assertEqual("MEASURE_EFFECT_REQUIRED", result["decision"])

    def test_measured_improvement_allows_next(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": measurement(),
        }])
        self.assertEqual("NEXT_ITERATION_ALLOWED", result["decision"])
        self.assertEqual("improved", result["effect_outcome"])

    def test_measured_neutral_effect_is_still_measured(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": measurement(after=10),
        }])
        self.assertEqual("NEXT_ITERATION_ALLOWED", result["decision"])

    def test_regression_requires_remediation_or_rollback(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": measurement(after=8),
        }])
        self.assertEqual("REMEDIATE_OR_ROLLBACK_REQUIRED", result["decision"])
        self.assertEqual("measured_regression", result["reason"])

    def test_decrease_direction_derives_improvement(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": measurement(before=10, after=8, desired_direction="decrease"),
        }])
        self.assertEqual("NEXT_ITERATION_ALLOWED", result["decision"])
        self.assertEqual("improved", result["effect_outcome"])

    def test_caller_cannot_self_declare_effect_outcome(self):
        with self.assertRaises(policy.EffectGateError):
            self.evaluate([{
                "iteration_id": "iter-1",
                "revision": REV_A,
                "state": "completed",
                "measurement": {
                    **measurement(before=10, after=8),
                    "effect_outcome": "improved",
                },
            }])

    def test_failed_validation_blocks_even_if_metric_improved(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "completed",
            "measurement": measurement(validation_outcome="failed"),
        }])
        self.assertEqual("REMEDIATE_OR_ROLLBACK_REQUIRED", result["decision"])
        self.assertEqual("validation_failed", result["reason"])

    def test_rollback_releases_gate(self):
        result = self.evaluate([{
            "iteration_id": "iter-1",
            "revision": REV_A,
            "state": "rolled_back",
            "measurement": measurement(after=8),
        }])
        self.assertEqual("NEXT_ITERATION_ALLOWED", result["decision"])
        self.assertEqual("prior_iteration_rolled_back", result["reason"])

    def test_duplicate_iteration_ids_are_rejected(self):
        with self.assertRaises(policy.EffectGateError):
            self.evaluate([
                {
                    "iteration_id": "iter-1",
                    "revision": REV_A,
                    "state": "completed",
                    "measurement": measurement(),
                },
                {
                    "iteration_id": "iter-1",
                    "revision": REV_B,
                    "state": "completed",
                    "measurement": measurement(),
                },
            ])

    def test_raw_text_fields_are_rejected(self):
        with self.assertRaises(policy.EffectGateError):
            self.evaluate([{
                "iteration_id": "iter-1",
                "revision": REV_A,
                "state": "completed",
                "measurement": {
                    **measurement(),
                    "notes": "raw diagnostic detail",
                },
            }])

    def test_non_finite_metrics_are_rejected(self):
        with self.assertRaises(policy.EffectGateError):
            self.evaluate([{
                "iteration_id": "iter-1",
                "revision": REV_A,
                "state": "completed",
                "measurement": measurement(after=float("nan")),
            }])

    def test_history_is_bounded(self):
        with self.assertRaises(policy.EffectGateError):
            self.evaluate([
                {
                    "iteration_id": f"iter-{i}",
                    "revision": REV_A,
                    "state": "completed",
                    "measurement": measurement(),
                }
                for i in range(policy.MAX_HISTORY + 1)
            ])

    def test_cli_fail_closed_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effect.json"
            path.write_text(json.dumps({"iterations": [{
                "iteration_id": "iter-1",
                "revision": REV_A,
                "state": "completed",
                "measurement": None,
            }]}), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/zcloud_iteration_effect_gate.py",
                    "--input",
                    str(path),
                    "--require-next-allowed",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(1, proc.returncode)
            result = json.loads(proc.stdout)
            self.assertEqual("MEASURE_EFFECT_REQUIRED", result["decision"])
            self.assertNotIn(REV_A, proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
