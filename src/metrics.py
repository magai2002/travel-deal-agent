"""
Run-level Prometheus metrics, so a broken run is visible in Grafana instead
of silently indistinguishable from "nothing was cheap today".

Every run is a fresh process with no persistent state, so everything here
is a Gauge (not a Counter) that gets fully overwritten each run — each
value means "as observed during the most recent run", not an accumulating
total. The one exception in spirit is `last_success_unixtime`, which must
survive a crashed run by carrying forward the previous value (see
RunMetrics.__init__ / finalize below) rather than resetting to 0.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway, write_to_textfile
from prometheus_client.parser import text_string_to_metric_families

log = logging.getLogger(__name__)

METRICS_BACKEND = os.environ.get("METRICS_BACKEND", "textfile")
METRICS_TEXTFILE_PATH = os.environ.get(
    "METRICS_TEXTFILE_PATH", "/var/lib/node_exporter/textfile_collector/travel_agent.prom"
)
METRICS_PUSHGATEWAY_URL = os.environ.get("METRICS_PUSHGATEWAY_URL", "http://localhost:9091")

# Only used for the pushgateway backend, where there's no textfile to read
# the previous value back from.
_LAST_SUCCESS_STATE_FILE = Path(
    os.environ.get("METRICS_LAST_SUCCESS_STATE_FILE", ".last_success_unixtime")
)

_LAST_SUCCESS_METRIC = "travel_agent_last_success_unixtime"


def _read_previous_last_success() -> float:
    """
    Best-effort read of the previous run's last_success_unixtime, so a
    crashed run doesn't reset the staleness clock to 0. Any failure (first
    ever run, missing file, unparseable content) falls back to 0.
    """
    try:
        if METRICS_BACKEND == "pushgateway":
            if _LAST_SUCCESS_STATE_FILE.exists():
                return float(_LAST_SUCCESS_STATE_FILE.read_text().strip())
            return 0.0

        path = Path(METRICS_TEXTFILE_PATH)
        if not path.exists():
            return 0.0
        text = path.read_text()
        for family in text_string_to_metric_families(text):
            if family.name != _LAST_SUCCESS_METRIC:
                continue
            for sample in family.samples:
                return float(sample.value)
        return 0.0
    except Exception:
        log.warning("Could not read back previous %s, defaulting to 0", _LAST_SUCCESS_METRIC, exc_info=True)
        return 0.0


class RunMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._start_time = time.time()
        self._previous_last_success = _read_previous_last_success()

        self._routes_checked = Gauge(
            "travel_agent_last_run_routes_checked",
            "Number of routes the most recent run got through",
            registry=self.registry,
        )
        self._deals_found = Gauge(
            "travel_agent_last_run_deals_found",
            "Number of deals that passed the threshold in the most recent run",
            registry=self.registry,
        )
        self._collector_errors = Gauge(
            "travel_agent_last_run_collector_errors",
            "Number of warning-level collector failures in the most recent run",
            ["collector"],
            registry=self.registry,
        )
        self._duration = Gauge(
            "travel_agent_last_run_duration_seconds",
            "Wall-clock duration of the most recent run",
            registry=self.registry,
        )
        self._last_success = Gauge(
            _LAST_SUCCESS_METRIC,
            "Unix timestamp of the last run that completed without a fatal error",
            registry=self.registry,
        )

    def record_route_checked(self) -> None:
        self._routes_checked.inc()

    def record_deal_found(self) -> None:
        self._deals_found.inc()

    def record_collector_error(self, collector: str) -> None:
        self._collector_errors.labels(collector=collector).inc()

    def finalize(self, success: bool) -> None:
        self._duration.set(time.time() - self._start_time)
        last_success_value = time.time() if success else self._previous_last_success
        self._last_success.set(last_success_value)

        try:
            if METRICS_BACKEND == "pushgateway":
                push_to_gateway(METRICS_PUSHGATEWAY_URL, job="travel_agent", registry=self.registry)
                _LAST_SUCCESS_STATE_FILE.write_text(str(last_success_value))
            else:
                write_to_textfile(METRICS_TEXTFILE_PATH, self.registry)
        except Exception:
            log.warning("Failed to export run metrics (backend=%s)", METRICS_BACKEND, exc_info=True)
