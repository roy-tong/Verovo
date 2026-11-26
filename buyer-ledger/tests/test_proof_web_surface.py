from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProofWebSurfaceTest(unittest.TestCase):
    def test_replayable_period_page_has_proof_and_boundaries(self):
        page = ROOT.parent / "website" / "examples" / "replayable-period.html"
        self.assertTrue(page.is_file())
        text = page.read_text(encoding="utf-8")
        for token in ("Intake", "Recompute", "Review", "Decide", "Delivery", "UNKNOWN",
                      "UNPROVEN", "reproduce build", "reproduce verify", "template.json",
                      "Synthetic"):
            self.assertIn(token, text)
        self.assertNotIn("<script", text)
        self.assertIn("../styles.css", text)

    def test_home_links_family_story_and_replayable_period(self):
        text = (ROOT.parent / "website" / "index.html").read_text(encoding="utf-8")
        destination = "https://roy-tong.github.io/ovo/verovo/"
        self.assertIn(f'http-equiv="refresh" content="0;url={destination}"', text)
        self.assertIn("examples/replayable-period.html", text)


if __name__ == "__main__":
    unittest.main()
