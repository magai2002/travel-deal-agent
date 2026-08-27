"""
Public, unauthenticated, single-exchange demo of the travel-deal agent —
linked from the README so a visitor can see the real pipeline (Claude
extracting a route from free text, then a live Flixbus price check) work
once, without needing credentials or being able to chat indefinitely.

Deliberately narrow, unlike src/assistant/web.py:
  - no config-mutating tools at all — read-only demo, can't touch routes.yaml
  - only Brussels<->Paris can hit real data (see CITY_IDS in
    src/collectors/flixbus.py — Flixbus has no public city lookup, so only
    two cities have hand-found UUIDs); anything else gets a friendly decline
    rather than a fabricated price
  - one Claude call per visit (Haiku, forced tool-choice route extraction),
    not an agent loop — cheap and fast by construction
  - rate-limited per IP per day plus a global daily cap (see rate_limit.py)
    since there's no login to rely on
  - nothing is written anywhere: no InfluxDB write, no config write. The
    real per-route deal verdict (src.pricing.baseline.evaluate) is still
    used to judge the price — which does *read* the real rolling average —
    but this demo query itself is never persisted.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from src.collectors import flixbus
from src.config import Route, Threshold, load_config
from src.demo.rate_limit import check_and_record
from src.pricing.baseline import evaluate

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[RotatingFileHandler(LOG_DIR / "demo.log", maxBytes=5_000_000, backupCount=5)],
)
log = logging.getLogger("travel-demo")

MODEL = os.environ.get("DEMO_MODEL", "claude-haiku-4-5-20251001")
STATIC_DIR = Path(__file__).parent / "static"

# City-name -> code, only for the two cities Flixbus has real UUIDs for
# (see CITY_IDS in src/collectors/flixbus.py). Deliberately small — this
# is what actually bounds the demo to Brussels<->Paris, not the prompt.
_KNOWN_CITIES = {
    "brussels": "BRU", "bruxelles": "BRU", "brussel": "BRU", "bru": "BRU",
    "paris": "PAR", "par": "PAR",
}

_EXTRACT_TOOL = {
    "name": "extract_route",
    "description": "Extract the origin and destination city the user is asking to check prices for.",
    "input_schema": {
        "type": "object",
        "properties": {
            "origin_city": {"type": "string", "description": "Origin city as mentioned by the user, or empty string if not given"},
            "destination_city": {"type": "string", "description": "Destination city as mentioned by the user, or empty string if not given"},
        },
        "required": ["origin_city", "destination_city"],
    },
}

_SYSTEM = """You extract the travel route (origin and destination city) a \
visitor is asking about, from one short message. Call extract_route exactly \
once with your best reading of the cities mentioned. If a city isn't \
clearly given, leave that field as an empty string rather than guessing."""


def _extract_route(user_text: str) -> tuple[str, str]:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=_SYSTEM,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_route"},
        messages=[{"role": "user", "content": user_text}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_route":
            return block.input.get("origin_city", ""), block.input.get("destination_city", "")
    return "", ""


def _next_weekend() -> tuple[date, date]:
    today = date.today()
    days_to_friday = (4 - today.weekday()) % 7 or 7  # always the *next* Friday, never today
    depart = today + timedelta(days=days_to_friday)
    return depart, depart + timedelta(days=2)


def _demo_route() -> Route:
    """
    Reuses the real "Brussels -> Paris" route from routes.yaml if present,
    so the demo's deal threshold matches production exactly. Falls back to
    a same-shaped Route if that entry is ever renamed or removed, so the
    demo doesn't break because of an unrelated config edit.
    """
    try:
        cfg = load_config()
        for r in cfg.routes:
            if {r.origin, r.destination} == {"BRU", "PAR"}:
                return r
    except Exception:
        log.warning("Could not load routes.yaml for the demo route, using fallback", exc_info=True)
    return Route(
        name="Brussels -> Paris",
        origin="BRU",
        destination="PAR",
        modes=["flixbus"],
        category="weekend",
        threshold=Threshold(type="percent_below_avg", value=30, lookback_days=180),
        hard_cap_eur=35,
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class TryIn(BaseModel):
    text: str


app = FastAPI()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/try")
def try_demo(body: TryIn, request: Request) -> JSONResponse:
    ip = _client_ip(request)
    allowed, reason = check_and_record(ip)
    if not allowed:
        log.info("Demo request declined (rate limit) from %s", ip)
        return JSONResponse({"ok": False, "message": reason})

    text = body.text.strip()[:300]  # cap input length — cheap abuse guard
    if not text:
        raise HTTPException(status_code=400, detail="Empty request")

    origin_city, destination_city = _extract_route(text)
    origin = _KNOWN_CITIES.get(origin_city.strip().lower())
    destination = _KNOWN_CITIES.get(destination_city.strip().lower())
    log.info("Demo request from %s: %r -> origin=%s destination=%s", ip, text, origin, destination)

    if not origin or not destination or origin == destination:
        return JSONResponse({
            "ok": True,
            "message": (
                "This demo only has real data wired up for Brussels ↔ Paris "
                "— Flixbus doesn't publish a public city lookup, so only a "
                "couple of cities have real IDs mapped by hand (see the "
                "README). Try asking about a trip between those two! The "
                "full agent in this repo supports any route you configure."
            ),
        })

    depart, ret = _next_weekend()
    result = flixbus.round_trip_cheapest(origin, destination, depart, ret)
    if result is None:
        return JSONResponse({
            "ok": True,
            "message": (
                "Flixbus didn't return a live price just now — that can "
                "happen. See the README for how this pipeline works, or "
                "come back another day."
            ),
        })

    route = _demo_route()
    verdict = evaluate(route, result.price_eur)
    verdict_line = (
        f"That's a deal — {verdict.reason}!" if verdict.is_deal
        else f"Not currently flagged as a deal ({verdict.reason})."
    )

    return JSONResponse({
        "ok": True,
        "message": (
            f"Brussels ↔ Paris round trip, {depart.isoformat()} → {ret.isoformat()}: "
            f"€{result.price_eur:.2f}. {verdict_line}\n\n"
            "That's a real, live Flixbus price just fetched, run through the "
            "same threshold logic the production agent uses twice a day. "
            "That was your one demo try for today — thanks for visiting! "
            "The full source is linked in the README."
        ),
    })
