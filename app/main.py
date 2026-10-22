"""kidde-collector entrypoint.

Validates the environment, connects to InfluxDB, then runs the Kidde poll loop until
a SIGTERM/SIGINT triggers a graceful shutdown. Run with ``python -m app.main``.
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import UTC, datetime

from app.collector.poller import KiddeCollector
from app.collector.session import KiddeSession
from app.core import config
from app.core.logging_config import configure_logging
from app.storage.influxdb import InfluxDBStorage

# Named, not __name__: run as `python -m app.main` this module IS `__main__`, and the
# aggregator would see that instead of where the line came from.
logger = logging.getLogger("app.main")

# Credentials are logged as a fixed mask: any characters or the exact length would narrow
# a short password to a guess. The username is an identifier, partially masked.
_CREDENTIALS = ("PASSWORD", "TOKEN")
_IDENTIFIERS = ("USERNAME",)
_CREDENTIAL_MASK = "****"


def _obscure(value: str) -> str:
    """Partially mask an identifier; fully redact values too short to partially mask."""
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def validate_environment() -> None:
    """Log the effective config and fail fast if a required variable is missing."""
    logger.info("Environment variable settings:")
    for var, value in config.describe_settings().items():
        if value == "None":
            shown = "<unset>"
        elif any(s in var for s in _CREDENTIALS):
            shown = _CREDENTIAL_MASK if value else value
        elif any(s in var for s in _IDENTIFIERS):
            shown = _obscure(value)
        else:
            shown = value
        logger.info("  %s: %s", var, shown)

    missing = [v for v in config.REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        raise ValueError("Missing required environment variables")


async def main() -> None:
    logger.info(
        "Welcome to Kidde Collector! Current time: %s",
        datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
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
    configure_logging(
        service="kidde-collector", version=config.BUILD_VERSION, level=config.LOG_LEVEL
    )
    config.flush_warnings()
    try:
        with asyncio.Runner() as runner:
            runner.run(main())
    except ValueError as e:
        # A configuration error (missing variable, malformed InfluxDB URL): say which, then exit
        # 1. Without this record a bad URL exits silently.
        logger.exception("Startup aborted: %s", e)
        sys.exit(1)
