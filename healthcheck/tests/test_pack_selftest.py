import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

from verovo_healthcheck import pack

ROOT = Path(__file__).resolve().parents[2]


class PackSelftestTest(unittest.TestCase):
    def test_pack_selftest_passes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, pack.main(["selftest"]))

    def test_standalone_runner_delegates_to_the_packaged_implementation(self):
        spec = importlib.util.spec_from_file_location(
            "standalone_run_pack", ROOT / "conformance" / "pack" / "run_pack.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(pack.main, module.main)

    def test_pack_registry_matches_standalone_runner(self):
        packaged = json.loads(
            (ROOT / "healthcheck" / "verovo_healthcheck" / "pack_invariants.json")
            .read_text(encoding="utf-8"))
        standalone = json.loads(
            (ROOT / "conformance" / "pack" / "invariants.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(packaged, standalone)


if __name__ == "__main__":
    unittest.main()
