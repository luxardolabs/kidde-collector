#!/usr/bin/env python3
"""Docker health check for kidde-collector (see the Dockerfile HEALTHCHECK).

Healthy means the collector WORKS, not that its process is alive: the poller touches a
heartbeat file in the export folder after every cycle whose InfluxDB write succeeded, and the
check requires it to be fresh. The poll loop survives every failure by design (a Kidde login
error, an InfluxDB outage), so a process-alive check reported a collector that wrote nothing
for hours as healthy, and a deploy's smoke probe passed a build that could not log in.

The allowed age is KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE, and never less than three poll
intervals, so a long KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS cannot fail a working collector.

Exit 0 = healthy, 1 = unhealthy.
"""

import datetime
import os
import sys
from datetime import timedelta
from pathlib import Path

from app.core.config import HEARTBEAT_FILENAME


def _int_env(key: str, default: int, lo: int, hi: int) -> int:
    """Parse a bounded int from the env, falling back to the default (never raises)."""
    try:
        value = int(os.getenv(key, str(default)))
    except TypeError, ValueError:
        return default
    return max(lo, min(hi, value))


MAX_AGE_SECONDS = max(
    _int_env("KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE", 300, 30, 3600),
    3 * _int_env("KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS", 60, 10, 3600),
)
OUTPUT_DIR = os.getenv("KIDDE_COLLECTOR_EXPORT_FOLDER", "output")


def check_heartbeat() -> tuple[bool, str]:
    """Verify the last successful cycle (the poller's heartbeat file) is recent."""
    heartbeat = Path(OUTPUT_DIR) / HEARTBEAT_FILENAME
    if not heartbeat.exists():
        return False, f"No successful cycle yet ({heartbeat} absent)"

    # Both sides tz-aware (UTC). Naive local wall-clock is not monotonic: at a DST
    # fall-back the local hour REPEATS, so a heartbeat written at 01:30 CDT and read at
    # 01:30 CST (a real hour later) subtracts to 0s. That fails OPEN — a stale collector
    # reports healthy. Absolute UTC on both sides is immune.
    age = datetime.datetime.now(datetime.UTC) - datetime.datetime.fromtimestamp(
        heartbeat.stat().st_mtime, tz=datetime.UTC
    )
    if age > timedelta(seconds=MAX_AGE_SECONDS):
        return (
            False,
            f"Last successful cycle {age.total_seconds():.0f}s ago "
            f"(max allowed: {MAX_AGE_SECONDS}s)",
        )
    return True, f"Healthy - last successful cycle {age.total_seconds():.0f}s ago"


def main() -> None:
    is_healthy, message = check_heartbeat()

    version = os.getenv("KIDDE_COLLECTOR_VERSION", "unknown")
    build_timestamp = os.getenv("KIDDE_COLLECTOR_BUILD_TIMESTAMP", "unknown")

    print(f"Health check: {'HEALTHY' if is_healthy else 'UNHEALTHY'}")
    print(f"Version: {version} (Built: {build_timestamp})")
    print(f"  - Last successful cycle: {message}")

    sys.exit(0 if is_healthy else 1)


if __name__ == "__main__":
    main()
