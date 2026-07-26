"""
Flight collector — a real Claude agent driving a Playwright browser, not a
scripted scraper.

Earlier versions of this file hardcoded CSS selectors against Skyscanner's
DOM, which is unstable and was never verified against the live site. This
version instead gives Claude a small set of text/DOM-based browser tools
(navigate, read_page, click, fill) plus a price-history lookup, and lets it
drive the search itself the way a human would: read the page, click on
visible text, adapt when something doesn't work. It also *decides* whether
a price is a deal, using the route's threshold as its brief - rather than
handing a bare number to a separate Python rule.

One agent session per route per run (not per candidate date), with the
model told the route's date *window* and left to explore Skyscanner's own
flexible-date/"Everywhere" search within it - this is what makes a flexible
"cheapest place in Europe" route possible at all, and keeps a run to one
browsing session per route instead of dozens.

The route's hard_cap_eur is still checked deterministically in src/main.py
after this returns - the agent's judgment augments the safety net, it never
replaces it.

STATUS: the tool-execution logic here can't be verified against the live
Skyscanner site from this sandbox (no network egress to it) - the first
real runs on the VPS are the actual test. logs/agent.log will show every
tool call the agent makes, which is the main thing to check after deploying.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date

import anthropic
from playwright.async_api import Browser

from src.agent.cost import CostTracker
from src.collectors.browser import new_page
from src.config import AppConfig, Route
from src.storage.influx import rolling_average

log = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_SKYSCANNER_MODEL", "claude-sonnet-5")
MAX_ITERATIONS = 20
SESSION_TIMEOUT_SEC = 180
_MAX_PAGE_TEXT_CHARS = 6000


@dataclass
class AgentSearchResult:
    route: str
    mode: str
    destination: str
    depart_date: date
    return_date: date | None
    price_eur: float
    booking_url: str | None
    itinerary_summary: str
    is_deal: bool
    reason: str


_SYSTEM = """You are a travel-deal research agent. You control a real web \
browser via tools (navigate, click, fill, read_page, press_key) to search \
Skyscanner for flights and judge whether a price is a good deal, given the \
route and threshold described in each task.

Work like someone doing this by hand: call read_page before deciding what \
to click, click on visible text/labels rather than guessing at technical \
selectors, and dismiss cookie/consent banners if they block the page. If a \
click or fill fails, try a different visible label rather than repeating \
the exact same action.

Call get_price_history if you want more context on whether an observed \
price is unusually good before deciding.

When you're done, call report_result exactly once with your findings. If \
you can't find a usable price after a reasonable effort, call it with \
found_price: false rather than guessing."""

_TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "read_page",
        "description": "Get the visible text content of the current page - use this to read prices, dates, and search results.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click the first visible element matching this text (button, link, dropdown option, calendar date, etc).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "fill",
        "description": "Type text into an input field identified by its visible label or placeholder text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["label", "text"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key, e.g. 'Enter' or 'Escape' - useful for submitting a field or dismissing a dropdown.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "get_price_history",
        "description": "Look up the rolling average price for this exact route over the last N days, to judge whether a price is unusually good.",
        "input_schema": {
            "type": "object",
            "properties": {"lookback_days": {"type": "integer"}},
            "required": ["lookback_days"],
        },
    },
    {
        "name": "report_result",
        "description": "End the search and report what you found. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "found_price": {"type": "boolean"},
                "destination": {
                    "type": "string",
                    "description": "The actual destination this price is for (city name or code) - fill in even for a fixed-destination route.",
                },
                "price_eur": {"type": "number"},
                "depart_date": {"type": "string", "description": "ISO date, YYYY-MM-DD"},
                "return_date": {"type": "string", "description": "ISO date, YYYY-MM-DD"},
                "booking_url": {"type": "string"},
                "itinerary_summary": {
                    "type": "string",
                    "description": "One or two sentences: airline(s), stops, duration.",
                },
                "is_deal": {"type": "boolean"},
                "reason": {
                    "type": "string",
                    "description": "Why this is or isn't a deal, given the threshold and history you were told.",
                },
            },
            "required": ["found_price", "is_deal", "reason"],
        },
    },
]


def _describe_window(route: Route, cfg: AppConfig) -> str:
    if route.category == "weekend":
        s = cfg.settings.weekend_trip
        nights = "/".join(str(n) for n in s.trip_length_nights)
        weekdays = "/".join(s.depart_weekday)
        return (
            f"a weekend-style trip: depart on a {weekdays}, {nights} night(s), "
            f"sometime in the next {s.search_window_weeks} weeks"
        )
    s = cfg.settings.longhaul
    return (
        f"a longer trip: {s.min_trip_nights}-{s.max_trip_nights} nights, "
        f"sometime in the next {s.search_window_weeks} weeks - dates are "
        f"flexible, find whatever is cheapest within that range"
    )


def _build_task_prompt(route: Route, cfg: AppConfig) -> str:
    window = _describe_window(route, cfg)

    if route.flexible_destination_region:
        destination_line = (
            f"Destination: flexible - search broadly across "
            f"{route.flexible_destination_region} (use Skyscanner's own "
            f'"Everywhere"/flexible-destination search from {route.origin}, '
            f"not a single fixed city) and report back whichever destination "
            f"comes up cheapest."
        )
    else:
        destination_line = f"Destination: {route.destination}"

    threshold = route.threshold
    if threshold.type == "percent_below_avg":
        threshold_line = (
            f"This route is worth flagging if the price is at least "
            f"{threshold.value:.0f}% below its {threshold.lookback_days}-day "
            f"rolling average (use the get_price_history tool to check that "
            f"average first) - OR if it's at or below the hard cap of "
            f"€{route.hard_cap_eur:.0f} regardless of history."
        )
    else:
        threshold_line = f"Flag this route if the price is at or below €{route.hard_cap_eur:.0f}."

    return f"""Find the cheapest round-trip flight for this route on \
Skyscanner (https://www.skyscanner.net) and decide whether it's a good deal.

Origin: {route.origin}
{destination_line}
Trip window: {window}

{threshold_line}

Use the browser tools to navigate Skyscanner, search, and read the results \
- handle cookie banners or popups if they appear. When you have a final \
answer (or have concluded no usable price is available), call \
report_result exactly once. Don't call it more than once."""


async def _execute_tool(page, route: Route, name: str, tool_input: dict) -> str:
    try:
        if name == "navigate":
            await page.goto(tool_input["url"], wait_until="domcontentloaded", timeout=30_000)
            return f"Navigated to {page.url}"

        if name == "read_page":
            text = await page.inner_text("body")
            text = " ".join(text.split())
            if len(text) > _MAX_PAGE_TEXT_CHARS:
                text = text[:_MAX_PAGE_TEXT_CHARS] + " …[truncated]"
            return text

        if name == "click":
            target_text = tool_input["text"]
            await page.get_by_text(target_text, exact=False).first.click(timeout=5_000)
            return f"Clicked element matching '{target_text}'"

        if name == "fill":
            label, text = tool_input["label"], tool_input["text"]
            try:
                await page.get_by_label(label, exact=False).first.fill(text, timeout=5_000)
            except Exception:
                await page.get_by_placeholder(label, exact=False).first.fill(text, timeout=5_000)
            return f"Filled '{label}' with '{text}'"

        if name == "press_key":
            await page.keyboard.press(tool_input["key"])
            return f"Pressed {tool_input['key']}"

        if name == "get_price_history":
            avg = rolling_average(route.name, tool_input["lookback_days"])
            return json.dumps({"rolling_average_eur": avg})

        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool '{name}' failed: {e}"


def _to_result(route: Route, report: dict) -> AgentSearchResult | None:
    try:
        depart_date = date.fromisoformat(report["depart_date"])
        return_date_raw = report.get("return_date")
        return_date = date.fromisoformat(return_date_raw) if return_date_raw else None
        return AgentSearchResult(
            route=route.name,
            mode="flight",
            destination=report.get("destination") or route.destination,
            depart_date=depart_date,
            return_date=return_date,
            price_eur=float(report["price_eur"]),
            booking_url=report.get("booking_url"),
            itinerary_summary=report.get("itinerary_summary", ""),
            is_deal=bool(report.get("is_deal", False)),
            reason=report.get("reason", ""),
        )
    except (KeyError, ValueError, TypeError) as e:
        log.warning(
            "Skyscanner agent for %s returned an unparseable report (%s): %s",
            route.name, e, report,
        )
        return None


async def _run_session(
    route: Route, cfg: AppConfig, browser: Browser, cost_tracker: CostTracker,
    video_dir: str | None = None,
) -> AgentSearchResult | None:
    context, page = await new_page(browser, record_video_dir=video_dir)
    try:
        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": _build_task_prompt(route, cfg)}]
        report: dict | None = None

        for _ in range(MAX_ITERATIONS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=_SYSTEM,
                tools=_TOOLS,
                messages=messages,
            )
            cost_tracker.record(MODEL, response.usage)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                log.info(
                    "Skyscanner agent for %s stopped without a result (stop_reason=%s)",
                    route.name, response.stop_reason,
                )
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "report_result":
                    report = block.input
                    log.info("Skyscanner agent report for %s: %s", route.name, report)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": "recorded"}
                    )
                    continue
                log.info(
                    "Skyscanner agent tool call for %s: %s(%s)",
                    route.name, block.name, block.input,
                )
                result_text = await _execute_tool(page, route, block.name, block.input)
                log.info(
                    "Skyscanner agent tool result for %s: %s",
                    route.name, result_text[:300],
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
                )

            messages.append({"role": "user", "content": tool_results})
            if report is not None:
                break
        else:
            log.warning(
                "Skyscanner agent for %s exceeded %d iterations without a result",
                route.name, MAX_ITERATIONS,
            )
            return None

        if report is None or not report.get("found_price"):
            log.info("Skyscanner agent for %s found no usable price", route.name)
            return None

        return _to_result(route, report)
    finally:
        await context.close()


async def search(
    route: Route, cfg: AppConfig, browser: Browser, cost_tracker: CostTracker,
    video_dir: str | None = None,
) -> AgentSearchResult | None:
    """
    Run one browsing session for this route. Never raises - any failure
    (timeout, tool error, unparseable report) is logged and returns None,
    same contract as the other collectors.

    video_dir: optional directory to record the session to (.webm) - see
    src/collectors/browser.py:new_page.
    """
    try:
        return await asyncio.wait_for(
            _run_session(route, cfg, browser, cost_tracker, video_dir=video_dir),
            timeout=SESSION_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        log.warning(
            "Skyscanner agent for %s timed out after %ds",
            route.name, SESSION_TIMEOUT_SEC,
        )
        return None
    except Exception as e:
        log.warning("Skyscanner agent failed for %s: %s", route.name, e)
        return None
