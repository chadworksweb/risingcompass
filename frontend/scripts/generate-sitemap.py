"""Regenerate frontend/sitemap.xml from the top level of the frontend tree.

Top-level pages only — this file never lists individual artists, songs,
tenets, amendments, or calibration-log entries. Those would be thousands
of URLs and would need their own dynamic sitemap indexes to do well.

Included:
  - "/" (the home page, from frontend/index.html)
  - Each direct subdirectory that contains an index.html  (e.g. /search/)
  - Each *.html file sitting at the root of frontend/  (e.g. /privacy.html)
  - Every real page one level inside /charts/ (see SCANNED_SUBDIRS)

Excluded:
  - Asset directories (css / img / js / scripts / songs) — either not pages
    or don't have an index.html
  - Hidden / config files (_headers, robots.txt, sitemap.xml, .*)

Run via:
    python3 frontend/scripts/generate-sitemap.py
or automatically from deploy.sh before every deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

BASE_URL = "https://risingcompass.net"
FRONTEND_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = FRONTEND_DIR / "sitemap.xml"

# Exclusion list — directories that are pure assets or aren't meant to be
# linked pages. Kept explicit rather than inferred so adding a new asset
# folder doesn't accidentally surface in the sitemap until we say so.
EXCLUDED_DIR_NAMES = {
    "css", "img", "js", "scripts",
    "songs",  # no index.html; only hosts the individual song page template
    "sentinel",  # Sentinel Auditor Team ships DARK -- keep out of the sitemap
                 # until launch (remove this line when sentinel_auditor goes live).
}

EXCLUDED_FILE_NAMES = {
    "sitemap.xml", "robots.txt", "_headers",
}

# Curated nested pages worth indexing that the top-level scan won't find
# (the scan never recurses). Add nested page paths here as they ship.
#
# The /charts/ children are NOT listed here -- they are scanned, see
# SCANNED_SUBDIRS below.
EXTRA_PAGES = [
    "/lyrical-charger/activity/",
]

# Subdirectories whose children are scanned rather than curated.
#
# /charts/ earns this because it is the one directory the project has a written
# recipe for GROWING (CLAUDE.md "Adding a chart shell"), and "add the page to
# sitemap.xml" is a step in that recipe that gets missed: five live chart pages
# (spotify, itunes, shazam, youtube, new-music-friday) were absent for months
# while the three hand-added all-time ones were present. Curation is the right
# default for a one-off nested page; it is the wrong default for a set that
# gains a member every time a new feed is wired up.
SCANNED_SUBDIRS = ("charts",)


def _is_redirect_stub(html_path: Path) -> bool:
    """True for a noindex + meta-refresh stub left behind by a rename.

    Matched by SHAPE, not by filename, the same way build_partials.py exempts
    them from its main.css check. /charts/daily-listens/ and
    /charts/daily-downloads/ are stubs pointing at /charts/spotify/ and
    /charts/itunes/; indexing them would put two canonical-elsewhere duplicates
    in the sitemap.
    """
    try:
        head = html_path.read_text(encoding="utf-8", errors="replace")[:2000].lower()
    except OSError:
        return False
    return 'name="robots"' in head and "noindex" in head and 'http-equiv="refresh"' in head


def _scanned_subdir_pages() -> list[str]:
    """Return URL paths for every real page one level inside SCANNED_SUBDIRS."""
    urls: list[str] = []
    for parent_name in SCANNED_SUBDIRS:
        parent = FRONTEND_DIR / parent_name
        if not parent.is_dir():
            continue
        for entry in sorted(parent.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            index = entry / "index.html"
            if not index.is_file() or _is_redirect_stub(index):
                continue
            urls.append(f"/{parent_name}/{entry.name}/")
    return urls


def _top_level_pages() -> list[str]:
    """Return sorted URL paths to include in the sitemap."""
    urls: list[str] = ["/"]

    for entry in sorted(FRONTEND_DIR.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if entry.name in EXCLUDED_DIR_NAMES:
                continue
            if (entry / "index.html").is_file():
                urls.append(f"/{entry.name}/")
        elif entry.is_file():
            if entry.name in EXCLUDED_FILE_NAMES:
                continue
            if entry.name == "index.html":  # already covered by "/"
                continue
            if entry.suffix.lower() == ".html":
                urls.append(f"/{entry.name}")

    for extra in EXTRA_PAGES + _scanned_subdir_pages():
        if extra not in urls:
            urls.append(extra)

    return urls


def _priority_for(path: str) -> str:
    """Priority hint — home gets 1.0, primary pages 0.8, utility pages 0.5."""
    if path == "/":
        return "1.0"
    # Utility / form-only pages stay low.
    if path in {"/privacy.html", "/misread-submission.html"}:
        return "0.5"
    return "0.8"


def _changefreq_for(path: str) -> str:
    """Hint how often the URL updates. Homepage daily; calibration-log and
    artist / song indexes also daily (new data flowing in); mostly-static
    utility pages monthly."""
    if path == "/":
        return "daily"
    if path in {"/calibration-log/", "/artists/", "/search/", "/library/",
                "/calendar/", "/lyrical-charger/activity/"}:
        return "daily"
    if path in {"/privacy.html", "/misread-submission.html"}:
        return "monthly"
    return "weekly"


def build_sitemap() -> str:
    pages = _top_level_pages()
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        # Stylesheet filename uses .xml (not .xsl) so nginx serves it as
        # text/xml — the default mime.types mapping for .xsl varies and
        # some installs fall back to application/octet-stream, which
        # browsers refuse to apply as an XSLT transform.
        '<?xml-stylesheet type="text/xsl" href="/sitemap-style.xml"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for p in pages:
        loc = escape(f"{BASE_URL}{p}")
        out.append("  <url>")
        out.append(f"    <loc>{loc}</loc>")
        out.append(f"    <changefreq>{_changefreq_for(p)}</changefreq>")
        out.append(f"    <priority>{_priority_for(p)}</priority>")
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def main() -> int:
    new_xml = build_sitemap()
    old_xml = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
    if new_xml == old_xml:
        print(f"sitemap.xml already up to date ({len(_top_level_pages())} URLs)")
        return 0
    OUTPUT_PATH.write_text(new_xml, encoding="utf-8", newline="\n")
    print(f"sitemap.xml rewritten ({len(_top_level_pages())} URLs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
