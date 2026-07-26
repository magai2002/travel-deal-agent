#!/usr/bin/env python3
"""
Atomic single-route test harness.

Runs exactly ONE route from config/routes.yaml through its real collector -
Flixbus (direct API) or the Skyscanner browsing agent (Playwright) - instead
of sweeping all of config/routes.yaml like `python -m src.main` does. Meant
for iterating on the agentic flight collector without waiting on (or paying
for) a full run.

Usage:
    python scripts/test_one_route.py "Paris"              # matches "Brussels -> Paris"
    python scripts/test_one_route.py "Dubai" --headed     # visible browser window
    python scripts/test_one_route.py "Astana" --no-video  # skip video recording
    python scripts/test_one_route.py "Europe" --write --notify

By default this does NOT write to InfluxDB or send a push notification -
pass --write / --notify to opt into either (kept off by default so repeated
test runs don't pollute real price history or spam your phone).

NOTE on --headed: this VPS has no DISPLAY set, so a non-headless Chromium
either fails to launch or (if run under `xvfb-run`) launches into a virtual
framebuffer - invisible to you, since Xvfb isn't a display *you* can watch,
just one Playwright can render into. For an actually-visible browser window,
run this script from your laptop against the same repo/config instead of on
the VPS. On the VPS, leave --headed off; the default video recording (a
.webm file under scripts/test_output/) is the practical way to see what the
agent did after the fact, headed or not.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, for `src.*` imports

from dotenv import load_dotenv

load_dotenv()

from src.agent.cost import CostTracker
from src.collectors import flixbus, skyscanner_agent
from src.collectors.browser import shared_browser
from src.config import Route, load_config
from src.dates import candidate_dates
from src.notify.ntfy import send_deal_alert
from src.pricing.baseline import evaluate
from src.storage.influx import write_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_one_route")

OUTPUT_DIR = Path("scripts/test_output")


def find_route(cfg, needle: str) -> Route:
    matches = [r for r in cfg.routes if needle.lower() in r.name.lower()]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        print(f"No route matches '{needle}'. Available routes:")
        candidates = cfg.routes
    else:
        print(f"'{needle}' matches multiple routes — be more specific:")
        candidates = matches
    for r in candidates:
        print(f"  - {r.name}")
    sys.exit(1)


async def test_flixbus(route: Route, cfg) -> tuple | None:
    dates = candidate_dates(route, cfg)
    if not dates:
        print("No candidate dates generated for this route/category.")
        return None
    depart_date, return_date = dates[0]
    print(f"Testing Flixbus: {route.origin} -> {route.destination}, depart={depart_date} return={return_date}")
    result = flixbus.round_trip_cheapest(route.origin, route.destination, depart_date, return_date)
    if not result:
        print("No price found.")
        return None
    print(f"Price: €{result.price_eur:.2f}  url={result.booking_url}")
    verdict = evaluate(route, result.price_eur)
    print(f"Deal verdict: is_deal={verdict.is_deal} ({verdict.reason})")
    return result, depart_date


async def test_flight(route: Route, cfg, headed: bool, video: bool, cost_tracker: CostTracker):
    video_dir = None
    if video:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        video_dir = str(OUTPUT_DIR)

    print(f"Launching Chromium ({'headed' if headed else 'headless'}) ...")
    async with shared_browser(headless=not headed) as browser:
        result = await skyscanner_agent.search(route, cfg, browser, cost_tracker, video_dir=video_dir)

    if not result:
        print("Agent found no usable price.")
        return None

    print(f"Destination found: {result.destination}")
    print(f"Price: €{result.price_eur:.2f}")
    print(f"Dates: {result.depart_date} -> {result.return_date}")
    print(f"Itinerary: {result.itinerary_summary}")
    print(f"Agent verdict: is_deal={result.is_deal} ({result.reason})")
    print(f"Booking URL: {result.booking_url}")
    if video_dir:
        print(f"Video recording saved under {video_dir}/ — look for the newest .webm file")
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("route", help="Route name or substring, e.g. 'Dubai' or 'Brussels -> Paris'")
    parser.add_argument(
        "--headed", action="store_true",
        help="Launch a real, visible browser window (needs a real display — see module docstring)",
    )
    parser.add_argument("--no-video", action="store_true", help="Skip recording the browser session")
    parser.add_argument(
        "--write", action="store_true",
        help="Write the result to InfluxDB (default: skip, so test runs don't pollute price history)",
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="Send a real push notification if the result is a deal (default: skip)",
    )
    args = parser.parse_args()

    cfg = load_config()
    route = find_route(cfg, args.route)
    print(f"=== {route.name} [{route.category}] modes={route.modes} ===\n")

    cost_tracker = CostTracker()

    if "flixbus" in route.modes:
        outcome = await test_flixbus(route, cfg)
        if outcome and args.write:
            result, depart_date = outcome
            write_price(route.name, "flixbus", depart_date, result.price_eur, result.booking_url)
            print("(written to InfluxDB)")
        print()

    if "flight" in route.modes:
        result = await test_flight(route, cfg, args.headed, not args.no_video, cost_tracker)
        if result:
            is_deal = result.is_deal or result.price_eur <= route.hard_cap_eur
            print(f"\nFinal deal verdict (with hard-cap safety net): {is_deal}")
            if args.write:
                write_price(route.name, "flight", result.depart_date, result.price_eur, result.booking_url)
                print("(written to InfluxDB)")
            if args.notify and is_deal:
                send_deal_alert(cfg.notifications, title="Test deal", message=result.reason, url=result.booking_url)
                print("(notification sent)")

    print(f"\n{cost_tracker.summary()}")


if __name__ == "__main__":
    asyncio.run(main())
