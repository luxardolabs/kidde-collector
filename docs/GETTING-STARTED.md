# Getting Started

This guide takes you from nothing to device metrics in Grafana. Pick the path that matches what you have.

## Prerequisites

- **Docker** and the **Docker Compose** plugin.
- A **Kidde HomeSafe account** (the email + password you use in the Kidde app) — required for real data, but **not** for the demo.
- For storing and viewing data you need **InfluxDB** (2.x or 3.x) and **Grafana**. You can bring your own, or let the dev/demo stacks run bundled copies for you.

The collector reaches **out** to the Kidde cloud and **out** to InfluxDB. It never listens on an inbound port, so no firewall rules are needed.

## Path 1 — Try it with no account and no hardware (demo)

The demo stack runs a built-in fake Kidde cloud plus a throwaway InfluxDB and Grafana, so you can see the dashboards populate in under a minute.

```bash
make demo-up
# Grafana:  http://localhost:3000  (admin / admin)
# InfluxDB: http://localhost:8086
make demo-down            # stop (add: make demo-clean to delete the data volumes)
```

If ports 3000/8086 are already in use, override them: `GRAFANA_PORT=13400 INFLUX_PORT=18186 make demo-up`.

## Path 2 — Real device data, self-contained (dev)

The dev stack polls your **real** Kidde account and stores it in a **bundled** InfluxDB + auto-provisioned Grafana — everything on your machine, nothing external required.

1. Copy the local override template and add your Kidde credentials:
   ```bash
   cp .env.dev.local.example .env.dev.local
   # edit .env.dev.local — set KIDDE_COLLECTOR_KIDDE_USERNAME / _KIDDE_PASSWORD
   ```
1. Start it:
   ```bash
   make dev-up               # Grafana http://localhost:3000 (admin/admin)
   make dev-logs             # follow the collector
   make dev-down             # stop (make dev-clean also wipes data volumes)
   ```

## Path 3 — Into your existing InfluxDB (collector-only)

If you already run InfluxDB and Grafana, run just the collector and point it at them.

1. Create your environment file from the template:
   ```bash
   cp .env.example .env.dev
   # edit .env.dev — your Kidde creds + your InfluxDB URL / org / bucket / token
   ```
1. Start the collector:
   ```bash
   make up                   # builds locally and runs against your InfluxDB
   make logs                 # follow it
   make down                 # stop
   ```

To run the published image directly instead of building locally, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Where to next

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — run the published image, Compose, and remote/production deploys.
- **[CONFIGURATION.md](CONFIGURATION.md)** — every `KIDDE_COLLECTOR_*` variable, defaults, and bounds.
- **[DASHBOARDS.md](DASHBOARDS.md)** — the Grafana dashboards, datasource, and importing them into your own Grafana.
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — no data, offline devices, re-auth, port clashes, and more.
