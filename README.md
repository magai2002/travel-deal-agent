# Travel Deal Agent

A config-driven agent that checks a set of routes twice a day, compares
prices against a rolling per-route baseline, and pushes a notification when
something is genuinely cheap — with an LLM writing the actual alert copy.
A separate, on-demand conversational assistant (terminal or browser) lets
you manage that config and ask about price history in plain English.

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

## Why Flixbus uses a direct API but flights use a browsing agent

As of mid-2026, most free flight-fare APIs have gone away — Amadeus shut
down its free Self-Service tier on July 17, 2026, and Kiwi's Tequila API
now requires 50k+ MAU for new access. Flixbus, in contrast, still has a
well-maintained unofficial JSON API, so bus routes are cheap direct HTTP
calls with no browser.

Flights go through `src/collectors/skyscanner_agent.py`: a real Claude
agent driving a Playwright browser with a small set of text/DOM tools
(navigate, read_page, click, fill), rather than a scraper hardcoded
against Skyscanner's CSS. It also judges the deal verdict itself, using
the route's threshold as its brief, and can use Skyscanner's own
flexible-date/"Everywhere" search for routes with no fixed destination —
one browsing session per route per run, not per candidate date.

## Architecture

```
config/routes.yaml            <- routes, thresholds, date rules (edit this, not code)
src/config.py                  <- validates the YAML
src/dates.py                    <- turns a route into a small set of candidate dates
src/collectors/
  flixbus.py                     <- direct JSON API, no browser
  skyscanner_agent.py            <- Claude agent driving Playwright
  browser.py                     <- shared Playwright browser/context setup
src/pricing/baseline.py       <- rolling-average + hard-cap deal logic
src/storage/influx.py          <- price history <-> InfluxDB (reuses the market-data instance)
src/agent/
  digest.py                      <- LLM turns raw hits into one readable notification
  cost.py                        <- tracks estimated Claude API $ spend per run/session
src/notify/ntfy.py             <- push notification
src/metrics.py                  <- Prometheus run metrics (staleness/failure visibility)
src/main.py                      <- orchestrates one full daily-batch run
src/assistant/                  <- separate on-demand conversational assistant (see below)
  tools.py                         <- read-only + config-mutating tools
  graph.py                         <- LangGraph agent: routing, human-in-the-loop approval
  cli.py                            <- terminal chat: python -m src.assistant.cli
  web.py                            <- FastAPI + browser chat: uvicorn src.assistant.web:app
  static/index.html                <- the browser frontend, single file, no build step
deploy/                          <- systemd units: batch-run timer + assistant web service
```

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in real values
# edit config/routes.yaml: set a real ntfy_topic, adjust routes/thresholds
python -m src.main      # single manual run of the daily batch job
```

Deploy the batch job on the VPS:
```bash
sudo cp deploy/travel-agent.* /etc/systemd/system/
sudo systemctl enable --now travel-agent.timer
```

### Run metrics

`src/metrics.py` exports Prometheus gauges after every batch run —
`travel_agent_last_success_unixtime` is the key one, since it's what makes
a crashed run or a dead timer visible instead of silently indistinguishable
from "nothing was cheap today." Backend is either a node_exporter textfile
collector or a Pushgateway push (`METRICS_BACKEND` in `.env`); see
`.env.example` for the paths/URL to set.

### Conversational assistant

A separate entry point — doesn't touch the daily batch run at all. Built on
LangGraph: typed state, conditional routing, and an `interrupt()` /
`Command(resume=...)` human-in-the-loop step before any write to
`routes.yaml` (every proposed edit is also re-validated through the same
`AppConfig` model the batch job uses, so a bad edit can't reach disk).

Terminal:
```bash
python -m src.assistant.cli
```

Browser, for hosting on the VPS:
```bash
uvicorn src.assistant.web:app --host 127.0.0.1 --port 8787
```
Gated by HTTP Basic Auth (`ASSISTANT_WEB_USER` / `ASSISTANT_WEB_PASSWORD` in
`.env` — the app refuses to start without a password set), and meant to sit
behind the box's existing Nginx for TLS/external access rather than being
exposed directly. Deploy as a systemd service:
```bash
sudo cp deploy/travel-assistant-web.service /etc/systemd/system/
sudo systemctl enable --now travel-assistant-web
```

The CLI and web UI keep independent conversation histories (separate
LangGraph `thread_id`s) in the same local SQLite checkpoint file
(`ASSISTANT_STATE_DB`), so either one can be killed and resumed later
without losing context.

## Roadmap

1. ✅ Config schema, date logic, Flixbus collector, storage, thresholds, notify, digest
2. ✅ Flight collector: agentic Playwright browsing against live Skyscanner
3. ✅ Run metrics export (Prometheus) for silent-failure visibility — Grafana
   alert rule on `travel_agent_last_success_unixtime` still to be set up on
   the VPS
4. ✅ Conversational assistant (LangGraph) for config edits + price Q&A, terminal and web
5. Grafana panel over the `travel_deals` bucket for a price-history view
6. Turn `src/agent/digest.py` into an actual planning step: given a daily
   request budget, have the model choose which routes/dates are worth
   checking rather than iterating every candidate date deterministically
7. Optional: self-host the Flixbus API wrapper in Docker on the VPS instead
   of depending on the public community instance
