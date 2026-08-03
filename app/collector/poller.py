"""The Kidde collection loop.

Each cycle authenticates (reusing cached cookies), fetches the location/device dataset,
optionally dumps the raw response, and writes it to InfluxDB. On an auth error (expired
session cookie) it clears the cached cookies so the next cycle re-authenticates.
"""

import asyncio
import contextlib
import datetime
import json
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiofiles

from app.collector.client import KiddeClientAuthError
from app.collector.session import KiddeSession
from app.core import config
from app.storage.influxdb import InfluxDBStorage
from app.utils.logging import logger


class KiddeCollector:
    """Polls the Kidde HomeSafe API on an interval and writes metrics to InfluxDB."""

    def __init__(self, session: KiddeSession, storage: InfluxDBStorage) -> None:
        self.session = session
        self.storage = storage
        try:
            Path(config.EXPORT_FOLDER).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Error creating export directory: %s", e)
            raise RuntimeError(f"Error creating export directory: {e}") from e
        logger.info("KiddeCollector initialized")

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Run the poll loop until the shutdown event is set."""
        while not shutdown_event.is_set():
            start_time = time.monotonic()
            logger.info("Starting processing cycle")

            try:
                await self._collect_once()
            except KiddeClientAuthError:
                logger.warning(
                    "Session expired (401/403); clearing cookies to re-login next cycle"
                )
                self.session.invalidate()
            except Exception as e:
                logger.error("An error occurred: %s", e)
                logger.debug("%s", traceback.format_exc())

            elapsed = time.monotonic() - start_time
            logger.info("Processing cycle completed in %.2f seconds", elapsed)

            sleep_duration = max(0, config.FETCH_INTERVAL_SECONDS - elapsed)
            logger.info("Sleeping for %.2f seconds until next cycle", sleep_duration)
            # TimeoutError is normal here: slept the full interval, loop again.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_duration)

    async def _collect_once(self) -> None:
        client = await self.session.get_client()
        if client is None:
            raise RuntimeError("Failed to create KiddeClient")

        data = await client.get_data(get_devices=True, get_events=config.GET_EVENTS)

        if config.WRITE_API_DATA:
            await self._dump_raw(asdict(data))

        await self.storage.write_dataset(data)
        logger.info("Processed %d devices", len(data.devices or {}))

    async def _dump_raw(self, serializable_data: dict[str, Any]) -> None:
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        json_file_name = Path(config.EXPORT_FOLDER) / f"api_data_{current_date}.jsonl"
        # Newline-delimited JSON — one compact object per cycle (parseable; date-partitioned).
        async with aiofiles.open(json_file_name, "a") as f:
            await f.write(json.dumps(serializable_data) + "\n")
        logger.debug("Raw API data saved to %s", json_file_name)
