"""
Generate album deep dive JSON from known track data.
Outputs to backend/data/album_deep_dives.json
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# DeBÍ TiRAR MáS FOToS — Bad Bunny (2025)
DTMF_TRACKS = [
    {"track_number": 1, "name": "NUEVAYoL", "charge_color": "red", "assessment": "Drugs, sex, flex — cultural pride in wrapper only"},
    {"track_number": 2, "name": "VOY A LLeVARTE PA PR", "charge_color": "red", "assessment": "Sexual pursuit, PR as set decoration"},
    {"track_number": 3, "name": "BAILE INoLVIDABLE", "charge_color": "red", "assessment": "Romantic longing + sexual references"},
    {"track_number": 4, "name": "PERFuMITO NUEVO", "charge_color": "red", "assessment": "Sexual pursuit, mutual objectification framed as empowerment"},
    {"track_number": 5, "name": "WELTiTA", "charge_color": "yellow", "assessment": "Innocent beach romance — harmless, light entertainment"},
    {"track_number": 6, "name": "VeLDÁ", "charge_color": "red", "assessment": "Sexual conquest, ego, possessiveness, materialism"},
    {"track_number": 7, "name": "EL CLúB", "charge_color": "yellow", "assessment": "Light paternalistic romance"},
    {"track_number": 8, "name": "KETU TeCRÉ", "charge_color": "red", "assessment": "Post-breakup contempt, age-shaming, possessive resentment"},
    {"track_number": 9, "name": "BOKeTE", "charge_color": "red", "assessment": "Adversarial ex, ego — political metaphor buried"},
    {"track_number": 10, "name": "KLOuFRENS", "charge_color": "red", "assessment": "Stalking ex, objectification wrapped in heartbreak"},
    {"track_number": 11, "name": "TURiSTA", "charge_color": "green", "assessment": "Genuine vulnerability — processor"},
    {"track_number": 12, "name": "CAFé CON RON", "charge_color": "red", "assessment": "Club, drugs, ex-obsession, commiseration"},
    {"track_number": 13, "name": "PIToRRO DE COCO", "charge_color": "green", "assessment": "Processor — holiday heartbreak, culturally grounded"},
    {"track_number": 14, "name": "LO QUE LE PASÓ A HAWAii", "charge_color": "bright_green", "assessment": "Political, communal, sovereign — the real thing"},
    {"track_number": 15, "name": "EoO", "charge_color": "red", "assessment": "Sexual grinding anthem, ego flex — lowest frequency on the album"},
    {"track_number": 16, "name": "DtMF", "charge_color": "red", "assessment": "Real nostalgia + displacement fear — contaminated by drug references"},
    {"track_number": 17, "name": "LA MuDANZA", "charge_color": "red", "assessment": "Cultural identity + family + defiance — ego woven through"},
]

ALBUMS = [
    {
        "title": "DeBÍ TiRAR MáS FOToS",
        "artist": "Bad Bunny",
        "slug": "debi-tirar-mas-fotos",
        "release_year": 2025,
        "overall_color": "red",
        "summary": "The 2026 Album of the Year: Bait and Switch on a Global Scale. 59% red-charged tracks. One bright green gem buried at track 14.",
        "tracks": DTMF_TRACKS,
    }
]


def main():
    out_path = DATA_DIR / "album_deep_dives.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ALBUMS, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(ALBUMS)} album(s) with {len(DTMF_TRACKS)} tracks to {out_path}")


if __name__ == "__main__":
    main()
