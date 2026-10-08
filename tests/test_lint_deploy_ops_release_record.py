import unittest
from scripts.lint_deploy_ops_release_record import validate

VALID = """#580 #1089 PWQ-258 HOLD main SHA PR-less exact-head
- mutation_performed: false
- merge_authorized: false
- deploy_authorized: false
"""

class DecisionRecordContract(unittest.TestCase):
    def test_defaults_pass(self):
        self.assertEqual(validate(VALID), [])

    def test_missing_gate_denied(self):
        self.assertTrue(validate(VALID.replace("#1089", "")))

    def test_release_authorization_denied(self):
        for flag in ("mutation_performed", "merge_authorized", "deploy_authorized"):
            with self.subTest(flag=flag):
                self.assertTrue(validate(VALID.replace(f"{flag}: false", f"{flag}: true")))

    def test_release_disposition_denied(self):
        self.assertTrue(validate(VALID + "- Gate disposition: GO\n"))

if __name__ == "__main__":
    unittest.main()
