import unittest
from pathlib import Path

from verovo_healthcheck import __version__


class ReleaseMetadataTest(unittest.TestCase):
    def test_distribution_version_comes_from_runtime_package(self):
        project = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8")
        self.assertIn('dynamic = ["version"]', project)
        self.assertIn('version = {attr = "verovo_healthcheck.__version__"}', project)
        self.assertNotIn('\nversion = "', project)
        self.assertTrue(__version__)


if __name__ == "__main__":
    unittest.main()
