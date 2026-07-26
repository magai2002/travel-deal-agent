"""
Flixbus price collector — direct HTTP client against Flixbus's backend
search API. No browser needed for buses at all.

The website drives this same endpoint under the hood:

    GET https://global.api.flixbus.com/search/service/v4/search

so we call it directly with `requests` — much faster and lighter than
driving Chromium, and it can't break on a CSS selector change the way the
old Playwright version could.

CITY IDS: Flixbus keys cities by internal UUIDs (e.g.
"40de6287-8646-11e6-9066-549f350fcb0c"), NOT IATA codes, and there's no
known public lookup endpoint. To add a new city to CITY_IDS below: open
flixbus.com with browser DevTools on the Network tab, run a real search
that touches that city, find the `.../v4/search` request, and read the
city's UUID out of the `cities` object in the JSON response (each entry
has `name`, `slug`, and `id`).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

import requests

log = logging.getLogger(__name__)

_SEARCH_URL = "https://global.api.flixbus.com/search/service/v4/search"
_TIMEOUT_SEC = 20

# IATA-style code -> Flixbus internal city UUID. There's no lookup endpoint,
# so these are populated by hand (see module docstring for how to find one).
CITY_IDS: dict[str, str] = {
    "BRU": "40de6287-8646-11e6-9066-549f350fcb0c",  # Brussels
    "PAR": "40de8964-8646-11e6-9066-549f350fcb0c",  # Paris
}


@dataclass
class PriceResult:
    route: str
    mode: str
    depart_date: date
    price_eur: float
    booking_url: str | None


def _one_way_price(origin: str, destination: str, depart_date: date) -> PriceResult | None:
    """
    Fetch the cheapest one-way fare for a single date. Shared by both public
    functions. Returns None (never raises) on any missing city, network
    error, or empty response — a single bad date must not crash the run.
    """
    from_id = CITY_IDS.get(origin)
    to_id = CITY_IDS.get(destination)
    if from_id is None or to_id is None:
        missing = origin if from_id is None else destination
        log.warning(
            "Flixbus: no city UUID for %r — add it to CITY_IDS (see module "
            "docstring). Skipping %s->%s on %s.",
            missing, origin, destination, depart_date,
        )
        return None

    params = {
        "from_city_id": from_id,
        "to_city_id": to_id,
        "departure_date": depart_date.strftime("%d.%m.%Y"),  # DD.MM.YYYY, not ISO
        "products": json.dumps({"adult": 1}),  # literal JSON string
        "currency": "EUR",
        "locale": "en",
        "search_by": "cities",
        "include_after_midnight_rides": "1",
        "disable_distribusion_trips": "0",
        "disable_global_trips": "0",
        "disable_trips": "[]",
    }

    try:
        resp = requests.get(_SEARCH_URL, params=params, timeout=_TIMEOUT_SEC)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning(
            "Flixbus request failed for %s->%s on %s: %s",
            origin, destination, depart_date, e,
        )
        return None
    except ValueError as e:  # JSON decode error
        log.warning(
            "Flixbus returned non-JSON for %s->%s on %s: %s",
            origin, destination, depart_date, e,
        )
        return None

    trips = data.get("trips") or []
    if not trips:
        log.warning("Flixbus: no trips for %s->%s on %s", origin, destination, depart_date)
        return None

    # `results` is a dict keyed by opaque uid strings — one entry per
    # departure time. Iterate .values() for the individual trip options.
    results = trips[0].get("results") or {}
    if not results:
        log.warning("Flixbus: no results for %s->%s on %s", origin, destination, depart_date)
        return None

    try:
        # Use total_with_platform_fee, not total: the platform fee is
        # mandatory (platform_fee_in_price_required: true), so total alone
        # understates the real price by ~€1.
        cheapest_price = min(
            option["price"]["total_with_platform_fee"] for option in results.values()
        )
    except (KeyError, TypeError) as e:
        log.warning(
            "Flixbus: unexpected result shape for %s->%s on %s: %s",
            origin, destination, depart_date, e,
        )
        return None

    return PriceResult(
        route=f"{origin}->{destination}",
        mode="flixbus",
        depart_date=depart_date,
        price_eur=float(cheapest_price),
        # Intentionally None: this search API has no confirmed per-trip deep
        # link, and guessing at flixbus.com's booking URL format would just
        # produce dead links. Left blank on purpose, not an oversight.
        booking_url=None,
    )


def cheapest(origin: str, destination: str, depart_date: date) -> PriceResult | None:
    """One-way cheapest price for a single date. None on any failure."""
    return _one_way_price(origin, destination, depart_date)


def round_trip_cheapest(
    origin: str, destination: str, depart_date: date, return_date: date
) -> PriceResult | None:
    """
    Cheapest round trip: outbound origin->destination on depart_date plus
    inbound destination->origin on return_date, summed. This is the number
    that actually matters for a "is this weekend trip cheap" check — a
    single leg understates the real cost of the trip.

    Returns None if either leg can't be priced.
    """
    outbound = _one_way_price(origin, destination, depart_date)
    if outbound is None:
        return None
    inbound = _one_way_price(destination, origin, return_date)
    if inbound is None:
        return None

    return PriceResult(
        route=f"{origin}->{destination}->{origin}",
        mode="flixbus",
        depart_date=depart_date,
        price_eur=outbound.price_eur + inbound.price_eur,
        booking_url=None,
    )
