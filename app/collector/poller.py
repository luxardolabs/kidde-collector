"""The Kidde collection loop.

Each cycle authenticates (reusing cached cookies), fetches the location/device dataset,
optionally dumps the raw response, and writes it to InfluxDB. On an auth error (expired
session cookie) it clears the cached cookies so the next cycle re-authenticates.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiofiles

from app.collector.client import KiddeClientAuthError, KiddeLoginRejectedError
from app.collector.session import KiddeSession
from app.core import config
from app.storage.influxdb import InfluxDBStorage

logger = logging.getLogger(__name__)


class KiddeCollector:
    """Polls the Kidde HomeSafe API on an interval and writes metrics to InfluxDB."""

    def __init__(self, session: KiddeSession, storage: InfluxDBStorage) -> None:
        self.session = session
        self.storage = storage
        try:
            Path(config.EXPORT_FOLDER).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.exception("Error creating export directory: %s", e)
            raise RuntimeError(f"Error creating export directory: {e}") from e
        # The export folder is a persistent volume, so a heartbeat left by the PREVIOUS
        # container would vouch for this one until it aged out: a new build that cannot log
        # in would report healthy through a deploy's smoke. Health starts unproven.
        self._heartbeat = Path(config.EXPORT_FOLDER) / config.HEARTBEAT_FILENAME
        self._heartbeat.unlink(missing_ok=True)
        # Consecutive rejected logins, and when the next login may be tried (monotonic).
        self._login_rejections = 0
        self._login_retry_at = 0.0
        logger.info("KiddeCollector initialized")

    def _login_backoff_seconds(self) -> float:
        """The wait after the latest rejected login: doubling per rejection, capped."""
        # The exponent is bounded too: a float power overflows past 2.0**1023.
        doublings = min(self._login_rejections - 1, 32)
        return min(
            config.LOGIN_BACKOFF_BASE_SECONDS * 2.0**doublings,
            config.LOGIN_BACKOFF_MAX_SECONDS,
        )

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Run the poll loop until the shutdown event is set."""
        while not shutdown_event.is_set():
            start_time = time.monotonic()
            logger.info("Starting processing cycle")

            try:
                await self._collect_once()
            except KiddeLoginRejectedError as e:
                self._login_rejections += 1
                delay = self._login_backoff_seconds()
                self._login_retry_at = time.monotonic() + delay
                logger.exception(
                    "Kidde rejected the login (%d in a row): %s. Check "
                    "KIDDE_COLLECTOR_KIDDE_USERNAME / KIDDE_COLLECTOR_KIDDE_PASSWORD; the "
                    "next login attempt is in %.0f seconds",
                    self._login_rejections,
                    e,
                    delay,
                )
            except KiddeClientAuthError:
                logger.warning(
                    "Session expired (401/403); clearing cookies to re-login next cycle"
                )
                self.session.invalidate()
            # swallowed-exceptions: THE poll-loop resilience contract (CLAUDE.md: "the loop
            # continues despite individual failures"). One bad cycle — a Kidde outage, a
            # malformed payload — must not end the process; the next interval retries. The
            # error is logged with its traceback (the JSON line's `exception` field), so
            # nothing vanishes silently. Re-raising here would exit the collector on any
            # transient.
            except Exception as e:
                logger.exception("An error occurred: %s", e)

            elapsed = time.monotonic() - start_time
            logger.info("Processing cycle completed in %.2f seconds", elapsed)

            sleep_duration = max(0, config.FETCH_INTERVAL_SECONDS - elapsed)
            logger.info("Sleeping for %.2f seconds until next cycle", sleep_duration)
            # TimeoutError is normal here: slept the full interval, loop again.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_duration)

    async def _collect_once(self) -> None:
        # After a rejected login, wait out the backoff: each attempt is a failed login
        # against the owner's account.
        wait = self._login_retry_at - time.monotonic()
        if self._login_rejections and wait > 0:
            logger.info("Login backoff: next Kidde login attempt in %.0f seconds", wait)
            return
        client = await self.session.get_client()
        if client is None:
            raise RuntimeError("Failed to create KiddeClient")
        self._login_rejections = 0

        data = await client.get_data(get_devices=True, get_events=config.GET_EVENTS)

        if config.WRITE_API_DATA:
            await self._dump_raw(asdict(data))

        # Only a cycle that reached InfluxDB proves the collector works (app.health.check).
        if await self.storage.write_dataset(data):
            await asyncio.to_thread(self._heartbeat.touch)
        logger.info("Processed %d devices", len(data.devices or {}))

    async def _dump_raw(self, serializable_data: dict[str, Any]) -> None:
        current_date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        json_file_name = Path(config.EXPORT_FOLDER) / f"api_data_{current_date}.jsonl"
        # Newline-delimited JSON — one compact object per cycle (parseable; date-partitioned).
        # blocking-io: aiofiles.open is the ASYNC file API, not pathlib.Path.open — the
        # append is awaited and never blocks the loop. Sweep matches on the method name.
        async with aiofiles.open(json_file_name, "a") as f:
            await f.write(json.dumps(serializable_data) + "\n")
        logger.debug("Raw API data saved to %s", json_file_name)
