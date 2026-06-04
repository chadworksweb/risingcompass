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
    """Pages that include shared header/footer partials must also load
    /css/main.css (the stylesheet is where .rc-header / .site-footer / etc.
    live). Returns a one-line violation string, or None if the file is fine."""
    used = set()
    for m in INCLUDE_RE.finditer(content):
        name = m.group(2)
        if name in PARTIALS_REQUIRING_MAIN_CSS:
            used.add(name)
    if not used:
        return None
    if MAIN_CSS_RE.search(content):
        return None
    parts = ", ".join(sorted(used))
    return f"{path.relative_to(ROOT)}: uses shared partial(s) [{parts}] but does not load /css/main.css"


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
            print(f"{len(violations)} file(s) use shared partials without loading /css/main.css", file=sys.stderr)
        return 1 if (drift or violations) else 0
    if written == 0:
        print("partials: up to date")
    else:
        print(f"partials: {written} file(s) updated")
    if violations:
        print(f"{len(violations)} file(s) use shared partials without loading /css/main.css", file=sys.stderr)
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
