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


def iter_html_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*.html"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIR_PARTS for part in rel.parts):
            continue
        out.append(path)
    return sorted(out)


def build_once(check: bool = False) -> int:
    """Process all HTML files. Returns 0 on success, 1 if --check found drift."""
    drift = 0
    written = 0
    for path in iter_html_files():
        original = path.read_text(encoding="utf-8")
        rendered = render(original)
        if rendered == original:
            continue
        if check:
            drift += 1
            print(f"DRIFT: {path.relative_to(ROOT)}", file=sys.stderr)
        else:
            path.write_text(rendered, encoding="utf-8")
            written += 1
            print(f"updated: {path.relative_to(ROOT)}")
    if check:
        if drift:
            print(f"{drift} file(s) need rebuild — run scripts/build_partials.py", file=sys.stderr)
            return 1
        return 0
    if written == 0:
        print("partials: up to date")
    else:
        print(f"partials: {written} file(s) updated")
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
