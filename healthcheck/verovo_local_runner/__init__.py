"""Explicit local-process adapter kept outside the offline healthcheck core."""
from __future__ import annotations

import subprocess


def run_process(*, argv, cwd, env, timeout):
    try:
        completed = subprocess.run(
            argv, cwd=cwd, env=env, shell=False, capture_output=True,
            timeout=timeout, check=False)
        return {"exit_code": completed.returncode, "stdout": completed.stdout,
                "stderr": completed.stderr, "error": ""}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": None, "stdout": exc.stdout or b"",
                "stderr": exc.stderr or b"", "error": "timeout"}
