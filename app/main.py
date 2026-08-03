"""kidde-collector entrypoint.

Validates the environment, connects to InfluxDB, then runs the Kidde poll loop until
a SIGTERM/SIGINT triggers a graceful shutdown. Run with ``python -m app.main``.
"""

import asyncio
import contextlib
import os
import signal
import sys
from datetime import datetime

from app.collector.poller import KiddeCollector
from app.collector.session import KiddeSession
from app.core import config
from app.storage.influxdb import InfluxDBStorage
from app.utils.logging import logger

_SENSITIVE = ("PASSWORD", "TOKEN", "USERNAME")


def _obscure(value: str) -> str:
    """Partially mask a secret; fully redact values too short to partially mask."""
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def validate_environment() -> None:
    """Log the effective config and fail fast if a required variable is missing."""
    logger.info("Environment variable settings:")
    for var, value in config.describe_settings().items():
        shown = _obscure(value) if any(s in var for s in _SENSITIVE) else value
        logger.info("  %s: %s", var, shown)

    missing = [v for v in config.REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        raise ValueError("Missing required environment variables")


async def main() -> None:
    logger.info(
        "Welcome to Kidde Collector! Current time: %s",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    logger.info("Build Version: %s", config.BUILD_VERSION)
    logger.info("Build Timestamp: %s", config.BUILD_TIMESTAMP)

    validate_environment()

    storage = InfluxDBStorage()
    await storage.connect()
    session = KiddeSession()
    await session.connect()
    collector = KiddeCollector(session, storage)

    shutdown_event = asyncio.Event()

    def _handle_signal(signum: int) -> None:
        logger.info("Received signal %s, initiating graceful shutdown...", signum)
        shutdown_event.set()

    # add_signal_handler is the asyncio-correct way to catch signals on the running loop.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler is unavailable on some platforms (e.g. Windows)
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        logger.info("Starting Kidde Collector poll loop")
        await collector.run(shutdown_event)
    finally:
        logger.info("Performing cleanup...")
        await session.close()
        await storage.close()
        logger.info("Kidde Collector shutdown complete")


if __name__ == "__main__":
    try:
        with asyncio.Runner() as runner:
            runner.run(main())
    except ValueError:
        sys.exit(1)
