#!/usr/bin/env python3
"""Lightweight SEO/GEO gate for the static site.

Checks, with the standard library only:
  1. sitemap URLs map to real files, use the declared site base, are not
     duplicated, and never list a page marked noindex;
  2. lastmod is neither in the future nor older than the page's last commit;
  3. canonical matches the file's own URL; JSON-LD blocks parse;
  4. internal links resolve; every indexable page has an in-body inbound link
     (sitemap discovery is not internal linking);
  5. review dates stay fresh, so stale product facts fail CI.

Normal run:
    python3 scripts/seo_check.py
Failure-injection self-test (proves the gate is not a happy-path no-op):
    python3 scripts/seo_check.py --selftest
Exit code 1 means at least one error. Warnings never fail the build.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

# Logic is duplicated across the three repos by design (no shared package yet):
# keep the logic below the config block identical, and change site differences
# only in this block.
# --- per-repository configuration -------------------------------------------
SITE_BASE = "https://roy-tong.github.io/Verovo/"
SITE_DIRNAME = "website"
MAX_REVIEW_AGE_DAYS = 120
# Files that may be absent from the sitemap (error pages, share images, ...).
SITEMAP_EXEMPT = {"404.html"}
# Pages allowed to carry <meta name="robots" content="noindex">.
ALLOW_NOINDEX: set[str] = {"assets/social-preview.html"}
# Deployment acceptance: key pages must contain these strings once live.
REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "index.html": ("26,563.25", "Synthetic sample"),
    "blog/ai-invoice-reconciliation.html": ("reconcile", "UNPROVABLE"),
    "blog/resolution-bill-check.html": ("resolutions",),
    "faq.html": ("解决量",),
}
# ---------------------------------------------------------------------------


@dataclass
class Report:
    pages: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SiteCheck:
    def __init__(
        self,
        site_dir: Path,
        site_base: str,
        repo_root: Path,
        today: date | None = None,
        max_age: int = MAX_REVIEW_AGE_DAYS,
        exempt: set[str] | None = None,
        allow_noindex: set[str] | None = None,
        required_markers: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.site_dir = site_dir.resolve()
        self.site_base = site_base
        self.repo_root = repo_root.resolve()
        self.today = today or date.today()
        self.max_age = max_age
        self.exempt = set(SITEMAP_EXEMPT if exempt is None else exempt)
        self.allow_noindex = set(ALLOW_NOINDEX if allow_noindex is None else allow_noindex)
        self.required_markers = dict(REQUIRED_MARKERS if required_markers is None else required_markers)
        self.report = Report()
        self.sitemap = self.site_dir / "sitemap.xml"
        self.info: dict[Path, dict] = {}

    # -- helpers ------------------------------------------------------------
    def error(self, message: str) -> None:
        self.report.errors.append(message)

    def warn(self, message: str) -> None:
        self.report.warnings.append(message)

    def rel_of(self, path: Path) -> str:
        return path.relative_to(self.site_dir).as_posix()

    def url_of(self, path: Path) -> str:
        rel = self.rel_of(path)
        return self.site_base if rel == "index.html" else self.site_base + rel

    def html_pages(self) -> list[Path]:
        return sorted(
            p for p in self.site_dir.rglob("*.html")
            if "_site" not in p.parts and "node_modules" not in p.parts
        )

    @staticmethod
    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def extract(pattern: str, text: str) -> list[str]:
        return [m.group(1).strip() for m in re.finditer(pattern, text, re.I | re.S)]

    def resolve_internal(self, page: Path, href: str) -> Path | None:
        """Return the file an internal href points at, None for external links."""
        value = href.strip()
        if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return None
        if value.startswith("//"):
            return None
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
            if not value.startswith(self.site_base):
                return None
            value = "/" + value[len(self.site_base):]
        value = unquote(value.split("#", 1)[0].split("?", 1)[0])
        if not value:
            return None
        if value.startswith("/"):
            prefix = urlparse(self.site_base).path
            if not value.startswith(prefix):
                return None
            target = self.site_dir / value[len(prefix):]
        else:
            target = page.parent / value
        try:
            target = target.resolve()
        except OSError:
            return None
        if self.site_dir not in target.parents and target != self.site_dir:
            return target  # outside the site: the caller reports it as broken
        if value.endswith("/"):
            target = target / "index.html"
        elif target.suffix == "":
            candidate = target.with_suffix(".html")
            target = candidate if candidate.is_file() else target / "index.html"
        return target

    def git_last_commit(self, path: Path) -> date | None:
        try:
            out = subprocess.run(
                ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(path)],
                cwd=self.repo_root, capture_output=True, text=True, check=False, timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        if not out:
            return None
        try:
            return datetime.strptime(out.splitlines()[0], "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def parse_date(value: str) -> date | None:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    # -- checks -------------------------------------------------------------
    def check_pages(self) -> None:
        pages = self.html_pages()
        self.report.pages = len(pages)
        titles: dict[str, Path] = {}

        for page in pages:
            text = self.read(page)
            rel = self.rel_of(page)
            title = (self.extract(r"<title>(.*?)</title>", text) or [""])[0]
            description = (self.extract(r'<meta\s+name="description"\s+content="(.*?)"', text) or [""])[0]
            canonical = (self.extract(r'<link\s+rel="canonical"\s+href="(.*?)"', text) or [""])[0]
            if not title:
                self.error(f"{rel}: missing <title>")
            elif title in titles:
                self.warn(f"{rel}: duplicate <title> with {self.rel_of(titles[title])}")
            else:
                titles[title] = page
            if not description:
                self.warn(f"{rel}: missing meta description")
            if not canonical:
                self.warn(f"{rel}: missing canonical")
            elif canonical != self.url_of(page):
                self.error(f"{rel}: canonical is {canonical!r}, expected {self.url_of(page)!r}")

            for index, block in enumerate(
                self.extract(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text), 1
            ):
                try:
                    payload = json.loads(block)
                except json.JSONDecodeError as exc:
                    self.error(f"{rel}: JSON-LD block {index} is not valid JSON ({exc.msg} line {exc.lineno})")
                    continue
                if not isinstance(payload, dict) or "@context" not in payload:
                    self.warn(f"{rel}: JSON-LD block {index} has no @context")

            noindex = bool(
                re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', text, re.I)
            )
            if noindex and rel not in self.allow_noindex:
                self.error(f"{rel}: noindex is not allow-listed; remove it or add to ALLOW_NOINDEX")
            for marker in self.required_markers.get(rel, ()):
                if marker not in text:
                    self.error(f"{rel}: required marker missing: {marker!r}")

            self.info[page] = {
                "title": title,
                "canonical": canonical,
                "text": text,
                "noindex": noindex,
            }

    def check_links(self) -> None:
        inbound: dict[Path, set[Path]] = {page: set() for page in self.info}
        for page, meta in self.info.items():
            rel = self.rel_of(page)
            for href in self.extract(r'<a\s[^>]*href="(.*?)"', meta["text"]):
                target = self.resolve_internal(page, href)
                if target is None:
                    continue
                if not target.is_file():
                    self.error(f"{rel}: broken internal link {href!r}")
                    continue
                if target.suffix == ".html" and target in inbound and target != page:
                    inbound[target].add(page)
        for page, sources in inbound.items():
            rel = self.rel_of(page)
            if rel in self.exempt or self.info[page]["noindex"]:
                continue
            if not sources:
                self.error(f"{rel}: no in-body link points at this page (sitemap is not internal linking)")

    def check_sitemap(self) -> None:
        if not self.sitemap.is_file():
            self.error("sitemap.xml is missing")
            return
        try:
            root = ET.parse(self.sitemap).getroot()
        except ET.ParseError as exc:
            self.error(f"sitemap.xml is not valid XML: {exc}")
            return

        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        def child(node, name):
            found = node.find("sm:" + name, namespace)
            return found if found is not None else node.find(name)

        entries = root.findall("sm:url", namespace) or root.findall("url")
        if not entries:
            self.error("sitemap.xml has no <url> entries")
            return

        listed: set[Path] = set()
        seen: set[str] = set()
        for entry in entries:
            loc_node = child(entry, "loc")
            if loc_node is None or not (loc_node.text or "").strip():
                self.error("sitemap.xml: a <url> entry has no <loc>")
                continue
            loc = loc_node.text.strip()
            if loc in seen:
                self.error(f"sitemap.xml: duplicate <loc> {loc}")
            seen.add(loc)
            if not loc.startswith(self.site_base):
                self.error(f"sitemap.xml: {loc} is outside the declared site base {self.site_base}")
                continue
            rel = loc[len(self.site_base):]
            if rel.endswith("/"):
                rel += "index.html"
            target = self.site_dir / (rel or "index.html")
            if not target.is_file():
                self.error(f"sitemap.xml: {loc} does not map to a file")
                continue
            listed.add(target)

            if target in self.info and self.info[target]["noindex"]:
                self.error(f"sitemap.xml: {loc} is marked noindex but listed in the sitemap")

            lastmod_node = child(entry, "lastmod")
            lastmod_text = (lastmod_node.text or "").strip() if lastmod_node is not None else ""
            if not lastmod_text:
                continue
            lastmod = self.parse_date(lastmod_text)
            if lastmod is None:
                self.error(f"sitemap.xml: {loc} has an unparsable lastmod {lastmod_text!r}")
                continue
            if lastmod > self.today:
                self.error(f"sitemap.xml: {loc} lastmod {lastmod} is in the future")
            committed = self.git_last_commit(target)
            if committed and lastmod < committed:
                self.error(
                    f"sitemap.xml: {loc} lastmod {lastmod} is older than the page's last commit "
                    f"({committed}); update lastmod or revert the page"
                )
            canonical = self.info.get(target, {}).get("canonical")
            if canonical and canonical != loc:
                self.error(f"sitemap.xml: {loc} does not match the page canonical {canonical!r}")

        for page in self.info:
            rel = self.rel_of(page)
            if rel in self.exempt or self.info[page]["noindex"]:
                continue
            if page not in listed:
                self.error(f"{rel}: not listed in sitemap.xml")

    def check_review_dates(self) -> None:
        limit = self.today - timedelta(days=self.max_age)
        for page, meta in self.info.items():
            for value in self.extract(r"复核日期\s*[：:]?\s*(\d{4}-\d{2}-\d{2})", meta["text"]):
                parsed = self.parse_date(value)
                if parsed is None:
                    self.error(f"{self.rel_of(page)}: unparsable review date {value!r}")
                elif parsed > self.today:
                    self.error(f"{self.rel_of(page)}: review date {value} is in the future")
                elif parsed < limit:
                    self.error(
                        f"{self.rel_of(page)}: review date {value} is older than {self.max_age} days; "
                        "re-check the page against the repo"
                    )

    def run(self) -> Report:
        if not self.site_dir.is_dir():
            self.error(f"site directory not found: {self.site_dir}")
            return self.report
        self.check_pages()
        self.check_links()
        self.check_sitemap()
        self.check_review_dates()
        return self.report


# --- failure-injection self-test -------------------------------------------
SELFTEST_BASE = "https://example.test/site/"
SELFTEST_TODAY = date(2026, 11, 15)


def _write_selftest_site(
    root: Path,
    *,
    a_exists: bool = True,
    a_canonical: str | None = None,
    index_links_a: bool = True,
    bad_jsonld: bool = False,
    noindex_a: bool = False,
    lastmod: str | None = None,
    review_date: str | None = None,
) -> Path:
    site = root / "website"
    site.mkdir(parents=True)
    a_url = SELFTEST_BASE + "a.html"
    canonical_a = a_canonical or a_url
    jsonld = "{not valid json" if bad_jsonld else json.dumps(
        {"@context": "https://schema.org", "@type": "WebSite", "name": "selftest"}
    )
    link = '<a href="a.html">a</a>' if index_links_a else ""
    review = f"<p>复核日期 {review_date}</p>" if review_date else ""
    (site / "index.html").write_text(
        "<!doctype html><html><head><title>Home</title>"
        '<meta name="description" content="home">'
        f'<link rel="canonical" href="{SELFTEST_BASE}">'
        f'<script type="application/ld+json">{jsonld}</script></head>'
        f"<body>{link}{review}</body></html>",
        encoding="utf-8",
    )
    if a_exists:
        robots = '<meta name="robots" content="noindex">' if noindex_a else ""
        (site / "a.html").write_text(
            "<!doctype html><html><head><title>A</title>"
            '<meta name="description" content="a">'
            f'<link rel="canonical" href="{canonical_a}">{robots}</head>'
            f'<body><a href="index.html">home</a></body></html>',
            encoding="utf-8",
        )
    lastmod_line = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{SELFTEST_BASE}</loc>{lastmod_line}</url>"
        f"<url><loc>{a_url}</loc>{lastmod_line}</url>"
        "</urlset>",
        encoding="utf-8",
    )
    return site


def selftest() -> int:
    cases = [
        ("valid site passes", {}, None),
        ("missing sitemap target fails", {"a_exists": False}, "does not map to a file"),
        ("canonical mismatch fails", {"a_canonical": SELFTEST_BASE + "wrong.html"}, "canonical is"),
        ("broken JSON-LD fails", {"bad_jsonld": True}, "not valid JSON"),
        ("noindex page in sitemap fails", {"noindex_a": True}, "marked noindex but listed"),
        ("orphan page fails", {"index_links_a": False}, "no in-body link"),
        ("future lastmod fails", {"lastmod": "2026-11-16"}, "is in the future"),
        ("stale review date fails", {"review_date": "2020-01-01"}, "older than"),
        ("fresh review date passes", {"review_date": "2026-11-01", "lastmod": "2026-11-15"}, None),
    ]
    failures = 0
    for name, kwargs, expected in cases:
        with tempfile.TemporaryDirectory() as tmp:
            site = _write_selftest_site(Path(tmp), **kwargs)
            report = SiteCheck(
                site, SELFTEST_BASE, Path(tmp), today=SELFTEST_TODAY,
                required_markers={},
            ).run()
            if expected is None:
                ok = not report.errors
            else:
                ok = any(expected in message for message in report.errors)
        print(f"{'PASS' if ok else 'FAIL'}  selftest: {name}")
        if not ok:
            failures += 1
            for message in report.errors:
                print(f"        {message}")
    with tempfile.TemporaryDirectory() as tmp:
        site = _write_selftest_site(Path(tmp))
        report = SiteCheck(
            site, SELFTEST_BASE, Path(tmp), today=SELFTEST_TODAY,
            required_markers={"index.html": ("definitely-not-present",)},
        ).run()
        ok = any("required marker missing" in message for message in report.errors)
    print(f"{'PASS' if ok else 'FAIL'}  selftest: missing required marker fails")
    if not ok:
        failures += 1
        for message in report.errors:
            print(f"        {message}")

    total = len(cases) + 1
    print(f"selftest: {total - failures}/{total} scenarios behaved as expected")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()

    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    check = SiteCheck(repo_root / SITE_DIRNAME, SITE_BASE, repo_root)
    report = check.run()
    for message in report.warnings:
        print(f"WARN  {message}")
    for message in report.errors:
        print(f"FAIL  {message}")
    print(f"seo_check: {report.pages} pages, {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
