import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zcloud_deploy_artifact_url_boundary_20261008.py"
spec = importlib.util.spec_from_file_location("artifact_boundary", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ArtifactBoundaryTests(unittest.TestCase):
    def test_valid_host_still_non_authorizing(self):
        result = module.inspect("https://api.github.com/repos/Zennay/zCloud/actions/artifacts/123")
        self.assertEqual(result["status"], "review_only")
        self.assertFalse(result["deploy_authorized"])
        self.assertFalse(result["recovery_authorized"])
        self.assertFalse(result["mutation_performed"])

    def test_valid_web_artifact_path_remains_non_authorizing(self):
        result = module.inspect("https://github.com/Zennay/zCloud/actions/runs/123/artifacts/456")
        self.assertEqual(result["status"], "review_only")
        self.assertFalse(result["deploy_authorized"])

    def test_rejects_untrusted_urls(self):
        for url in (
            "http://github.com/a", "https://github.com.evil.invalid/x",
            "https://evil.invalid@github.com/x", "https://github.com@evil.invalid/x",
            "https://127.0.0.1/x", "https://github.com:444/x",
            "https://github.com/a#fragment", "file:///etc/passwd",
            "https://github.com\\@evil.invalid/x", None, "", 123,
            "https://github.com:bad/x", "https://github.com//evil.invalid/x",
            "https://github.com/x\ny",
            "https://github.com/Zennay/zCloud/actions/runs/123",
            "https://api.github.com/repos/Other/zCloud/actions/artifacts/123",
            "https://api.github.com/repos/Zennay/zCloud/actions/artifacts/0",
            "https://api.github.com/repos/Zennay/zCloud/actions/artifacts/123?token=secret",
            "https://api.github.com/repos/Zennay/zCloud/actions/artifacts/123/extra",
        ):
            with self.subTest(url=url):
                self.assertEqual(module.inspect(url)["status"], "deny")

if __name__ == "__main__":
    unittest.main()
