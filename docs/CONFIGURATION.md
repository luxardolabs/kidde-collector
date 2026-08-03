# Kidde Collector Configuration Guide

All configuration is done through environment variables with the `KIDDE_COLLECTOR_` prefix. Values are validated at startup (see `app/core/config.py`); out-of-range numeric values fall back to the documented default with a warning.

## Table of Contents

- [Required Configuration](#required-configuration)
- [Kidde API Configuration](#kidde-api-configuration)
- [Polling & HTTP](#polling--http)
- [InfluxDB Configuration](#influxdb-configuration)
- [Output / Capture](#output--capture)
- [Logging](#logging)
- [Health Check](#health-check)

## Required Configuration

These environment variables MUST be set for the application to run:

| Variable                          | Description                          | Example                 |
| --------------------------------- | ------------------------------------ | ----------------------- |
| `KIDDE_COLLECTOR_KIDDE_USERNAME`  | Your Kidde HomeSafe account email    | `user@example.com`      |
| `KIDDE_COLLECTOR_KIDDE_PASSWORD`  | Your Kidde HomeSafe account password | `your-password`         |
| `KIDDE_COLLECTOR_INFLUXDB_URL`    | InfluxDB server URL                  | `http://localhost:8086` |
| `KIDDE_COLLECTOR_INFLUXDB_TOKEN`  | InfluxDB authentication token        | `your-token`            |
| `KIDDE_COLLECTOR_INFLUXDB_ORG`    | InfluxDB organization                | `your-org`              |
| `KIDDE_COLLECTOR_INFLUXDB_BUCKET` | InfluxDB bucket name                 | `kidde`                 |

## Kidde API Configuration

| Variable                                    | Default                                 | Description                                                                 |
| ------------------------------------------- | --------------------------------------- | --------------------------------------------------------------------------- |
| `KIDDE_COLLECTOR_API_BASE_URL`              | `https://api.homesafe.kidde.com/api/v4` | Kidde API base URL. Override to target the harness emulator (dev/demo/e2e). |
| `KIDDE_COLLECTOR_GET_EVENTS`                | `false`                                 | Also fetch per-location events each cycle.                                  |
| `KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS` | `true`                                  | See [Offline devices](#offline-devices).                                    |

## Offline devices

Kidde keeps unresponsive devices in the account and returns their **frozen last-known readings** with `lost` / `offline` / `contact_lost` set true and a stale `last_seen`. Writing those every cycle would draw misleading static lines on the dashboards.

Every device always gets an **`online`** field (true when none of the offline flags are set). With `KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS=true` (default), an offline device writes **only a liveness point** — `online` plus the identity/status fields (`last_seen`, `lost`, `offline`, `contact_lost`, `battery_state`) — and **no environmental measurements**, so its trends gap out while it still shows up as offline. Set to `false` to write the frozen readings for offline devices too (the pre-existing behavior).

The collector authenticates once with a cookie session, persists the cookies to the output dir, and reuses them across restarts; a `403` clears them and forces re-auth.

## Polling & HTTP

| Variable                                 | Default | Min | Max  | Description                           |
| ---------------------------------------- | ------- | --- | ---- | ------------------------------------- |
| `KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS` | `60`    | 10  | 3600 | Seconds between poll cycles.          |
| `KIDDE_COLLECTOR_REQUEST_TIMEOUT`        | `10`    | 1   | 120  | Total HTTP request timeout (seconds). |
| `KIDDE_COLLECTOR_CONNECTION_TIMEOUT`     | `5`     | 1   | 60   | HTTP connect timeout (seconds).       |

## InfluxDB Configuration

| Variable                             | Default                  | Description                                                   |
| ------------------------------------ | ------------------------ | ------------------------------------------------------------- |
| `KIDDE_COLLECTOR_INFLUXDB_URL`       | — (required)             | InfluxDB server URL (must start with `http://` / `https://`). |
| `KIDDE_COLLECTOR_INFLUXDB_TOKEN`     | — (required)             | InfluxDB auth token.                                          |
| `KIDDE_COLLECTOR_INFLUXDB_ORG`       | — (required)             | InfluxDB organization.                                        |
| `KIDDE_COLLECTOR_INFLUXDB_BUCKET`    | — (required)             | Target bucket.                                                |
| `KIDDE_COLLECTOR_MEASUREMENT_DEVICE` | `kidde_collector_device` | Measurement name for device metrics.                          |

Writes use the asyncio-native `InfluxDBClientAsync` (fleet ingestion standard): one client per process, and each poll cycle's points are handed to a single awaited batch write — no background flush thread, nothing to flush on shutdown. This v2 write path works against InfluxDB 2.x and 3.x alike.

## Output / Capture

| Variable                         | Default  | Description                                                            |
| -------------------------------- | -------- | ---------------------------------------------------------------------- |
| `KIDDE_COLLECTOR_WRITE_API_DATA` | `false`  | Append each cycle's raw API response to `output/api_data_<date>.json`. |
| `KIDDE_COLLECTOR_EXPORT_FOLDER`  | `output` | Directory for raw captures (bind-mounted).                             |
| `KIDDE_COLLECTOR_COOKIES_DIR`    | `output` | Directory for the persisted session cookie file.                       |

## Logging

| Variable                    | Default | Description                                              |
| --------------------------- | ------- | -------------------------------------------------------- |
| `KIDDE_COLLECTOR_LOG_LEVEL` | `INFO`  | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL`. |

## Health Check

| Variable                               | Default | Min | Max  | Description                                                                                       |
| -------------------------------------- | ------- | --- | ---- | ------------------------------------------------------------------------------------------------- |
| `KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE` | `300`   | 30  | 3600 | When raw capture is enabled, max age (s) of the capture file for the container to report healthy. |

## Data Model

One measurement, `kidde_collector_device`:

- **tags**: `id`, `serial_number`, `location_id`, `location_label`, `label`
- **fields**: an `online` liveness flag; every scalar device attribute (`smoke_alarm`, `co_alarm`, `co_level`, `smoke_level`, `temperature`, `battery_state`, `life`, …); plus per-metric `{name}_value` / `{name}_status` for the air-quality panel: `iaq_temperature`, `humidity`, `hpa`, `tvoc`, `iaq`, `co2`. Non-IAQ detectors write only the scalar fields; offline devices write only the liveness fields (see [Offline devices](#offline-devices)).

Numeric fields are written as **float** so a field that Kidde reports as int on some devices and float on others (e.g. `accuracy`) keeps one consistent InfluxDB type — a mismatch makes InfluxDB reject the whole point.

## Dashboards

Two auto-provisioned Grafana dashboards ship in `grafana/shared-local/`:

- **By Device** (`kidde_collector-by_device.json`) — ![By Device](kidde_collector-by_device.jpg)
- **By Measurement** (`kidde_collector-by_measurement.json`) — ![By Measurement](kidde_collector-by_measurement.jpg)
