# Kidde harness — `fake_kidde.py`

A pure-stdlib emulator of the Kidde HomeSafe cloud API. It lets kidde-collector run
end-to-end with **no Kidde account and no hardware**, so the demo/e2e stacks (and CI)
can exercise the full auth → poll → InfluxDB path.

One HTTP port (default `8080`) serves the endpoints the collector polls:

| Method | Path | Response |
|--------|------|----------|
| `POST` | `/api/v4/auth/login` | `200` + `Set-Cookie` (any credentials accepted) |
| `GET`  | `/api/v4/location` | `[ location ]` |
| `GET`  | `/api/v4/location/{id}/device` | `[ device, … ]` |
| `GET`  | `/api/v4/location/{id}/event` | `{ "events": [] }` |

The device JSON mirrors the real API: scalar attributes (`smoke_alarm`, `co_level`,
`temperature`, `battery_state`, …) plus the nested air-quality panel
(`iaq_temperature`, `humidity`, `hpa`, `tvoc`, `iaq`, `co2`), each a
`{value, status, Unit}` object. Readings wobble over time so the dashboards move. Two
of the fake devices are WiFi IAQ detectors (full panel); one is a plain smoke/CO
detector (scalar fields only) so both storage paths are covered.

## Point the collector at it

```bash
KIDDE_COLLECTOR_API_BASE_URL=http://<host>:8080/api/v4
```

The demo (`compose.demo.yml`) and e2e (`compose.e2e.yml`) stacks wire this up
automatically. Env knobs: `KIDDE_FAKE_PORT` (8080), `KIDDE_FAKE_LOCATION_ID` (356103),
`KIDDE_FAKE_LOCATION_LABEL` (Fake Home).

## Run standalone

```bash
python3 harness/fake_kidde.py
curl -s -X POST http://localhost:8080/api/v4/auth/login -d '{}' -i | head -20
curl -s http://localhost:8080/api/v4/location/356103/device | python3 -m json.tool
```
