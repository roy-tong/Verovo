import unittest
from pathlib import Path


class ReleaseWorkflowTest(unittest.TestCase):
    def test_release_reads_runtime_version_and_exact_pypi_candidate(self):
        text = (Path(__file__).resolve().parents[1] /
                ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotIn("['project']['version']", text)
        self.assertGreaterEqual(
            text.count("from verovo_healthcheck import __version__"), 3)
        self.assertIn("https://pypi.org/pypi/verovo/${VER}/json", text)


if __name__ == "__main__":
    unittest.main()
