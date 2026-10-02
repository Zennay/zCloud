import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lightup.redaction import redact_text


class RedactionTests(unittest.TestCase):
    def test_bearer_token_is_redacted(self):
        value = "Authorization: Bearer abc.def.ghi"
        output = redact_text(value)
        self.assertNotIn("abc.def.ghi", output)
        self.assertIn("[REDACTED]", output)

    def test_common_secret_assignment_is_redacted(self):
        output = redact_text("api_key=supersecret")
        self.assertNotIn("supersecret", output)


if __name__ == "__main__":
    unittest.main()
