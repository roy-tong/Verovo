from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "verovo"


class NativeCaseEntryTests(unittest.TestCase):
    def test_case_list_delegates_to_shared_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "cases.jsonl"
            registry.write_text(json.dumps({"case_id": "DEMO-CASE", "title": "Demo", "product": "Verovo", "status": "delivered"}) + "\n", encoding="utf-8")
            acceptance = Path(directory) / "acceptance"
            package = acceptance / "ovo_acceptance"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "casebook.py").write_text(
                "import os\nfrom pathlib import Path\n"
                "def main(argv=None):\n"
                "    print(Path(os.environ['OVO_CASE_REGISTRY']).read_text(encoding='utf-8'), end='')\n"
                "    return 0\n", encoding="utf-8")
            env = os.environ.copy()
            env["OVO_CASE_REGISTRY"] = str(registry)
            env["OVO_ACCEPTANCE_HOME"] = str(acceptance)
            result = subprocess.run([sys.executable, str(CLI), "case", "list"], env=env, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("DEMO-CASE", result.stdout)


if __name__ == "__main__":
    unittest.main()
