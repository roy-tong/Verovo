"""Root test-suite shared helpers (V-4 dedup, review 2026-11-09).

此前 load_json 在 test_audit_golden_case.py 与 test_audit_case_schema.py
逐字重复定义；统一到此处（unittest discover 以 tests/ 为 start-dir，同级
模块可直接 import）。
"""
from __future__ import annotations

import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
