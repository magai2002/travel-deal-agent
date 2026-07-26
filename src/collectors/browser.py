"""
Shared Playwright browser lifecycle for all browser-based collectors.

Launching a fresh Chromium process per date-check is the expensive part of
scraping (a few seconds and ~300MB per launch). This launches ONE browser
for an entire run and hands out lightweight contexts/pages instead — that's
what keeps this at "a few minutes of browser time per day" rather than
"a few minutes per request", which matters on a small VPS shared with
Pi-hole, Grafana, etc.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from playwright.async_api import Browser, async_playwright

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def shared_browser(headless: bool = True):
    """
    Usage:
        async with shared_browser() as browser:
            await flixbus.cheapest(browser, ...)
            await skyscanner_agent.search(route, cfg, browser, cost_tracker)

    headless=False is for local debugging only (e.g. scripts/test_one_route.py
    --headed) — it needs a real display, which the production VPS doesn't have.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            yield browser
        finally:
            await browser.close()


async def new_page(browser: Browser, record_video_dir: str | None = None):
    """
    Fresh, isolated context+page per check — cheap compared to a new browser.

    record_video_dir: if set, Playwright records the session to a .webm file
    in that directory — the practical way to "see" a browsing session when
    there's no real display to watch it live on (e.g. on the VPS).
    """
    context_kwargs = {"user_agent": _DEFAULT_UA, "locale": "en-GB"}
    if record_video_dir:
        context_kwargs["record_video_dir"] = record_video_dir
    context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    return context, page
