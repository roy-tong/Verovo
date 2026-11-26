#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "healthcheck/pyproject.toml"
INDEX = ROOT / "website/index.html"
LABELS = {
    "cli": "Current CLI release: v{version}",
    "v-pypi": "v{version} · PyPI",
}


def package_version(path: Path = VERSION_FILE) -> str:
    project = path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\'](\d+\.\d+\.\d+(?:rc\d+)?)["\']\s*$',
                      project, re.M)
    if match:
        return match.group(1)
    dynamic = re.search(
        r'^version\s*=\s*\{\s*attr\s*=\s*["\']([\w.]+)["\']\s*\}\s*$',
        project, re.M)
    if dynamic:
        module = path.parent.joinpath(*dynamic.group(1).split(".")[:-1])
        module = module / "__init__.py" if module.is_dir() else module.with_suffix(".py")
        match = re.search(r'^__version__\s*=\s*["\'](\d+\.\d+\.\d+(?:rc\d+)?)["\']\s*$',
                          module.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    raise ValueError("healthcheck package has no semantic runtime version")


def sync_text(text: str, version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+)?", version):
        raise ValueError("version must be MAJOR.MINOR.PATCH with an optional rcN suffix")
    updated = text
    for kind, template in LABELS.items():
        pattern = re.compile(
            r'(<[^>]+data-release-version=["\']' + re.escape(kind) +
            r'["\'][^>]*>).*?(</(?:strong|span)>)', re.I | re.S)
        replacement = lambda match: match.group(1) + template.format(version=version) + match.group(2)
        updated, count = pattern.subn(replacement, updated)
        if count != 1:
            raise ValueError("website/index.html must contain exactly one %s release label" % kind)
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize static website release labels with package metadata")
    parser.add_argument("--check", action="store_true", help="fail instead of writing when labels are stale")
    args = parser.parse_args(argv)
    version = package_version()
    original = INDEX.read_text(encoding="utf-8")
    updated = sync_text(original, version)
    if args.check:
        if updated != original:
            print("FAIL website release labels are stale; run python3 scripts/sync_website_version.py", file=sys.stderr)
            return 1
        print("website release labels match v%s" % version)
        return 0
    if updated != original:
        INDEX.write_text(updated, encoding="utf-8")
        print("updated website release labels to v%s" % version)
    else:
        print("website release labels already match v%s" % version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
