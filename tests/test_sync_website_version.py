import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_website_version.py"


class SyncWebsiteVersionTests(unittest.TestCase):
    def load_module(self):
        self.assertTrue(SCRIPT.is_file(), "website version sync script is missing")
        spec = importlib.util.spec_from_file_location("sync_website_version_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_replaces_both_release_labels_and_is_idempotent(self):
        module = self.load_module()
        source = (
            '<strong data-release-version="cli">old</strong>'
            '<span class="x" data-release-version="v-pypi">old</span>'
        )
        expected = (
            '<strong data-release-version="cli">Current CLI release: v1.2.3</strong>'
            '<span class="x" data-release-version="v-pypi">v1.2.3 · PyPI</span>'
        )
        self.assertEqual(expected, module.sync_text(source, "1.2.3"))
        self.assertEqual(expected, module.sync_text(expected, "1.2.3"))

    def test_prerelease_version_can_be_synchronized(self):
        module = self.load_module()
        source = (
            '<strong data-release-version="cli">old</strong>'
            '<span data-release-version="v-pypi">old</span>'
        )
        updated = module.sync_text(source, "1.2.3rc2")
        self.assertIn("Current CLI release: v1.2.3rc2", updated)
        self.assertIn("v1.2.3rc2 · PyPI", updated)

    def test_repository_labels_match_package_version(self):
        version = self.load_module().package_version()
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"], cwd=ROOT,
            capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn(version, completed.stdout)


if __name__ == "__main__":
    unittest.main()
