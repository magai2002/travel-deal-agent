"""
Writes price observations to InfluxDB and reads back rolling averages.

Reuses the same InfluxDB instance as the market-data collector
(/root/market-data/collector.py) but a dedicated bucket, since travel prices
and quant/market data are logically separate datasets.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUX_ORG", "quantfinance")
INFLUX_BUCKET = os.environ.get("TRAVEL_BUCKET", "travel_deals")


def _client() -> InfluxDBClient:
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)


def write_price(
    route_name: str,
    mode: str,
    depart_date: date,
    price_eur: float,
    booking_url: str | None = None,
) -> None:
    point = (
        Point("travel_price")
        .tag("route", route_name)
        .tag("mode", mode)
        .field("price_eur", float(price_eur))
        .field("depart_date", depart_date.isoformat())
        .field("booking_url", booking_url or "")
        .time(datetime.now(timezone.utc))
    )
    with _client() as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=INFLUX_BUCKET, record=point)


def rolling_average(route_name: str, lookback_days: int) -> float | None:
    """Average observed price for a route over the last `lookback_days`."""
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{lookback_days}d)
      |> filter(fn: (r) => r._measurement == "travel_price")
      |> filter(fn: (r) => r.route == "{route_name}")
      |> filter(fn: (r) => r._field == "price_eur")
      |> mean()
    '''
    with _client() as client:
        query_api = client.query_api()
        tables = query_api.query(flux)
        for table in tables:
            for record in table.records:
                return float(record.get_value())
    return None


def price_history_summary(route_name: str, days: int = 30) -> dict | None:
    """
    Recent price stats for a route, for the assistant's read-only Q&A tool:
    observation count, mean, min, max, and the most recent price + when it
    was seen. Returns None if there's no data in the window.
    """
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "travel_price")
      |> filter(fn: (r) => r.route == "{route_name}")
      |> filter(fn: (r) => r._field == "price_eur")
    '''
    with _client() as client:
        query_api = client.query_api()
        tables = query_api.query(flux)
        values: list[tuple[datetime, float]] = []
        for table in tables:
            for record in table.records:
                values.append((record.get_time(), float(record.get_value())))

    if not values:
        return None

    values.sort(key=lambda t: t[0])
    prices = [v for _, v in values]
    most_recent_time, most_recent_price = values[-1]

    return {
        "route": route_name,
        "window_days": days,
        "observations": len(prices),
        "mean_eur": round(sum(prices) / len(prices), 2),
        "min_eur": round(min(prices), 2),
        "max_eur": round(max(prices), 2),
        "most_recent_eur": round(most_recent_price, 2),
        "most_recent_at": most_recent_time.isoformat(),
    }
