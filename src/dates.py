"""
Turns a Route + global settings into a *small* list of candidate
(depart_date, return_date) pairs to actually query — instead of brute-forcing
every day in a 6-month window, which is what would blow up cost/day.
"""
from __future__ import annotations

from datetime import date, timedelta

from src.config import AppConfig, Route

_WEEKDAY_MAP = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def candidate_dates(route: Route, cfg: AppConfig, today: date | None = None) -> list[tuple[date, date]]:
    today = today or date.today()

    if route.category == "weekend":
        s = cfg.settings.weekend_trip
        depart_wd = {_WEEKDAY_MAP[d] for d in s.depart_weekday}
        horizon = today + timedelta(weeks=s.search_window_weeks)

        pairs = []
        d = today
        while d <= horizon:
            if d.weekday() in depart_wd:
                for nights in s.trip_length_nights:
                    pairs.append((d, d + timedelta(days=nights)))
            d += timedelta(days=1)
        return pairs

    # longhaul: sparser sampling across a wide window — we're not fitting a
    # weekend, we're just looking for any fare that clears the threshold.
    s = cfg.settings.longhaul
    horizon = today + timedelta(weeks=s.search_window_weeks)
    pairs = []
    d = today + timedelta(days=7)  # skip the very near term, rarely cheap
    while d <= horizon:
        for nights in (s.min_trip_nights, (s.min_trip_nights + s.max_trip_nights) // 2, s.max_trip_nights):
            pairs.append((d, d + timedelta(days=nights)))
        d += timedelta(days=5)  # sample every ~5 days, not every day
    return pairs


if __name__ == "__main__":
    from src.config import load_config

    cfg = load_config()
    for route in cfg.routes:
        dates = candidate_dates(route, cfg)
        print(f"{route.name}: {len(dates)} candidate date-pairs to check")
