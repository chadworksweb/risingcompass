#!/usr/bin/env python3
"""Build-time partial inclusion for the static frontend.

Walks every public-facing *.html file under frontend/, finds blocks delimited
by `<!-- INCLUDE:name -->` ... `<!-- /INCLUDE:name -->` markers, and replaces
the content between them with the contents of `frontend/partials/<name>.html`.
Idempotent: running twice is a no-op.

Usage:
  python scripts/build_partials.py            # one-shot
  python scripts/build_partials.py --watch    # rebuild on change (polling)
  python scripts/build_partials.py --check    # exit non-zero if anything would change

Skips:
  - Anything inside frontend/partials/ (the source of truth)
  - Anything inside frontend/scripts/
  - Anything inside backend/ (admin Jinja templates live there, untouched)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTIALS_DIR = ROOT / "partials"
SKIP_DIR_PARTS = {"partials", "scripts", "node_modules", ".git"}

INCLUDE_RE = re.compile(
    r"(<!--\s*INCLUDE:([\w-]+)\s*-->)(.*?)(<!--\s*/INCLUDE:\2\s*-->)",
    re.DOTALL,
)

# Partials that depend on global stylesheet rules in /css/main.css. Pages
# that use any of these MUST load main.css or the partial renders unstyled.
# Caught us once already (commit a2a318b on 2026-04-25 broke /tenets/,
# /amendments/, /lyrical-charger/ for ~24h). The check below makes sure we
# never ship an HTML file that uses one of these partials without the link.
PARTIALS_REQUIRING_MAIN_CSS = {"header", "footer", "support-callout"}
# Require the absolute /css/main.css form. Relative variants (`../css/main.css`
# or `css/main.css`) work only when the URL sits at one specific path depth;
# add a stray trailing slash anywhere upstream and they 404 silently. Tripped
# us once on /songs/<slug>/ on 2026-04-26. Absolute path = depth-independent.
MAIN_CSS_RE = re.compile(
    r"""<link[^>]+href=["']/css/main\.css(?:\?[^"']*)?["']""",
)

# The only pages allowed to skip main.css. Each one is deliberately isolated
# from the site sheet; anything else missing main.css is a bug, not a choice.
#   cards/  -- the charge-card render harness. It is screenshotted at fixed
#              pixel sizes, so inheriting site typography would change the
#              output image. noindex, never linked.
PAGES_EXEMPT_FROM_MAIN_CSS = ("cards/",)

# A noindex meta-refresh stub is not a page anyone reads: it hands the browser
# straight on. Loading the whole site stylesheet to style a link that shows for
# a few milliseconds is the wrong trade. Detected by what the file IS rather
# than by filename, so the exemption cannot go stale when files move or when a
# stub is later replaced by a real page.
REDIRECT_STUB_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']refresh["']""", re.IGNORECASE)
NOINDEX_RE = re.compile(
    r"""<meta[^>]+name=["']robots["'][^>]+noindex""", re.IGNORECASE)


# EVERY same-origin script/stylesheet must carry a `?v=` cache-bust.
#
# risingcompass.net sits behind Cloudflare with a 4h edge TTL on static assets.
# The page HTML is SSR'd and serves DYNAMIC, so a deploy updates the markup
# immediately while the edge keeps handing out the OLD JS and CSS. That is not a
# theoretical race: on 2026-08-12 new song.html markup shipped against JULY
# copies of songs.css and songs.js (cf-cache-status: HIT, Last-Modified
# 2026-07-07). The cover-art wrap rendered with nothing to unhide it and the
# share menu ran old handlers.
#
# CLAUDE.md has said "bump ?v= on any edit" ever since, and the rule was followed
# for the three files it named while ~30 shared assets kept shipping with no
# version at all -- including /js/auth.js and /js/api.js, which nearly every page
# loads. A rule people have to remember is a rule that decays. This is the same
# shape of gate as the main.css check above, for the same reason.
ASSET_TAG_RE = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)=["']([^"'>?:]+\.(?:js|css))(\?[^"'>]*)?["']""",
    re.IGNORECASE,
)
# Excluding the leading `/` from the pattern above is deliberate: three
# stylesheets were referenced RELATIVELY (`href="style.css"`), so an
# absolute-only pattern reported them as absent rather than as unversioned and
# two of them shipped with no cache-bust at all. Requiring `[^:]` in the path
# keeps `https://fonts.googleapis.com/...` and `data:` out.
#
# A relative reference is itself a violation, for the reason MAIN_CSS_RE already
# documents: it resolves against whatever depth the URL happens to sit at, so a
# stray trailing slash 404s it silently. /lyrical-charger/ and /topic-trends/ are
# exactly the directory URLs where that bites.
RELATIVE_ASSET_RE = re.compile(
    r"""<(?:script|link)\b[^>]*?\b(?:src|href)=["'](?!/|https?:|data:|//)([^"'>?:]+\.(?:js|css))""",
    re.IGNORECASE,
)


def find_cachebust_violations(path: Path, content: str) -> list[str]:
    """Same-origin .js/.css must carry ?v=, and must not be loaded twice.

    The duplicate check rides along because it is the same scan and the same
    class of bug: /methodology/calibrator-before-after/ loaded /js/consent.js
    twice, once from the baked footer partial with ?v= and once hand-added below
    the include marker without one, so the consent bar initialised twice and the
    unversioned copy was whatever the edge happened to be holding. RC uses no
    preload/modulepreload anywhere, so a repeated path is always a mistake.
    """
    rel = path.relative_to(ROOT).as_posix()
    if is_redirect_stub(content):
        return []
    out: list[str] = []
    seen: dict[str, int] = {}
    for m in ASSET_TAG_RE.finditer(content):
        asset, query = m.group(1), m.group(2) or ""
        seen[asset] = seen.get(asset, 0) + 1
        if "v=" not in query:
            out.append(f"{rel}: {asset} has no ?v= cache-bust")
    for asset, count in seen.items():
        if count > 1:
            out.append(f"{rel}: loads {asset} {count} times")
    for m in RELATIVE_ASSET_RE.finditer(content):
        out.append(f"{rel}: {m.group(1)} is a RELATIVE asset path -- use /the/absolute/one")
    return out


def is_redirect_stub(content: str) -> bool:
    return bool(REDIRECT_STUB_RE.search(content) and NOINDEX_RE.search(content))


def load_partial(name: str) -> str:
    path = PARTIALS_DIR / f"{name}.html"
    if not path.exists():
        raise FileNotFoundError(f"Missing partial: {path}")
    return path.read_text(encoding="utf-8").rstrip("\n")


def render(content: str) -> str:
    def repl(m: re.Match) -> str:
        open_tag, name, _existing, close_tag = m.groups()
        partial = load_partial(name)
        return f"{open_tag}\n{partial}\n{close_tag}"

    return INCLUDE_RE.sub(repl, content)


def find_main_css_violations(path: Path, content: str) -> str | None:
    """EVERY public page must load /css/main.css.

    This used to fire only for pages using the shared header/footer partials.
    That was too narrow: main.css is also the only place the global anchor
    colour is defined, so a page that skipped it rendered the browser's blue
    and visited-purple links. A page could pass this check and still ship with
    default links, which is exactly what kept happening on new pages.

    So the gate is now universal, with a short allowlist for pages that are
    deliberately unstyled by the site sheet.

    Returns a one-line violation string, or None if the file is fine.
    """
    rel = path.relative_to(ROOT).as_posix()
    if any(rel.startswith(p) for p in PAGES_EXEMPT_FROM_MAIN_CSS):
        return None
    if is_redirect_stub(content):
        return None
    if MAIN_CSS_RE.search(content):
        return None
    used = sorted(
        m.group(2) for m in INCLUDE_RE.finditer(content)
        if m.group(2) in PARTIALS_REQUIRING_MAIN_CSS
    )
    if used:
        parts = ", ".join(used)
        return f"{rel}: uses shared partial(s) [{parts}] but does not load /css/main.css"
    return (f"{rel}: does not load /css/main.css, so its links render in the "
            f"browser's default blue and visited-purple")

def iter_html_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*.html"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIR_PARTS for part in rel.parts):
            continue
        out.append(path)
    return sorted(out)


def build_once(check: bool = False) -> int:
    """Process all HTML files. Returns 0 on success, 1 if --check found drift
    or a main.css gate violation."""
    drift = 0
    written = 0
    violations: list[str] = []
    for path in iter_html_files():
        original = path.read_text(encoding="utf-8")
        rendered = render(original)
        v = find_main_css_violations(path, rendered)
        if v:
            violations.append(v)
        violations.extend(find_cachebust_violations(path, rendered))
        if rendered == original:
            continue
        if check:
            drift += 1
            print(f"DRIFT: {path.relative_to(ROOT)}", file=sys.stderr)
        else:
            path.write_text(rendered, encoding="utf-8")
            written += 1
            print(f"updated: {path.relative_to(ROOT)}")
    for v in violations:
        print(f"VIOLATION: {v}", file=sys.stderr)
    if check:
        if drift:
            print(f"{drift} file(s) need rebuild — run scripts/build_partials.py", file=sys.stderr)
        if violations:
            print(f"{len(violations)} gate violation(s) -- see VIOLATION lines above", file=sys.stderr)
        return 1 if (drift or violations) else 0
    if written == 0:
        print("partials: up to date")
    else:
        print(f"partials: {written} file(s) updated")
    if violations:
        print(f"{len(violations)} gate violation(s) -- see VIOLATION lines above", file=sys.stderr)
        return 1
    return 0


def watch(interval: float = 1.0) -> int:
    """Poll partials + html files for mtime changes and rebuild on any."""
    print(f"watching {ROOT} (interval {interval}s) — Ctrl+C to stop")
    last_seen: dict[Path, float] = {}
    try:
        while True:
            changed = False
            sources = list(PARTIALS_DIR.glob("*.html")) + iter_html_files()
            for p in sources:
                try:
                    m = p.stat().st_mtime
                except FileNotFoundError:
                    continue
                if last_seen.get(p) != m:
                    last_seen[p] = m
                    changed = True
            if changed:
                build_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="poll for changes and rebuild")
    ap.add_argument("--check", action="store_true", help="exit 1 if any file needs rebuild")
    args = ap.parse_args()
    if args.watch and args.check:
        ap.error("--watch and --check are mutually exclusive")
    if args.watch:
        return watch()
    return build_once(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
