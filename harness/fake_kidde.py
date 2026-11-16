#!/usr/bin/env python3
"""Pure-stdlib emulator of the Kidde HomeSafe cloud API.

Lets kidde-collector be tested end-to-end with NO Kidde account and NO hardware. One
HTTP port (default 8080) serves the cookie-session login + the location/device/event
REST endpoints the collector polls:

  POST /api/v4/auth/login                     -> 200 + Set-Cookie (any creds accepted)
  GET  /api/v4/location                       -> [ location ]
  GET  /api/v4/location/{id}/device           -> [ device, ... ]
  GET  /api/v4/location/{id}/event            -> { "events": [] }

Point the collector at it with:
  KIDDE_COLLECTOR_API_BASE_URL=http://<host>:8080/api/v4

Protocol fidelity is the point: the device JSON mirrors what app/storage/influxdb.py
writes — scalar attributes plus the nested air-quality metrics (iaq_temperature,
humidity, hpa, tvoc, iaq, co2), each a {value,status,Unit} object. Readings wobble over
time so the dashboards show movement. No third-party deps — stdlib only.

Env knobs: KIDDE_FAKE_PORT (8080), KIDDE_FAKE_LOCATION_ID (356103),
KIDDE_FAKE_LOCATION_LABEL (Fake Home).
"""

import contextlib
import json
import math
import os
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

PORT = int(os.getenv("KIDDE_FAKE_PORT", "8080"))
LOCATION_ID = int(os.getenv("KIDDE_FAKE_LOCATION_ID", "356103"))
LOCATION_LABEL = os.getenv("KIDDE_FAKE_LOCATION_LABEL", "Fake Home")

_T0 = time.time()


@dataclass(frozen=True)
class FakeDevice:
    id: int
    serial: str
    label: str
    model: str
    rssi: int


# The simulated home, shaped like a real account: two WiFi smoke/IAQ detectors and a WiFi
# CO detector. Every model reports the full air-quality panel; only the smoke/IAQ
# detectors report a last self-test. (The collector's scalar-only path, a device with no
# air-quality metrics, is covered by the unit tests.)
DEVICES = [
    FakeDevice(553549, "001372CBA9BD", "Basement", "wifiiaqdetector", -59),
    FakeDevice(553550, "001372CBA9C0", "Living Room", "wifiiaqdetector", -48),
    FakeDevice(553551, "001372CBA9C4", "Hallway", "cowifidetector", -66),
]

# The last self-test the smoke/IAQ detectors report: a fixed past moment, as a real
# detector's stays until its next test.
LAST_TEST_TIME = "2026-01-28T18:52:54Z"


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _wobble(base: float, amp: float, phase: float, t: float) -> float:
    return round(base + amp * math.sin(t / 30.0 + phase), 2)


def location_list() -> list[dict[str, Any]]:
    return [
        {
            "id": LOCATION_ID,
            "label": LOCATION_LABEL,
            "user_id": 142414,
            "country": "United States",
            "state": "MO",
            "city": "Town and Country",
            "plan": "basic",
            "weather_alert": True,
        }
    ]


def _iaq_block(t: float) -> dict[str, dict[str, Any]]:
    return {
        "iaq_temperature": {
            "value": _wobble(73.6, 2.0, 0, t),
            "status": "Good",
            "Unit": "F",
        },
        "humidity": {
            "value": _wobble(36.2, 4.0, 1, t),
            "status": "Good",
            "Unit": "%RH",
        },
        "hpa": {
            "value": _wobble(98911, 40, 2, t),
            "status": "Unhealthy",
            "Unit": "hpa",
        },
        "tvoc": {"value": _wobble(600, 400, 3, t), "status": "Moderate", "Unit": "ppb"},
        "iaq": {"value": _wobble(92.2, 5.0, 4, t), "status": "Good", "Unit": ""},
        "co2": {"value": _wobble(922, 120, 5, t), "status": "Good", "Unit": "PPM"},
    }


def device_list() -> list[dict[str, Any]]:
    t = time.time() - _T0
    devices = []
    for i, d in enumerate(DEVICES):
        smoke = d.model == "wifiiaqdetector"
        device = {
            "id": d.id,
            "serial_number": d.serial,
            "model": d.model,
            "location_id": LOCATION_ID,
            "label": d.label,
            "lost": False,
            "offline": False,
            "capabilities": (["smoke", "temperature"] if smoke else []) + ["co", "iaq"],
            "co_alarm": False,
            "co_level": 0,
            "battery_state": "ok",
            "batt_volt": 0,
            "life": 514,
            "ap_rssi": int(_wobble(d.rssi, 3, i, t)),
            "last_seen": _iso_now(),
            "end_of_life_status": 1,
            "overall_iaq_status": "Moderate",
        }
        if smoke:
            device.update(
                {
                    "smoke_alarm": False,
                    "smoke_level": int(_wobble(3, 2, i, t)),
                    "temperature": round(_wobble(78, 3, i, t)),
                    "temperature_f": round(_wobble(78, 3, i, t)),
                    "last_test_time": LAST_TEST_TIME,
                }
            )
        else:
            device.update({"end_of_life_alarm": False, "battery_level": 100})
        device.update(_iaq_block(t))
        devices.append(device)
    return devices


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(
        self, format: str, *args: Any
    ) -> None:  # matches the BaseHTTPRequestHandler signature
        """Silence per-request access logging (the e2e harness reads only our banner)."""

    def _json(self, obj: Any, code: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = min(int(self.headers.get("Content-Length", 0) or 0), 1_000_000)
        if length:
            self.rfile.read(length)  # drain the credentials body (any creds accepted)
        if path.endswith("/auth/login"):
            # The collector reads the session cookie off the login response.
            self._json(
                {"status": "ok"},
                cookie="id_token=fake-session-token; Path=/; HttpOnly",
            )
        else:
            self._json({"error": "not found"}, 404)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.endswith("/location"):
            self._json(location_list())
        elif path.endswith("/device"):
            self._json(device_list())
        elif path.endswith("/event"):
            self._json({"events": []})
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"fake-kidde: listening on :{PORT} "
        f"(location {LOCATION_ID} '{LOCATION_LABEL}', {len(DEVICES)} devices)",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
