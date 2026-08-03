#!/usr/bin/env python3
"""Docker health check for kidde-collector (see the Dockerfile HEALTHCHECK).

Two strategies:
  - When raw capture is enabled (KIDDE_COLLECTOR_WRITE_API_DATA=true), verify the
    daily ``api_data_<date>.json`` capture file is fresh.
  - Otherwise (the common case) verify the process/interpreter is alive.

Exit 0 = healthy, 1 = unhealthy.
"""

import datetime
import os
import sys
from datetime import timedelta
from pathlib import Path


def _int_env(key: str, default: int, lo: int, hi: int) -> int:
    """Parse a bounded int from the env, falling back to the default (never raises)."""
    try:
        value = int(os.getenv(key, str(default)))
    except TypeError, ValueError:
        return default
    return max(lo, min(hi, value))


MAX_AGE_SECONDS = _int_env("KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE", 300, 30, 3600)
OUTPUT_DIR = os.getenv("KIDDE_COLLECTOR_EXPORT_FOLDER", "output")


def check_recent_capture() -> tuple[bool, str]:
    """Verify a recent ``api_data_*.jsonl`` capture exists and is fresh."""
    output_path = Path(OUTPUT_DIR)
    if not output_path.exists():
        return False, f"Output directory {OUTPUT_DIR} does not exist"

    candidates = list(output_path.glob("api_data_*.jsonl"))
    if not candidates:
        return False, "No api_data_*.jsonl capture file found"

    capture = max(candidates, key=lambda f: f.stat().st_mtime)
    file_age = datetime.datetime.now() - datetime.datetime.fromtimestamp(
        capture.stat().st_mtime
    )
    if file_age > timedelta(seconds=MAX_AGE_SECONDS):
        return (
            False,
            f"Capture {capture.name} is {file_age.total_seconds():.0f}s old "
            f"(max allowed: {MAX_AGE_SECONDS}s)",
        )
    return True, f"Healthy - last capture update {file_age.total_seconds():.0f}s ago"


def check_process_alive() -> tuple[bool, str]:
    """Basic liveness — if this interpreter runs at all in the container, it's healthy."""
    return True, "Process healthy"


def main() -> None:
    if os.getenv("KIDDE_COLLECTOR_WRITE_API_DATA", "false").lower() == "true":
        is_healthy, message = check_recent_capture()
        label = "Capture freshness"
    else:
        is_healthy, message = check_process_alive()
        label = "Process check"

    version = os.getenv("KIDDE_COLLECTOR_VERSION", "unknown")
    build_timestamp = os.getenv("KIDDE_COLLECTOR_BUILD_TIMESTAMP", "unknown")

    print(f"Health check: {'HEALTHY' if is_healthy else 'UNHEALTHY'}")
    print(f"Version: {version} (Built: {build_timestamp})")
    print(f"  - {label}: {message}")

    sys.exit(0 if is_healthy else 1)


if __name__ == "__main__":
    main()
