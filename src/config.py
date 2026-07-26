"""
Loads and validates config/routes.yaml.

Keeping this as its own module means every other piece of the system
(collectors, pricing, notify, main) shares one validated view of config —
add a route in YAML and it's immediately usable everywhere, no code changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Weekday = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
Mode = Literal["flixbus", "flight"]
Category = Literal["weekend", "longhaul"]
ThresholdType = Literal["percent_below_avg", "absolute"]


class WeekendTripSettings(BaseModel):
    search_window_weeks: int = 10
    trip_length_nights: list[int] = [1, 2, 3]
    depart_weekday: list[Weekday] = ["FRI", "SAT"]
    return_weekday: list[Weekday] = ["SUN", "MON"]


class LonghaulSettings(BaseModel):
    search_window_weeks: int = 26
    flexible_dates: bool = True
    min_trip_nights: int = 4
    max_trip_nights: int = 14


class GlobalSettings(BaseModel):
    home_base: str
    currency: str = "EUR"
    timezone: str = "Europe/Brussels"
    weekend_trip: WeekendTripSettings = WeekendTripSettings()
    longhaul: LonghaulSettings = LonghaulSettings()


class NotificationSettings(BaseModel):
    channel: Literal["ntfy", "telegram"] = "ntfy"
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"


class Threshold(BaseModel):
    type: ThresholdType
    value: float
    lookback_days: int = 180


class Route(BaseModel):
    name: str
    origin: str
    destination: str
    modes: list[Mode]
    category: Category
    threshold: Threshold
    hard_cap_eur: float = Field(..., description="Always flag below this price")
    flexible_destination_region: str | None = Field(
        default=None,
        description=(
            "When set (e.g. 'Europe'), the flight collector searches this "
            "region broadly (Skyscanner's own flexible/'Everywhere' view) "
            "instead of the fixed `destination` code, and reports back "
            "whichever city came up cheapest. `destination` is then just a "
            "display label, not a real airport code."
        ),
    )


class AppConfig(BaseModel):
    settings: GlobalSettings
    notifications: NotificationSettings
    routes: list[Route]


def load_config(path: str | Path = "config/routes.yaml") -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return AppConfig(**raw)


if __name__ == "__main__":
    cfg = load_config()
    print(f"Loaded {len(cfg.routes)} routes, home base {cfg.settings.home_base}")
    for r in cfg.routes:
        print(f"  - {r.name} [{r.category}] modes={r.modes} cap=€{r.hard_cap_eur}")
