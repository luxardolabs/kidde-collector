"""Centralized Kidde HomeSafe API endpoint builders.

The base URL comes from ``config.API_BASE_URL`` (``KIDDE_COLLECTOR_API_BASE_URL``,
default ``https://api.homesafe.kidde.com/api/v4``) so the dev/demo/e2e stacks can
point the collector at the harness emulator instead of the real cloud.
"""

from app.core import config


class KiddeAPIEndpoints:
    """URL builders for the Kidde HomeSafe REST API (v4)."""

    BASE_URL = config.API_BASE_URL
    LOGIN = f"{BASE_URL}/auth/login"
    LOCATIONS = f"{BASE_URL}/location"
    LOCATION_DEVICES = BASE_URL + "/location/{location_id}/device"
    LOCATION_EVENTS = BASE_URL + "/location/{location_id}/event"
