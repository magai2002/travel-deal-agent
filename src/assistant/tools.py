"""
Tools available to the assistant agent.

Two categories, and the distinction matters — it's what graph.py uses to
decide whether a tool call needs human confirmation before it runs:

READ-ONLY (not in MUTATING_TOOL_NAMES): list_routes, get_route,
validate_config, price_history. Safe to execute the moment the model
calls them.

MUTATING (in MUTATING_TOOL_NAMES): add_route, remove_route, update_route,
set_home_base. These actually perform the write when invoked — but by
graph design (see graph.py), they are only ever reached via the `tools`
node AFTER the human_review interrupt has approved the action. Never call
these directly outside the graph.

Every mutating tool validates through the existing `AppConfig` pydantic
model before writing anything to disk — a malformed edit (bad threshold
type, missing field) raises inside the tool and comes back to the model as
an error to correct, rather than ever reaching routes.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from langchain_core.tools import tool
from pydantic import ValidationError

from src.config import AppConfig, load_config
from src.storage.influx import price_history_summary

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config/routes.yaml"))

MUTATING_TOOL_NAMES = {"add_route", "remove_route", "update_route", "set_home_base"}


def _load_raw() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _save_raw(data: dict) -> None:
    # Validate BEFORE writing — this is what makes it safe to let an LLM
    # propose arbitrary edits. Note: plain YAML round-trip, so hand-written
    # comments in routes.yaml won't survive an edit made through the
    # assistant. Acceptable for v1; ruamel.yaml would preserve them if that
    # ever matters.
    AppConfig(**data)
    CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


# ─────────────────────────── read-only tools ───────────────────────────

@tool
def list_routes() -> str:
    """List every currently configured route: name, origin, destination, modes, and thresholds."""
    cfg = load_config(CONFIG_PATH)
    return yaml.safe_dump([r.model_dump() for r in cfg.routes], sort_keys=False)


@tool
def get_route(name: str) -> str:
    """Get full configuration details for one route, by its exact name."""
    cfg = load_config(CONFIG_PATH)
    for r in cfg.routes:
        if r.name == name:
            return yaml.safe_dump(r.model_dump(), sort_keys=False)
    return f"No route found named '{name}'. Use list_routes to see exact names."


@tool
def validate_config() -> str:
    """Check whether the current config file is currently valid."""
    try:
        load_config(CONFIG_PATH)
        return "Config is valid."
    except (ValidationError, yaml.YAMLError) as e:
        return f"Config validation failed: {e}"


@tool
def price_history(route_name: str, days: int = 30) -> str:
    """
    Get recent price history for a route from stored observations: mean,
    min, max, observation count, and the most recent price seen. Use this
    to answer questions like 'what's Paris been running lately'.
    """
    summary = price_history_summary(route_name, days=days)
    if summary is None:
        return f"No price observations found for '{route_name}' in the last {days} days."
    return yaml.safe_dump(summary, sort_keys=False)


# ──────────────────────────── mutating tools ────────────────────────────

@tool
def add_route(
    name: str,
    origin: str,
    destination: str,
    modes: list[str],
    category: str,
    threshold_value: float,
    hard_cap_eur: float,
    threshold_type: str = "percent_below_avg",
    lookback_days: int = 180,
) -> str:
    """
    Add a new route to track. `category` must be 'weekend' or 'longhaul'.
    `modes` is a list containing 'flixbus' and/or 'flight'. `threshold_value`
    is a percent (e.g. 30 for "30% below average"). `hard_cap_eur` always
    triggers an alert below that price regardless of average.
    """
    data = _load_raw()
    if any(r["name"] == name for r in data["routes"]):
        return f"A route named '{name}' already exists — use update_route instead."

    data["routes"].append(
        {
            "name": name,
            "origin": origin,
            "destination": destination,
            "modes": modes,
            "category": category,
            "threshold": {
                "type": threshold_type,
                "value": threshold_value,
                "lookback_days": lookback_days,
            },
            "hard_cap_eur": hard_cap_eur,
        }
    )
    try:
        _save_raw(data)
    except ValidationError as e:
        return f"Could not add route — invalid config: {e}"
    return f"Added route '{name}'."


@tool
def remove_route(name: str) -> str:
    """Remove a route by its exact name."""
    data = _load_raw()
    before = len(data["routes"])
    data["routes"] = [r for r in data["routes"] if r["name"] != name]
    if len(data["routes"]) == before:
        return f"No route found named '{name}' — nothing removed."
    _save_raw(data)
    return f"Removed route '{name}'."


@tool
def update_route(
    name: str,
    threshold_value: float | None = None,
    hard_cap_eur: float | None = None,
    lookback_days: int | None = None,
) -> str:
    """Update specific fields on an existing route by name. Only the fields you provide are changed."""
    data = _load_raw()
    for r in data["routes"]:
        if r["name"] == name:
            if threshold_value is not None:
                r["threshold"]["value"] = threshold_value
            if lookback_days is not None:
                r["threshold"]["lookback_days"] = lookback_days
            if hard_cap_eur is not None:
                r["hard_cap_eur"] = hard_cap_eur
            try:
                _save_raw(data)
            except ValidationError as e:
                return f"Could not update route — invalid config: {e}"
            return f"Updated route '{name}'."
    return f"No route found named '{name}'."


@tool
def set_home_base(code: str) -> str:
    """Change the home base / default origin (e.g. 'BRU')."""
    data = _load_raw()
    data["settings"]["home_base"] = code
    try:
        _save_raw(data)
    except ValidationError as e:
        return f"Could not update home base — invalid config: {e}"
    return f"Home base set to '{code}'."


READ_ONLY_TOOLS = [list_routes, get_route, validate_config, price_history]
MUTATING_TOOLS = [add_route, remove_route, update_route, set_home_base]
ALL_TOOLS = READ_ONLY_TOOLS + MUTATING_TOOLS
