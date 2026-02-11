"""
Extract 650+ songs from the chadrising.com Billboard analysis post.
Reads from local MySQL dump (raw_post_1174.txt).
Outputs to backend/data/billboard_songs.json
"""

import json
import re
import sys
from pathlib import Path
from html import unescape

DATA_DIR = Path(__file__).parent.parent / "data"
RAW_FILE = DATA_DIR / "raw_post_1174.txt"

COLOR_MAP = {
    "bright-green": "bright_green",
    "green": "green",
    "red": "red",
}


def year_to_decade(year: int) -> str:
    base = (year // 10) * 10
    return f"{base}s"


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r'<[^>]+>', '', text)
    return unescape(clean).strip()


def parse_title_artist(card_text: str):
    """
    Extract position, title, and artist from the card's title line.
    Handles many formatting variations:
      #1: "Title" - Artist
      #1: Title - Artist  (no quotes)
      #1: "Title" Artist  (no dash)
      #1: "Title" &ndash; Artist
    """
    # First strip HTML and decode entities
    clean = strip_html(card_text.split('\n')[0])  # first "line" after stripping

    # Actually, let's work with the full card but find the #N line
    # Strip all HTML first
    full_clean = strip_html(card_text)

    # Find the #N pattern
    pos_match = re.search(r'#(\d+)\s*[-:]\s*', full_clean)
    if not pos_match:
        return None, None, None

    position = int(pos_match.group(1))
    rest = full_clean[pos_match.end():]

    # Try: "Title" <separator> Artist
    # Separator: -, –, —, or just space before a capitalized word
    m = re.match(r'["\u201c](.+?)["\u201d]\s*[-\u2013\u2014/]?\s*[-\u2013\u2014]?\s*(.+?)(?:\n|$)', rest)
    if m:
        title = m.group(1).strip()
        artist = m.group(2).strip()
        # Clean artist: stop at newline or period that starts analysis
        artist = re.split(r'\n', artist)[0].strip()
        # Remove trailing period if it's part of analysis leaking in
        if len(artist) > 50:
            # Probably grabbed analysis text — try to truncate
            artist = artist[:50].rsplit(' ', 1)[0]
        return position, title, artist

    # Try: Title - Artist (no quotes)
    m = re.match(r'(.+?)\s+[-\u2013\u2014]\s+(.+?)(?:\n|$)', rest)
    if m:
        title = m.group(1).strip().strip('"').strip('\u201c\u201d')
        artist = m.group(2).strip()
        artist = re.split(r'\n', artist)[0].strip()
        return position, title, artist

    # Try: Title Artist (no separator — look for "ft.", "/", or two capitalized phrases)
    m = re.match(r'(.+?)\s+((?:ft\.|feat\.)?\s*[A-Z].+?)(?:\n|$)', rest)
    if m:
        title = m.group(1).strip().strip('"').strip('\u201c\u201d')
        artist = m.group(2).strip()
        artist = re.split(r'\n', artist)[0].strip()
        return position, title, artist

    return position, None, None


def extract_songs(content: str) -> list[dict]:
    songs = []
    current_year = None

    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check for year heading
        year_match = re.search(r'\[aw_heading\](\d{4})\[/aw_heading\]', line)
        if year_match:
            current_year = int(year_match.group(1))
            i += 1
            continue

        # Check for card start
        card_match = re.match(r'\[aw_card\s+color="([^"]+)"\](.*)', line)
        if card_match and current_year:
            color_raw = card_match.group(1)
            rubric_color = COLOR_MAP.get(color_raw, "green")

            # Collect all lines until [/aw_card]
            card_content = card_match.group(2)
            while '[/aw_card]' not in card_content and i + 1 < len(lines):
                i += 1
                card_content += '\n' + lines[i].strip()

            # Remove closing tag
            card_content = card_content.replace('[/aw_card]', '')

            # Parse song title/artist
            position, title, artist = parse_title_artist(card_content)
            if not title or not artist:
                # Debug: show what we couldn't parse
                preview = strip_html(card_content)[:80]
                print(f"  SKIP ({current_year}): Could not parse: {preview}")
                i += 1
                continue

            # Find "Why:" classification
            why_classification = None
            why_match = re.search(r'(?:Why|WHY):\s*(.+?)(?:\n|$|<)', card_content, re.IGNORECASE)
            if why_match:
                why_classification = strip_html(why_match.group(1)).strip().rstrip('.')

            # Get analysis text — everything after the title line
            analysis_text = card_content
            # Remove everything up to first closing </strong> or </b>
            analysis_text = re.sub(r'^.*?</(?:strong|b)>', '', analysis_text, count=1, flags=re.DOTALL)
            # Remove Why line
            analysis_text = re.sub(r'<(?:strong|b)>\s*(?:Why|WHY):.*?</(?:strong|b)>', '', analysis_text, flags=re.IGNORECASE)
            analysis_clean = strip_html(analysis_text).strip()
            # Also try: if no closing tag found, just take text after first newline
            if not analysis_clean:
                plain = strip_html(card_content)
                nl_pos = plain.find('\n')
                if nl_pos > 0:
                    remainder = plain[nl_pos:].strip()
                    remainder = re.sub(r'(?:Why|WHY):\s*.+', '', remainder).strip()
                    if remainder:
                        analysis_clean = remainder

            charge_summary = analysis_clean if analysis_clean else None
            contaminated = rubric_color == "red"

            songs.append({
                "title": title,
                "artist": artist,
                "year": current_year,
                "decade": year_to_decade(current_year),
                "chart_position": position,
                "rubric_color": rubric_color,
                "contaminated": contaminated,
                "contamination_note": why_classification if contaminated else None,
                "charge_summary": charge_summary,
                "why_classification": why_classification,
                "chart_source": "billboard_hot_100",
            })

        i += 1

    return songs


def main():
    if not RAW_FILE.exists():
        print(f"Raw post file not found: {RAW_FILE}")
        print("Dump it from local MySQL:")
        print('  mysql -u root -proot -h 127.0.0.1 --port=10005 '
              '-e "SELECT post_content FROM local.wp_posts WHERE ID=1174" '
              f'--raw --skip-column-names > "{RAW_FILE}"')
        sys.exit(1)

    print(f"Reading from {RAW_FILE}...")
    content = RAW_FILE.read_text(encoding="utf-8")

    print("Parsing songs...")
    songs = extract_songs(content)
    print(f"\nExtracted {len(songs)} songs")

    # Year/decade breakdown
    decades = {}
    for s in songs:
        decades[s["decade"]] = decades.get(s["decade"], 0) + 1
    for dec in sorted(decades):
        print(f"  {dec}: {decades[dec]} songs")

    # Color breakdown
    colors = {}
    for s in songs:
        colors[s["rubric_color"]] = colors.get(s["rubric_color"], 0) + 1
    for c, n in sorted(colors.items()):
        print(f"  {c}: {n}")

    # Check completeness
    by_year = {}
    for s in songs:
        by_year.setdefault(s["year"], set()).add(s["chart_position"])
    missing_count = 0
    for y in sorted(by_year):
        positions = by_year[y]
        if len(positions) < 10:
            missing = [p for p in range(1, 11) if p not in positions]
            missing_count += len(missing)
    if missing_count:
        print(f"\n{missing_count} songs could not be parsed (formatting issues in source)")

    out_path = DATA_DIR / "billboard_songs.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(songs, f, indent=2, ensure_ascii=False)

    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
