# Troubleshooting

Common issues and how to resolve them. Start by reading the collector logs — most problems are visible there:

```bash
make logs        # collector-only stack
make dev-logs    # dev stack
make demo-logs   # demo stack
# or: docker logs -f kidde-collector
```

## Dashboards show "No data"

Work through these in order:

1. **Is the collector writing?** The logs print a line each cycle (`Processing cycle completed in …`). If it errors on login or InfluxDB, fix that first.
1. **DBRP mapping (InfluxDB 2.x).** The dashboards use InfluxQL, which on InfluxDB 2.x needs a DBRP mapping. The bundled stacks create it automatically; for your own InfluxDB see [DASHBOARDS.md](DASHBOARDS.md#influxdb-2x--influxql-the-dbrp-mapping).
1. **Datasource.** Make sure the dashboard's `${data_source}` variable points at the right InfluxDB, and that the datasource's database name matches your bucket.
1. **Bucket/org/token.** A 401/404 from InfluxDB (visible in the logs) means the token, org, or bucket is wrong.

## Collector can't log in to Kidde

- Double-check `KIDDE_COLLECTOR_KIDDE_USERNAME` / `_KIDDE_PASSWORD` — they're the same credentials as the Kidde HomeSafe app.
- If you changed your Kidde password, update the env file and restart.
- The collector logs in once and caches the session cookie in `output/`; a `403` clears it and forces a clean re-login on the next cycle. An occasional 403 → re-auth is normal, not an error.

## A device shows offline / stopped reporting measurements

Kidde keeps unresponsive devices in the account and returns their **frozen last-known readings** with `lost` / `offline` / `contact_lost` set. By default (`KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS=true`) the collector writes **only a liveness point** for those — `online=false` plus identity/status fields — and no environmental measurements, so the trends gap out instead of drawing misleading flat lines.

- To bring a device back: reset it in the Kidde app (offline detectors usually need a physical reset).
- To record the frozen readings anyway, set `KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS=false`.

## A device draws a flat/static line

That's an offline device with `SKIP_OFFLINE_MEASUREMENTS=false` — Kidde is returning the same frozen reading every cycle. Set it back to `true` (the default) to gap out offline devices instead.

## Ports 3000 or 8086 already in use

Another stack (or app) is on those ports. Override them:

```bash
GRAFANA_PORT=13400 INFLUX_PORT=18186 make demo-up
```

## Temperature is in Fahrenheit; I want Celsius

Temperature is stored as Kidde reports it (°F). Converting only the value breaks the color thresholds — convert the value **and** rescale the thresholds. See [DASHBOARDS.md → Customizing](DASHBOARDS.md#customizing).

## InfluxDB "unauthorized" / "bucket not found" in the logs

The collector logs actionable guidance on 401 (bad token) and 404 (bad org/bucket) and keeps retrying. Fix `KIDDE_COLLECTOR_INFLUXDB_TOKEN` / `_ORG` / `_BUCKET` in your env file and restart.

## Nothing works and I want to see the raw API

Set `KIDDE_COLLECTOR_WRITE_API_DATA=true` to append each cycle's raw Kidde response to `output/api_data_<date>.jsonl` (one JSON object per line), then inspect it. Turn it back off when done — it's off by default.

## Still stuck?

Open an issue: <https://github.com/luxardolabs/kidde-collector/issues>. Include the collector logs (redact credentials) and your InfluxDB/Grafana versions.
