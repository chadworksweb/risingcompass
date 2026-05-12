"""Run a one-off calibration and print the model's full reasoning trace.

Mirrors what the live calibrator does (same prompt builder, same model,
temperature 0) but never writes to the DB. Useful for inspecting the
chain-of-thought when you want to know WHY the calibrator landed where it did.

*** SERVER-SIDE ONLY. ***
Hits the Anthropic API directly. Runs only when the RC_SERVER_TOOLS=1
env var is set, which docker-compose injects into the rc-backend
container. Terminal sessions hit the guard below and exit 1. To run:

    ssh deploy@le-projects-01
    docker compose exec backend bash -lc 'cat lyrics.txt | python scripts/server_only/inspect_calibration.py "<title>" "<artist>"'

Why guarded: the Anthropic API budget is reserved for live public traffic
(daily cron, Lyrical Charger, badge calibrations). Operator-initiated
calibration work runs through Claude Code in the operator's terminal,
not the API. See feedback_rc_no_api_in_terminal memory + 2026-05-06b.

Usage:
    cat lyrics.txt | python inspect_calibration.py "<title>" "<artist>"
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

if os.environ.get("RC_SERVER_TOOLS") != "1":
    sys.stderr.write(
        "REFUSED: inspect_calibration.py calls Anthropic directly and is "
        "reserved for server-side admin use.\n"
        "Set RC_SERVER_TOOLS=1 (docker-compose does this for rc-backend) "
        "and run inside the container:\n"
        "  docker compose exec backend python scripts/server_only/inspect_calibration.py ...\n"
    )
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from anthropic import AsyncAnthropic

from app.config import settings
from app.database import SessionLocal
from app.services.agents.compass_agent_rubric import (
    build_few_shot_examples,
    build_calibration_prompt,
)


async def run(title: str, artist: str, lyrics: str) -> None:
    db = SessionLocal()
    try:
        examples = build_few_shot_examples(db, target_year=2026)
    finally:
        db.close()

    system_prompt, user_prompt = build_calibration_prompt(
        title, artist, lyrics=lyrics, examples=examples
    )

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.agent_model,
        max_tokens=2048,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    print(f"=== model: {settings.agent_model} ===")
    print(f"=== rubric chars: {len(system_prompt)} ===")
    print()
    print(resp.content[0].text.strip())


def main() -> int:
    if len(sys.argv) != 3:
        print('Usage: inspect_calibration.py "<title>" "<artist>"', file=sys.stderr)
        return 2
    title, artist = sys.argv[1], sys.argv[2]
    lyrics = sys.stdin.read()
    if not lyrics.strip():
        print("No lyrics on stdin", file=sys.stderr)
        return 2
    asyncio.run(run(title, artist, lyrics))
    return 0


if __name__ == "__main__":
    sys.exit(main())
