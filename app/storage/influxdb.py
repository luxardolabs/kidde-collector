"""InfluxDB storage for kidde-collector.

Uses the asyncio-native ``InfluxDBClientAsync`` (aiohttp) — the fleet ingestion
standard: one client per process, opened inside the running loop in ``connect()``,
and every poll cycle's points handed to a single awaited ``write`` (the poll cycle IS
the batch). No background Rx thread, no buffer to flush. This same v2 ``/api/v2/write``
path works against InfluxDB 2.x today and 3.x later.

Each device becomes one main point in the ``kidde_collector_device`` measurement:

    tags   : id, serial_number, location_id, location_label, label
    fields : an ``online`` liveness flag; every scalar device attribute; plus per-metric
             ``{name}_value`` / ``{name}_status`` for the air-quality panel
             (iaq_temperature, humidity, hpa, tvoc, iaq, co2). Offline devices write only
             the liveness fields (see app.core.config OFFLINE_FLAGS / LIVENESS_FIELDS).
"""

import contextlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from influxdb_client import Point
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.write_api_async import WriteApiAsync

from app.collector.client import KiddeDataset
from app.core import config
from app.utils.logging import logger


def _age_seconds(timestamp: Any) -> float | None:
    """Seconds since an ISO-8601 timestamp, or None if unparseable.

    Kidde returns ISO-8601 with a trailing ``Z`` and up to nanosecond precision (which
    ``datetime.fromisoformat`` can't take), so fractional seconds are truncated to
    microseconds. Used to emit numeric ages (last_seen, last_test_time) so a dashboard can
    color cells by staleness — a string timestamp can't be threshold-colored, and the
    InfluxDB point time can't either since offline devices still get a liveness write each cycle.
    """
    if not isinstance(timestamp, str):
        return None
    try:
        ts = re.sub(r"(\.\d{6})\d+", r"\1", timestamp.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except Exception:
        return None


class InfluxDBStorage:
    """Async InfluxDB writer. Validate in ``__init__``, open in ``connect()``."""

    def __init__(self) -> None:
        url, token, org, bucket = (
            config.INFLUXDB_URL,
            config.INFLUXDB_TOKEN,
            config.INFLUXDB_ORG,
            config.INFLUXDB_BUCKET,
        )
        if not (url and token and org and bucket):
            raise ValueError(
                "Missing required InfluxDB parameter (url/token/org/bucket)"
            )
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid InfluxDB URL format: {url}")

        self.url: str = url
        self.token: str = token
        self.bucket: str = bucket
        self.org: str = org
        self.client: InfluxDBClientAsync | None = None
        self.write_api: WriteApiAsync | None = None

    async def connect(self) -> None:
        """Open the async InfluxDB connection and fail fast if unreachable.

        ``ping()`` is unauthenticated, so it only proves the server is reachable and
        healthy — auth/bucket problems surface on the first write (see ``_log_write_error``)
        and are logged loudly but NOT fatal, so a transient blip doesn't crash-loop the
        collector.
        """
        self.client = InfluxDBClientAsync(
            url=self.url, token=self.token, org=self.org, enable_gzip=True
        )
        try:
            healthy = await self.client.ping()
        except Exception as e:
            await self._close_client()
            logger.error("=" * 60)
            logger.error("InfluxDB Connection Failed")
            logger.error("Could not reach InfluxDB at: %s", self.url)
            logger.error("Error: %s: %s", type(e).__name__, e)
            logger.error(
                "Verify InfluxDB is running, the URL/port are correct, and reachable."
            )
            logger.error("=" * 60)
            raise SystemExit(1) from None

        if not healthy:
            await self._close_client()
            logger.error("InfluxDB health check failed at %s", self.url)
            raise SystemExit(1)

        self.write_api = self.client.write_api()
        try:
            version = await self.client.version()
            logger.info("Connected to InfluxDB (server %s)", version)
        except Exception:
            logger.info("Connected to InfluxDB")

    async def _close_client(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.close()
            self.client = None
            self.write_api = None

    async def write_dataset(self, data: KiddeDataset) -> None:
        """Write every device in the dataset — one awaited batch for the poll cycle."""
        if not data.devices:
            logger.debug("No devices in dataset; nothing to write")
            return

        points: list[Point] = []
        for device in data.devices.values():
            location = data.locations.get(device["location_id"], {})
            points.extend(self._device_points(device, location.get("label", "")))
        await self._write(points)

    async def _write(self, points: list[Point]) -> None:
        """Write the cycle's points in a single awaited request.

        Tolerates InfluxDB partial writes: if a numeric field ever disagrees on type
        across devices, InfluxDB returns 422 while still persisting the valid points —
        logged as a warning, not a cycle failure.
        """
        if not points or self.write_api is None:
            return
        try:
            if logger.isEnabledFor(logging.DEBUG):
                for point in points:
                    logger.debug("Writing point: %s", point.to_line_protocol())
            await self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.debug("Wrote %d points to InfluxDB", len(points))
        except InfluxDBError as e:
            self._log_write_error(e)
        except Exception as e:
            logger.error("Error writing to InfluxDB: %s", e)

    @staticmethod
    def _log_write_error(error: InfluxDBError) -> None:
        """Log an InfluxDB write failure with actionable guidance."""
        status = getattr(getattr(error, "response", None), "status", None)
        if status == 401:
            logger.error(
                "InfluxDB rejected the write (401 Unauthorized) — check that the token has "
                "write access to the bucket/org."
            )
        elif status == 404:
            logger.error(
                "InfluxDB write failed (404 Not Found) — bucket or org does not exist."
            )
        elif status == 422:
            logger.warning(
                "InfluxDB partial write (some fields dropped on type conflict): %s",
                getattr(error, "message", None) or error,
            )
        else:
            logger.error("InfluxDB write failed: %s", error)

    async def close(self) -> None:
        """Release the aiohttp session on shutdown (no background buffer to flush)."""
        await self._close_client()

    @staticmethod
    def _device_points(device: dict[str, Any], location_label: str) -> list[Point]:
        """Build the points for one device.

        Every device gets an ``online`` liveness field (derived from Kidde's lost/offline/
        contact_lost flags) and numeric ages for last_seen / last_test_time. An ONLINE
        device writes its full main point (all scalar fields) plus the nested value/status
        metric points. An OFFLINE device (when SKIP_OFFLINE_MEASUREMENTS is set) writes ONLY
        a liveness point — no environmental measurements — so Kidde's frozen last-known
        readings don't draw static lines; the trends gap out instead.

        Numeric fields are written as float so a field Kidde reports as int on some devices
        and float on others (e.g. ``accuracy``) keeps one consistent InfluxDB type — a
        mismatch makes InfluxDB reject the whole point. Booleans (``int`` subclasses in
        Python) and strings are written as-is.
        """

        def _tagged() -> Point:
            return (
                Point(config.MEASUREMENT_DEVICE)
                .tag("id", str(device["id"]))
                .tag("serial_number", device["serial_number"])
                .tag("location_id", str(device["location_id"]))
                .tag("location_label", location_label)
                .tag("label", device["label"])
            )

        def _num(value: Any) -> Any:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return float(value)
            return value

        # One timestamp for all of a device's points this cycle, so the main point and
        # its nested metric points reliably land on the same row (don't rely on the
        # server assigning a single write-time).
        now = datetime.now(UTC)
        online = not any(bool(device.get(flag)) for flag in config.OFFLINE_FLAGS)
        main_point = _tagged().time(now).field("online", online)

        seen_age = _age_seconds(device.get("last_seen"))
        if seen_age is not None:
            main_point = main_point.field("last_seen_age_seconds", seen_age)

        test_age = _age_seconds(device.get("last_test_time"))
        if test_age is not None:
            main_point = main_point.field("last_test_time_age_seconds", test_age)

        if not online and config.SKIP_OFFLINE_MEASUREMENTS:
            # Liveness only — no environmental measurements for an unresponsive device.
            for key in config.LIVENESS_FIELDS:
                value = device.get(key)
                if isinstance(value, (int, float, bool, str)):
                    main_point = main_point.field(key, _num(value))
            return [main_point]

        for key, value in device.items():
            if isinstance(value, (int, float, bool, str)):
                main_point = main_point.field(key, _num(value))

        points = [main_point]
        for item in config.NESTED_ITEMS:
            nested = device.get(item)
            if isinstance(nested, dict) and "value" in nested and "status" in nested:
                points.append(
                    _tagged()
                    .time(now)
                    .field(f"{item}_value", _num(nested["value"]))
                    .field(f"{item}_status", nested["status"])
                )
        return points
