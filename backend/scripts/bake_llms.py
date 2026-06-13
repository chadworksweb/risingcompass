"""Generate /llms.txt and /llms-full.txt for The Rising Compass.

llms.txt is the concise, link-first index answer engines read first
(llmstxt.org). llms-full.txt is the framework in plain language. Both are
written into the static frontend root, which nginx already serves (mime.types
maps .txt -> text/plain).

    cd backend && .venv/Scripts/python.exe scripts/bake_llms.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from app.routers.page_ssr import _FRONTEND_DIR

SITE = "https://risingcompass.net"

LLMS_TXT = """# The Rising Compass

> The Rising Compass reads the vibrational charge of the song lyrics that dominate popular culture. Every charting song is calibrated against a 58-tenet rubric and placed on a five-tier spectrum, from Ascended at the top down to Corrupted at the bottom, so anyone can see what the music people actually listen to is really saying.

A fuller description of the framework lives at /llms-full.txt.

## Charts
- [Most Streamed Songs of All Time](https://risingcompass.net/charts/streamed-all-time/): the 100 most-streamed songs on Spotify by global lifetime streams, each one charged.
- [Most Streamed Albums of All Time](https://risingcompass.net/charts/most-streamed-albums/): the 100 most-streamed albums on Spotify, the streaming-era counterpart the sales chart leaves out.
- [Best-Selling Albums of All Time](https://risingcompass.net/charts/best-selling-albums/): the top 50 US albums by RIAA certified units.
- [Spotify (US)](https://risingcompass.net/charts/spotify/): today's Spotify Top 50 USA, charged song by song.
- [iTunes](https://risingcompass.net/charts/itunes/): the daily iTunes download chart, charged.
- [Shazam](https://risingcompass.net/charts/shazam/): the daily Shazam chart, charged.
- [YouTube](https://risingcompass.net/charts/youtube/): the daily YouTube trending chart, charged.
- [New Music Friday](https://risingcompass.net/charts/new-music-friday/): Spotify's weekly New Music Friday USA new releases, charged song by song.
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

LLMS_FULL = f"""# The Rising Compass: Full Reference

The Rising Compass measures the vibrational charge of the song lyrics that dominate popular culture. Every song that charts is read against a 58-tenet rubric and placed on a five-tier spectrum. The aim is to make visible what the music people actually listen to is really saying, one song at a time and at the scale of the whole culture.

## The charge spectrum

A reading scores a song on a scale that runs from a high positive charge to a deep negative one, then names the tier it lands in:

- Ascended (violet): the highest, most life-giving charge.
- Elevated (blue): a clearly positive charge.
- Decent (green): roughly neutral, neither lifting nor corroding.
- Degraded (orange): a negative charge that pulls downward.
- Corrupted (red): the lowest, most corrosive charge.

Instrumentals carry no lyric charge and are left uncharted. Non-music audio such as sleep or white-noise tracks is tagged and excluded the same way. The exact tenets behind a reading are documented at {SITE}/methodology/ and {SITE}/tenets/.

## Where to read more

- Methodology: {SITE}/methodology/
- Tenets: {SITE}/tenets/
- The charts (songs, albums, daily sources): {SITE}/ (see /llms.txt for the full list)
"""


def main():
    root = Path(_FRONTEND_DIR)
    (root / "llms.txt").write_text(LLMS_TXT, encoding="utf-8", newline="\n")
    print(f"wrote llms.txt ({len(LLMS_TXT)} bytes)")
    (root / "llms-full.txt").write_text(LLMS_FULL, encoding="utf-8", newline="\n")
    print(f"wrote llms-full.txt ({len(LLMS_FULL)} bytes)")


if __name__ == "__main__":
    main()
