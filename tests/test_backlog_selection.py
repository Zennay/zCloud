import tempfile
import unittest
from pathlib import Path

from scripts import zcloud_backlog_selection as selection


def c(cid, impact, risk, **overrides):
    row = {
        "id": cid,
        "impact": impact,
        "risk": risk,
        "executable": True,
        "claimed": False,
        "human_gate": False,
        "conflict": False,
    }
    row.update(overrides)
    return row


class BacklogSelectionTests(unittest.TestCase):
    def choose(self, *rows):
        return selection.select({"schema_version": 1, "candidates": list(rows)})

    def test_highest_impact_risk_ratio_wins(self):
        result = self.choose(c("a", 5, 2), c("b", 4, 1), c("c", 5, 5))
        self.assertEqual("SELECTED", result["decision"])
        self.assertEqual("b", result["selected_id"])
        self.assertEqual(
            {"impact": 4, "risk": 1, "ratio_numerator": 4, "ratio_denominator": 1},
            result["selected_score"],
        )

    def test_equal_ratio_prefers_higher_impact_then_lower_risk_then_id(self):
        result = self.choose(c("z", 2, 1), c("b", 4, 2), c("a", 4, 2))
        self.assertEqual("a", result["selected_id"])

    def test_ineligible_candidates_never_win(self):
        result = self.choose(
            c("claimed", 5, 1, claimed=True),
            c("human", 5, 1, human_gate=True),
            c("conflict", 5, 1, conflict=True),
            c("notexec", 5, 1, executable=False),
            c("safe", 2, 2),
        )
        self.assertEqual("safe", result["selected_id"])
        self.assertEqual(
            {
                "active_conflict": 1,
                "already_claimed": 1,
                "human_gate": 1,
                "not_executable": 1,
            },
            result["excluded_counts"],
        )

    def test_no_eligible_returns_bounded_noop(self):
        result = self.choose(c("human", 5, 1, human_gate=True))
        self.assertEqual("NO_ELIGIBLE", result["decision"])
        self.assertIsNone(result["selected_id"])
        self.assertIsNone(result["selected_score"])

    def test_bool_float_and_string_scores_fail_closed(self):
        for value in (True, 1.0, "5", 0, 6):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.choose(c("bad", value, 1))

    def test_non_boolean_gate_fields_fail_closed(self):
        for field in ("executable", "claimed", "human_gate", "conflict"):
            row = c("bad", 3, 1)
            row[field] = 1
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.choose(row)

    def test_unknown_fields_are_rejected(self):
        for field in ("prompt", "patch", "command", "apply", "approved", "raw_text"):
            row = c("bad", 3, 1)
            row[field] = "hidden"
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.choose(row)

    def test_duplicate_or_noncanonical_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            self.choose(c("dup", 3, 1), c("dup", 4, 1))
        with self.assertRaises(ValueError):
            self.choose(c("../bad", 3, 1))
        with self.assertRaises(ValueError):
            self.choose(c("Upper", 3, 1))

    def test_file_input_is_bounded_and_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="zcloud-backlog-selection-") as tmp:
            root = Path(tmp)
            good = root / "good.json"
            good.write_bytes(b"{}")
            self.assertEqual(b"{}", selection._load_bytes(good))

            large = root / "large.json"
            large.write_bytes(b"x" * (selection.MAX_INPUT_BYTES + 1))
            with self.assertRaises(ValueError):
                selection._load_bytes(large)

            link = root / "link.json"
            link.symlink_to(good)
            with self.assertRaises(ValueError):
                selection._load_bytes(link)

    def test_schema_shape_is_exact(self):
        with self.assertRaises(ValueError):
            selection.select({"schema_version": 1, "candidates": [c("a", 1, 1)], "extra": True})
        with self.assertRaises(ValueError):
            selection.select({"schema_version": True, "candidates": [c("a", 1, 1)]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
