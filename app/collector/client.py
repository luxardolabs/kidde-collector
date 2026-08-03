"""Low-level async API client for Kidde HomeSafe.

Handles cookie-session login and the location/device/event REST calls. Endpoint URLs
come from ``app.collector.endpoints`` (base URL is ``config.API_BASE_URL``).
"""

from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

from app.collector.endpoints import KiddeAPIEndpoints


def _dict_by_ids(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Index items by their integer ``id``, raising on duplicates."""
    result: dict[int, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for item in items:
        item_id = int(item["id"])
        if item_id in result:
            duplicates.append(item)
        result[item_id] = item
    if duplicates:
        raise ValueError(f"Duplicate IDs: {duplicates}")
    return result


class KiddeClientAuthError(Exception):
    """Exception to indicate an authentication error."""


@dataclass(frozen=True)
class KiddeDataset:
    """Dataset of locations, devices, and events returned from ``get_data()``.

    Attributes
    ----------
    locations : dict[int, dict[str, Any]]
        Location data, keyed by id.
    devices : dict[int, dict[str, Any]] | None
        Device data, keyed by id. None if not requested.
    events : dict[int, dict[str, Any]] | None
        Event data, keyed by id. None if not requested.
    """

    locations: dict[int, dict[str, Any]]
    devices: dict[int, dict[str, Any]] | None
    events: dict[int, dict[str, Any]] | None


class KiddeClient:
    """API Client for Kidde HomeSafe — issues requests through a shared, long-lived session."""

    def __init__(self, session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
        self._session = session
        self.cookies = cookies

    @classmethod
    async def from_login(
        cls, session: aiohttp.ClientSession, email: str, password: str
    ) -> KiddeClient:
        """Create a client from a login (POST /auth/login), storing the session cookies."""
        payload = {"email": email, "password": password}
        async with session.post(KiddeAPIEndpoints.LOGIN, json=payload) as response:
            if response.status in (401, 403):
                raise KiddeClientAuthError
            response.raise_for_status()
            cookies = {c.key: c.value for c in response.cookies.values()}
            return cls(session, cookies)

    async def _request(self, url: str, method: Literal["GET", "POST"] = "GET") -> Any:
        """Make an authenticated request against a fully-qualified URL."""
        async with self._session.request(method, url, cookies=self.cookies) as response:
            if response.status in (401, 403):
                raise KiddeClientAuthError
            response.raise_for_status()
            if response.status == 204:
                return None
            return await response.json()

    async def get_data(
        self, get_devices: bool = True, get_events: bool = True
    ) -> KiddeDataset:
        """Refresh the dataset of locations, devices, and (optionally) events."""
        location_list = await self._request(KiddeAPIEndpoints.LOCATIONS)
        locations = _dict_by_ids(location_list)
        devices = events = None
        if get_devices:
            devices_list = []
            for location_id in locations:
                location_devices = await self._request(
                    KiddeAPIEndpoints.LOCATION_DEVICES.format(
                        location_id=int(location_id)
                    )
                )
                devices_list.extend(location_devices)
            devices = _dict_by_ids(devices_list)
        if get_events:
            events_list = []
            for location_id in locations:
                location_events = await self._request(
                    KiddeAPIEndpoints.LOCATION_EVENTS.format(
                        location_id=int(location_id)
                    )
                )
                events_list.extend(location_events["events"])
            events = _dict_by_ids(events_list)
        return KiddeDataset(locations, devices, events)
