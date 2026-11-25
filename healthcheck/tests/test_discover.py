import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verovo_healthcheck import discover


class RuntimeDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_explicit_codex_discovery_filters_filename_dates(self):
        sessions = self.root / "sessions"
        sessions.mkdir()
        old = sessions / "rollout-2026-01-01T10-00-00-abc.jsonl"
        current = sessions / "rollout-2026-11-01T10-00-00-def.jsonl"
        other = sessions / "notes.jsonl"
        for path in (old, current, other):
            path.write_text("", encoding="utf-8")
        result = discover.discover(
            explicit_dir=str(sessions), since=discover.parse_date("2026-11-01"))
        self.assertEqual([str(current)], result.selected_files())
        self.assertEqual(1, len(result.runtimes))
        self.assertNotIn(str(other), result.selected_files())

    def test_empty_codex_directory_does_not_hide_claude_sessions(self):
        home = self.root / "home"
        codex = home / ".codex" / "sessions"
        claude = home / ".claude" / "projects" / "project"
        codex.mkdir(parents=True)
        claude.mkdir(parents=True)
        session = claude / "session-1.jsonl"
        session.write_text("", encoding="utf-8")
        with patch.dict(os.environ, {"HOME": str(home)}):
            result = discover.discover(scan_all=True)
        self.assertEqual([str(session)], result.selected_files())
        self.assertEqual("claude", result.primary().runtime)


if __name__ == "__main__":
    unittest.main()
