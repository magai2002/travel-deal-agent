"""
Entry point: one full pass over every route in config/routes.yaml.
Invoked 1-2x/day by the systemd timer (see deploy/).

Runs sequentially (not concurrently) on purpose: a small VPS shares this
box with Pi-hole, Grafana, etc., and a single well-behaved client that
waits a couple seconds between checks is both kinder to the sites we're
checking and lighter on the box than firing everything at once.
"""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before importing src.storage.influx, which reads
                # os.environ at import time

from src.agent.cost import CostTracker
from src.agent.digest import build_digest
from src.collectors import flixbus, skyscanner_agent
from src.collectors.browser import shared_browser
from src.config import AppConfig, Route, load_config
from src.dates import candidate_dates
from src.metrics import RunMetrics
from src.notify.ntfy import send_deal_alert
from src.pricing.baseline import evaluate
from src.storage.influx import write_price

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=5_000_000, backupCount=5),
    ],
)
log = logging.getLogger("travel-deal-agent")

DELAY_BETWEEN_CHECKS_SEC = 2.5  # flights: browser automation, throttle more
FLIXBUS_DELAY_SEC = 1.0          # flixbus: plain JSON call, needs less throttling

# Maps a collector's logger name (or a prefix of it) to the label used in
# travel_agent_last_run_collector_errors, so failures already logged inside
# the collectors (they log a warning and return None) get counted without
# touching their return signatures.
_COLLECTOR_LOGGER_LABELS = {
    "src.collectors.flixbus": "flixbus",
    "src.collectors.skyscanner_agent": "skyscanner",
}


class _CollectorErrorCounter(logging.Handler):
    """Watches root-logger WARNING+ records and tallies them by collector."""

    def __init__(self, metrics: RunMetrics) -> None:
        super().__init__(level=logging.WARNING)
        self._metrics = metrics

    def emit(self, record: logging.LogRecord) -> None:
        for logger_name, label in _COLLECTOR_LOGGER_LABELS.items():
            if record.name == logger_name or record.name.startswith(logger_name + "."):
                self._metrics.record_collector_error(label)
                return


def _record_if_deal(route: Route, mode: str, depart_date, price_eur: float, url: str | None, found_deals: list[dict], metrics: RunMetrics) -> None:
    write_price(route.name, mode, depart_date, price_eur, url)
    verdict = evaluate(route, price_eur)
    if verdict.is_deal:
        metrics.record_deal_found()
        log.info("DEAL: %s [%s] on %s — €%.2f (%s)", route.name, mode, depart_date, price_eur, verdict.reason)
        found_deals.append(
            {
                "route": route.name,
                "mode": mode,
                "price_eur": price_eur,
                "reason": verdict.reason,
                "depart_date": depart_date.isoformat(),
                "url": url,
            }
        )


async def run() -> None:
    metrics = RunMetrics()
    error_counter = _CollectorErrorCounter(metrics)
    logging.getLogger().addHandler(error_counter)
    success = False
    try:
        cfg: AppConfig = load_config()
        found_deals: list[dict] = []
        cost_tracker = CostTracker()

        # Flixbus first: a direct JSON API, no browser or LLM involved at all.
        # Plain sequential calls, deterministic threshold rule, short delay.
        for route in cfg.routes:
            if "flixbus" not in route.modes:
                continue
            metrics.record_route_checked()
            dates = candidate_dates(route, cfg)
            log.info("Checking %s [flixbus]: %d candidate dates", route.name, len(dates))

            for depart_date, return_date in dates:
                log.info(
                    "Search: %s [flixbus] %s->%s depart=%s return=%s",
                    route.name, route.origin, route.destination, depart_date, return_date,
                )
                result = flixbus.round_trip_cheapest(
                    route.origin, route.destination, depart_date, return_date
                )
                if result:
                    log.info("Result: %s [flixbus] €%.2f on %s", route.name, result.price_eur, depart_date)
                    _record_if_deal(route, "flixbus", depart_date, result.price_eur, result.booking_url, found_deals, metrics)
                else:
                    log.info("Result: %s [flixbus] no price found for %s", route.name, depart_date)
                await asyncio.sleep(FLIXBUS_DELAY_SEC)

        # Flights: one Claude-driven browsing session per route per run (not per
        # date) - the agent explores Skyscanner's own flexible-date window and
        # decides the deal verdict itself; hard_cap_eur is still double-checked
        # here as a deterministic safety net regardless of what it decides.
        flight_routes = [r for r in cfg.routes if "flight" in r.modes]
        if flight_routes:
            async with shared_browser() as browser:
                for route in flight_routes:
                    metrics.record_route_checked()
                    log.info("Searching %s [flight, agentic browsing session]", route.name)
                    result = await skyscanner_agent.search(route, cfg, browser, cost_tracker)

                    if result:
                        log.info(
                            "Result: %s [flight] destination=%s €%.2f depart=%s return=%s (%s)",
                            route.name, result.destination, result.price_eur,
                            result.depart_date, result.return_date, result.itinerary_summary,
                        )
                        write_price(route.name, "flight", result.depart_date, result.price_eur, result.booking_url)

                        is_deal = result.is_deal or result.price_eur <= route.hard_cap_eur
                        reason = result.reason
                        if not result.is_deal and result.price_eur <= route.hard_cap_eur:
                            reason = f"below hard cap (€{route.hard_cap_eur:.0f}) — agent said: {result.reason}"

                        if is_deal:
                            metrics.record_deal_found()
                            log.info("DEAL: %s [flight] on %s — €%.2f (%s)", route.name, result.depart_date, result.price_eur, reason)
                            found_deals.append(
                                {
                                    "route": route.name,
                                    "mode": "flight",
                                    "price_eur": result.price_eur,
                                    "reason": reason,
                                    "depart_date": result.depart_date.isoformat(),
                                    "url": result.booking_url,
                                }
                            )
                    else:
                        log.info("Result: %s [flight] agent found no usable price", route.name)

                    await asyncio.sleep(DELAY_BETWEEN_CHECKS_SEC)

        if found_deals:
            message = build_digest(found_deals, cost_tracker)
            send_deal_alert(
                cfg.notifications,
                title="Cheap trip found",
                message=message,
                url=found_deals[0].get("url"),
            )
            log.info("Sent notification for %d deal(s)", len(found_deals))
        else:
            log.info("No deals found this run.")

        log.info("Claude API usage this run: %s", cost_tracker.summary())
        success = True
    except Exception:
        log.exception("Run failed")
        raise
    finally:
        logging.getLogger().removeHandler(error_counter)
        metrics.finalize(success=success)


if __name__ == "__main__":
    asyncio.run(run())
