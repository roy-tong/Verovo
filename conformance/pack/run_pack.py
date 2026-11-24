#!/usr/bin/env python3
"""Standalone shim for the embedded Verovo conformance pack.

`conformance/pack/run_pack.py` and the packaged `verovo conformance`
command share one implementation: `healthcheck/verovo_healthcheck/pack.py`.
This wrapper keeps the repository-relative entry point stable without
maintaining a second copy of the pack logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "healthcheck"))

from verovo_healthcheck.pack import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
