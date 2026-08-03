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

import json
import math
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.getenv("KIDDE_FAKE_PORT", "8080"))
LOCATION_ID = int(os.getenv("KIDDE_FAKE_LOCATION_ID", "356103"))
LOCATION_LABEL = os.getenv("KIDDE_FAKE_LOCATION_LABEL", "Fake Home")

_T0 = time.time()

# The simulated home: WiFi IAQ detectors that report the full air-quality panel, plus a
# plain smoke/CO detector with no environmental metrics — so the collector exercises
# both the nested-metric path and the scalar-only path.
DEVICES = [
    {"id": 553549, "serial": "001372CBA9BD", "label": "Basement", "iaq": True},
    {"id": 553550, "serial": "001372CBA9C0", "label": "Living Room", "iaq": True},
    {"id": 553551, "serial": "001372CBA9C4", "label": "Hallway", "iaq": False},
]


def _wobble(base: float, amp: float, phase: float, t: float) -> float:
    return round(base + amp * math.sin(t / 30.0 + phase), 2)


def location_list() -> list:
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


def _iaq_block(t: float) -> dict:
    return {
        "iaq_temperature": {"value": _wobble(73.6, 2.0, 0, t), "status": "Good", "Unit": "F"},
        "humidity": {"value": _wobble(36.2, 4.0, 1, t), "status": "Good", "Unit": "%RH"},
        "hpa": {"value": _wobble(98911, 40, 2, t), "status": "Unhealthy", "Unit": "hpa"},
        "tvoc": {"value": _wobble(600, 400, 3, t), "status": "Moderate", "Unit": "ppb"},
        "iaq": {"value": _wobble(92.2, 5.0, 4, t), "status": "Good", "Unit": ""},
        "co2": {"value": _wobble(922, 120, 5, t), "status": "Good", "Unit": "PPM"},
    }


def device_list() -> list:
    t = time.time() - _T0
    devices = []
    for i, d in enumerate(DEVICES):
        device = {
            "id": d["id"],
            "serial_number": d["serial"],
            "model": "wifiiaqdetector" if d["iaq"] else "smokecodetector",
            "location_id": LOCATION_ID,
            "label": d["label"],
            "lost": False,
            "capabilities": ["smoke", "co", "temperature"]
            + (["iaq"] if d["iaq"] else []),
            "smoke_alarm": False,
            "co_alarm": False,
            "smoke_level": int(_wobble(3, 2, i, t)),
            "co_level": 0,
            "battery_state": "ok",
            "batt_volt": 0,
            "life": 514,
            "temperature": round(_wobble(78, 3, i, t)),
            "temperature_f": round(_wobble(78, 3, i, t)),
            "overall_iaq_status": "Moderate" if d["iaq"] else "Normal",
        }
        if d["iaq"]:
            device.update(_iaq_block(t))
        devices.append(device)
    return devices


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence per-request logging
        pass

    def _json(self, obj, code: int = 200, cookie: str | None = None) -> None:
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
