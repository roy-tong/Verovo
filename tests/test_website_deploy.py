import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "website-deploy.yml"


class WebsiteDeployTests(unittest.TestCase):
    def test_deploy_uses_content_checksums_not_only_timestamp_and_size(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("rsync -ac --delete", text)
        self.assertNotIn("rsync -a --delete", text)

    def test_checksum_deploy_updates_same_size_same_timestamp_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            destination = Path(tmp) / "destination"
            source.mkdir()
            destination.mkdir()
            old = b"Current CLI release: v0.17.0\n"
            new = b"Current CLI release: v0.17.1\n"
            self.assertEqual(len(old), len(new))
            source_file = source / "index.html"
            destination_file = destination / "index.html"
            source_file.write_bytes(new)
            destination_file.write_bytes(old)
            timestamp = 1_700_000_000
            os.utime(source_file, (timestamp, timestamp))
            os.utime(destination_file, (timestamp, timestamp))
            subprocess.run(
                ["rsync", "-ac", "--delete", str(source) + "/", str(destination) + "/"],
                check=True,
            )
            self.assertEqual(destination_file.read_bytes(), new)


if __name__ == "__main__":
    unittest.main()
