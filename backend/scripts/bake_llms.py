"""Generate /llms.txt and /llms-full.txt for The Rising Compass.

llms.txt is the concise, link-first index answer engines read first
(llmstxt.org). llms-full.txt is the cumulative reference: the framework in
plain language plus the live all-time chart rankings, so a model can cite the
actual data without crawling. Both are written into the static frontend root,
which nginx already serves (mime.types maps .txt -> text/plain). Re-run after a
monthly chart refresh, then commit + deploy.

    cd backend && .venv/Scripts/python.exe scripts/bake_llms.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import httpx

from app.routers.page_ssr import _FRONTEND_DIR, _fmt_streams

SITE = "https://risingcompass.net"
API_KEY = "6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b"

TIER = {"violet": "Ascended", "blue": "Elevated", "green": "Decent",
        "orange": "Degraded", "red": "Corrupted"}

CHARTS = [
    ("Most Streamed Songs of All Time", "/api/charts/alltime/streams",
     "title", lambda r: f"{_fmt_streams(r['total_streams'])} streams" if r.get("total_streams") else "",
     "/charts/streamed-all-time/"),
    ("Most Streamed Albums of All Time", "/api/charts/alltime/stream-albums",
     "album_title", lambda r: f"{_fmt_streams(r['total_streams'])} streams" if r.get("total_streams") else "",
     "/charts/most-streamed-albums/"),
    ("Best-Selling Albums of All Time (US, RIAA)", "/api/charts/alltime/albums",
     "album_title", lambda r: ", ".join(b for b in (r.get("certified_units"),
        str(r["release_year"]) if r.get("release_year") else None) if b),
     "/charts/best-selling-albums/"),
]

LLMS_TXT = """# The Rising Compass

> The Rising Compass reads the vibrational charge of the song lyrics that dominate popular culture. Every charting song is calibrated against a 58-tenet rubric and placed on a five-tier spectrum, from Ascended at the top down to Corrupted at the bottom, so anyone can see what the music people actually listen to is really saying.

The full reference, including the complete all-time chart rankings, lives at /llms-full.txt.

## Charts
- [Most Streamed Songs of All Time](https://risingcompass.net/charts/streamed-all-time/): the 100 most-streamed songs on Spotify by global lifetime streams, each one charged.
- [Most Streamed Albums of All Time](https://risingcompass.net/charts/most-streamed-albums/): the 100 most-streamed albums on Spotify, the streaming-era counterpart the sales chart leaves out.
- [Best-Selling Albums of All Time](https://risingcompass.net/charts/best-selling-albums/): the top 50 US albums by RIAA certified units.
- [Spotify (US)](https://risingcompass.net/charts/spotify/): today's Spotify Top 50 USA, charged song by song.
- [iTunes](https://risingcompass.net/charts/itunes/): the daily iTunes download chart, charged.
- [Shazam](https://risingcompass.net/charts/shazam/): the daily Shazam chart, charged.
- [YouTube](https://risingcompass.net/charts/youtube/): the daily YouTube trending chart, charged.
- [Ether Art Chart](https://risingcompass.net/ether-art-chart/): the same songs named for what the lyrics really say, with the topics pulled through the ether.

## The framework
- [Methodology](https://risingcompass.net/methodology/): how a song is read and assigned a charge.
- [Tenets](https://risingcompass.net/tenets/): the rubric every reading is built on.
- [Amendments](https://risingcompass.net/amendments/): how the framework has changed over time.

## Tools
- [Lyrical Charger](https://risingcompass.net/lyrical-charger/): paste any lyrics and get the same reading the charts use.
- [Charger Activity](https://risingcompass.net/lyrical-charger/activity/): what is moving through the charger right now.

## Explore
- [Library](https://risingcompass.net/library/): every calibrated song.
- [Artists](https://risingcompass.net/artists/): the artists in the corpus.
- [Search](https://risingcompass.net/search/): find a song or artist.
- [Calendar](https://risingcompass.net/calendar/): the daily reading over time.
- [Calibration Log](https://risingcompass.net/calibration-log/): the running record of readings.

## Participate
- [Motion Desk](https://risingcompass.net/motion-desk/): propose a change to the framework.
- [Subscribe](https://risingcompass.net/subscribe/): get the daily reading by email.
"""


def _fetch(path):
    r = httpx.get(SITE + path, headers={"X-Api-Key": API_KEY}, timeout=30)
    r.raise_for_status()
    return r.json().get("rows", [])


def _full_text():
    out = []
    out.append("# The Rising Compass: Full Reference")
    out.append("")
    out.append(
        "The Rising Compass measures the vibrational charge of the song lyrics that "
        "dominate popular culture. Every song that charts is read against a 58-tenet "
        "rubric and placed on a five-tier spectrum. The aim is to make visible what "
        "the music people actually listen to is really saying, one song at a time and "
        "at the scale of the whole culture.")
    out.append("")
    out.append("## The charge spectrum")
    out.append("")
    out.append(
        "A reading scores a song on a scale that runs from a high positive charge to a "
        "deep negative one, then names the tier it lands in:")
    out.append("")
    out.append("- Ascended (violet): the highest, most life-giving charge.")
    out.append("- Elevated (blue): a clearly positive charge.")
    out.append("- Decent (green): roughly neutral, neither lifting nor corroding.")
    out.append("- Degraded (orange): a negative charge that pulls downward.")
    out.append("- Corrupted (red): the lowest, most corrosive charge.")
    out.append("")
    out.append(
        "Instrumentals carry no lyric charge and are left uncharted. Non-music audio "
        "such as sleep or white-noise tracks is tagged and excluded the same way. The "
        "exact tenets behind a reading are documented at " + SITE + "/methodology/ and "
        + SITE + "/tenets/.")
    out.append("")
    for heading, api, title_key, metric, page in CHARTS:
        rows = _fetch(api)
        out.append(f"## {heading}")
        out.append("")
        out.append(f"Source: {SITE}{page}")
        out.append("")
        for r in rows:
            name = r.get(title_key) or ""
            artist = r.get("artist") or ""
            m = metric(r)
            if r.get("non_music"):
                tier = "Non-music"
            else:
                tier = TIER.get(r.get("rubric_color"), "uncharged")
            line = f"{r['rank']}. {name} - {artist}"
            if m:
                line += f" ({m})"
            line += f" [{tier}]"
            out.append(line)
        out.append("")
    return "\n".join(out) + "\n"


def main():
    root = Path(_FRONTEND_DIR)
    (root / "llms.txt").write_text(LLMS_TXT, encoding="utf-8", newline="\n")
    print(f"wrote llms.txt ({len(LLMS_TXT)} bytes)")
    full = _full_text()
    (root / "llms-full.txt").write_text(full, encoding="utf-8", newline="\n")
    print(f"wrote llms-full.txt ({len(full)} bytes)")


if __name__ == "__main__":
    main()
