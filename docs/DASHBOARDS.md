# Grafana Dashboards

Two dashboards ship with the collector and are auto-provisioned in the dev/demo stacks. This guide covers what they show, how provisioning works, and how to import them into an existing Grafana.

![By Device dashboard](kidde_collector-by_device.jpg)

## The dashboards

Both live in `grafana/shared-local/` and read the `kidde_collector_device` measurement:

- **By Device** (`kidde_collector-by_device.json`) — one view per detector: online/liveness, last seen, last test, battery, and the air-quality metrics for IAQ models.
- **By Measurement** (`kidde_collector-by_measurement.json`) — one view per metric across all detectors (temperature, humidity, TVOC, CO₂, IAQ, air pressure), good for comparing rooms.

Both also carry a **Safety & Power** row — the alarm and power surface the collector has always stored but nothing displayed: smoke and CO alarms, too-much-smoke, low-battery and end-of-life warnings, battery state/level, CO and peak-CO levels, and the `lost` flag. Booleans render as OK/ALARM (and OK/LOW, OK/REPLACE, OK/LOST) on a coloured background, so a green row reads as safe at a glance; CO carries a threshold at **35 ppm**, the level at which an alarm must sound within four hours.

Its columns are **legitimately sparse** — a blank cell means that model does not report that field, not that data is missing. Measured across a live fleet: `co_alarm`, `battery_state`, `co_level` and `lost` come from every unit; `smoke_alarm` and `too_much_smoke` only from smoke-capable units; `battery_level`, `low_battery_alarm`, `end_of_life_alarm` and `peak_co_level` only from battery-powered CO units.

> The screenshots above predate this row.

They use only core Grafana panels — no plugins to install — and a dashboard variable **`${data_source}`** so you can point them at any InfluxDB datasource.

## Auto-provisioning (dev / demo stacks)

`make dev-up` / `make demo-up` bring up Grafana already wired:

- `grafana/provisioning/datasources/influxdb.yml` — the InfluxDB datasource, pinned to uid **`kidde_influxdb`**, at `http://kidde_influxdb:8086`.
- `grafana/provisioning/dashboards/dashboards.yml` — loads the JSON from `grafana/shared-local/`.

Open `http://localhost:3000` (admin / admin) and both dashboards are there, populated.

## InfluxDB 2.x + InfluxQL: the DBRP mapping

The dashboards are written in **InfluxQL**. On InfluxDB 2.x, InfluxQL is served through the v1-compatibility API, which needs a **DBRP mapping** (a v1 `database` + retention policy that resolves to your bucket). Without it, InfluxQL queries return no data.

The bundled stacks handle this automatically via `ops/influxdb/init-dbrp.sh` (dropped into the InfluxDB container's init hooks). If you run **your own** InfluxDB 2.x, create the mapping once so a v1 database named after your bucket resolves to it:

```bash
influx v1 dbrp create --org <org> --bucket-id <bucket-id> \
  --db <bucket-name> --rp autogen --default
```

InfluxDB 3.x and 1.x serve InfluxQL directly and need no mapping.

## Importing into your own Grafana

1. **Add an InfluxDB datasource** in Grafana. To use the dashboards unchanged, give it uid `kidde_influxdb`; otherwise you'll pick your datasource from the `${data_source}` variable after import. Query language **InfluxQL**, database = your bucket name (see the DBRP note above for 2.x).
1. **Import the JSON:** Dashboards → New → Import → upload `grafana/shared-local/kidde_collector-by_device.json` (and `…-by_measurement.json`), then select your datasource.
1. Repeat for the second dashboard.

## Customizing

**Fahrenheit → Celsius.** Temperature is stored exactly as the Kidde cloud reports it (°F). To display °C, convert in **both** places or the color thresholds won't line up:

1. **Value** — apply `°C = (°F − 32) × 5 / 9` in the query or a Grafana transformation (*Transform → Add field from calculation*).
1. **Thresholds** — rescale each color step with the same formula (e.g. a 68 / 72 / 78 °F set becomes ~20 / 22 / 26 °C).

Set the panel **Unit** to Celsius for the axis label.

**Other tweaks.** The dashboards are plain JSON — edit in Grafana and export, or edit the files in `grafana/shared-local/` and re-provision. See the [data model](CONFIGURATION.md#data-model) for the exact tags and fields available.
