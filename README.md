# Travel Deal Agent

A config-driven agent that checks a set of routes daily, compares prices
against a rolling per-route baseline, and pushes a notification when
something is genuinely cheap — with an LLM writing the actual alert copy.

## Why this exists

Two goals at once: a real AI-engineering portfolio piece, and something
Alexey actually uses to catch cheap weekend trips out of Brussels and
opportunistic long-haul fares.

## How it decides what's "cheap"

Not a single static threshold. Each route flags a deal if *either*:
- the price is a configurable % below its own rolling average
  (`percent_below_avg`, computed from price history in InfluxDB), or
- it's below a hard € cap you set manually (a safety net before enough
  history exists, or for "always tell me" prices).

## Why Flixbus uses a direct API but flights use a browser

As of mid-2026, most free flight-fare APIs have gone away — Amadeus shut
down its free Self-Service tier on July 17, 2026, and Kiwi's Tequila API
now requires 50k+ MAU for new access. Flixbus, in contrast, still has a
well-maintained unofficial JSON API, so bus routes are cheap direct HTTP
calls with no browser. Flights fall back to Playwright until/unless a
viable low-cost fare API shows up (a pay-per-request aggregator is the
production-safe alternative if this needs to scale past personal use).

## Architecture

```
config/routes.yaml      <- routes, thresholds, date rules (edit this, not code)
src/config.py            <- validates the YAML
src/dates.py              <- turns a route into a small set of candidate dates
src/collectors/           <- flixbus.py (working), skyscanner_playwright.py (skeleton)
src/storage/influx.py     <- price history -> InfluxDB (reuses the market-data instance)
src/pricing/baseline.py   <- rolling-average + hard-cap deal logic
src/agent/digest.py       <- LLM turns raw hits into one readable notification
src/notify/ntfy.py        <- push notification
src/main.py                <- orchestrates one full run
deploy/                    <- systemd service + timer (runs 2x/day)
```

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
# edit config/routes.yaml: set a real ntfy_topic, adjust routes/thresholds
python -m src.main      # single manual run
```

For flights (once the Playwright collector is finished):
```bash
pip install -r requirements-scraping.txt
playwright install chromium
```

Deploy on the VPS:
```bash
sudo cp deploy/travel-agent.* /etc/systemd/system/
sudo systemctl enable --now travel-agent.timer
```

## Roadmap

1. ✅ Config schema, date logic, Flixbus collector, storage, thresholds, notify, digest
2. Wire up the Playwright flight collector against real Skyscanner selectors
3. Grafana panel over the `travel_deals` bucket for a price-history view
4. Turn `src/agent/digest.py` into an actual planning step: given a daily
   request budget, have the model choose which routes/dates are worth
   checking rather than iterating every candidate date deterministically
5. Optional: self-host the Flixbus API wrapper in Docker on the VPS instead
   of depending on the public community instance
