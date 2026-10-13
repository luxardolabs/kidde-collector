"""Environment-driven configuration for kidde-collector.

All settings come from ``KIDDE_COLLECTOR_*`` environment variables (fleet standard).
Secrets live in gitignored ``.env.dev`` / ``.env.prod`` (see ``.env.example``); the
bundled dev/demo stacks use the committed, non-secret ``.env.demo``.
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

# Module logger, never the root logger (ruff LOG015): a root-logger call implicitly
# configures the root handler on first use, which hijacks logging for anything that
# imports this module and bypasses the fleet handler app.core.logging_config installs.
logger = logging.getLogger(__name__)

# Config is read at IMPORT, before app.main's configure_logging() installs the fleet handler. A
# record logged with no handler anywhere goes to logging.lastResort: plain text on stderr, outside
# the JSON stream, so a log pipeline drops or mis-levels it. Hold those warnings until then.
_PENDING: list[str] = []


def _warn(msg: str, *args: object) -> None:
    """Warn now if logging is configured, else hold the warning for ``flush_warnings()``."""
    message = msg % args
    if logger.hasHandlers():
        logger.warning("%s", message)
    else:
        _PENDING.append(message)


def flush_warnings() -> None:
    """Log the warnings held since import; ``app.main`` calls it after configure_logging()."""
    while _PENDING:
        logger.warning("%s", _PENDING.pop(0))


def redact_url(url: str | None) -> str | None:
    """The URL with any ``user:password@`` credentials replaced, safe to log."""
    if not url:
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return parts._replace(netloc="***@" + parts.netloc.rsplit("@", 1)[1]).geturl()


class ConfigValidator:
    """Validates and coerces configuration values with bounds checking."""

    @staticmethod
    def validate_int(
        value: str,
        min_val: int | None = None,
        max_val: int | None = None,
        default: int | None = None,
    ) -> int:
        try:
            num = int(value)
        except (ValueError, TypeError) as e:
            if default is not None:
                _warn("Value '%s' is not an integer, using default %s", value, default)
                return default
            raise ValueError(f"Invalid integer value: {value}") from e
        if (min_val is not None and num < min_val) or (
            max_val is not None and num > max_val
        ):
            if default is not None:
                _warn(
                    "Value %s out of range [%s, %s], using default %s",
                    num,
                    min_val,
                    max_val,
                    default,
                )
                return default
            raise ValueError(f"Value {num} out of range [{min_val}, {max_val}]")
        return num

    @staticmethod
    def validate_bool(value: str, default: bool | None = None) -> bool:
        if value.lower() in ("true", "1", "yes", "on"):
            return True
        elif value.lower() in ("false", "0", "no", "off"):
            return False
        elif default is not None:
            _warn("Invalid boolean value '%s', using default %s", value, default)
            return default
        raise ValueError(f"Invalid boolean value: {value}")

    @staticmethod
    def validate_log_level(value: str, default: str = "INFO") -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        upper_value = value.upper()
        if upper_value in valid_levels:
            return upper_value
        _warn("Invalid log level '%s', using default %s", value, default)
        return default


def get_env_int(
    key: str, default: int, min_val: int | None = None, max_val: int | None = None
) -> int:
    return ConfigValidator.validate_int(
        os.getenv(key, str(default)), min_val, max_val, default
    )


def get_env_bool(key: str, default: bool) -> bool:
    return ConfigValidator.validate_bool(os.getenv(key, str(default).lower()), default)


def get_env_log_level(key: str, default: str = "INFO") -> str:
    return ConfigValidator.validate_log_level(os.getenv(key, default), default)


def _warn_if_insecure_url(name: str, url: str | None) -> None:
    """Warn when a URL uses cleartext http:// to a non-local host (token/data in the clear)."""
    if url and url.startswith("http://"):
        # urlsplit, not string slicing: `http://user:pass@host` would otherwise read `user`
        # as the host and log it.
        host = urlsplit(url).hostname or ""
        if host not in ("localhost", "127.0.0.1", "kidde_influxdb", "kidde_fake"):
            _warn(
                "%s uses http:// (cleartext) to non-local host '%s' — use https:// in "
                "production so the token and telemetry aren't sent in the clear.",
                name,
                host,
            )


# Build information (stamped into the image; see Dockerfile)
BUILD_VERSION = os.getenv("KIDDE_COLLECTOR_BUILD_VERSION", "dev")
BUILD_TIMESTAMP = os.getenv("KIDDE_COLLECTOR_BUILD_TIMESTAMP", "unknown")

# InfluxDB — where device metrics are written (the fleet's external InfluxDB in prod)
INFLUXDB_URL = os.getenv("KIDDE_COLLECTOR_INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("KIDDE_COLLECTOR_INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("KIDDE_COLLECTOR_INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("KIDDE_COLLECTOR_INFLUXDB_BUCKET")

# Kidde HomeSafe cloud account — the collector authenticates with these
KIDDE_USERNAME = os.getenv("KIDDE_COLLECTOR_KIDDE_USERNAME")
KIDDE_PASSWORD = os.getenv("KIDDE_COLLECTOR_KIDDE_PASSWORD")

# Kidde API base URL — env-overridable so the dev/demo/e2e stacks can target the
# harness emulator (harness/fake_kidde.py) instead of the real cloud.
API_BASE_URL = os.getenv(
    "KIDDE_COLLECTOR_API_BASE_URL", "https://api.homesafe.kidde.com/api/v4"
)

_warn_if_insecure_url("KIDDE_COLLECTOR_INFLUXDB_URL", INFLUXDB_URL)
_warn_if_insecure_url("KIDDE_COLLECTOR_API_BASE_URL", API_BASE_URL)

# Cookie session persistence (bind-mounted output dir survives restarts)
COOKIES_DIR = Path(os.getenv("KIDDE_COLLECTOR_COOKIES_DIR", "output"))

# Raw API-response capture (writes to the gitignored output/ folder)
EXPORT_FOLDER = os.getenv("KIDDE_COLLECTOR_EXPORT_FOLDER", "output")
WRITE_API_DATA = get_env_bool("KIDDE_COLLECTOR_WRITE_API_DATA", False)

# Poll cadence + HTTP timeouts (seconds)
FETCH_INTERVAL_SECONDS = get_env_int(
    "KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS", 60, min_val=10, max_val=3600
)
REQUEST_TIMEOUT = get_env_int(
    "KIDDE_COLLECTOR_REQUEST_TIMEOUT", 10, min_val=1, max_val=120
)
CONNECTION_TIMEOUT = get_env_int(
    "KIDDE_COLLECTOR_CONNECTION_TIMEOUT", 5, min_val=1, max_val=60
)

# Whether to also fetch per-location events (off by default)
GET_EVENTS = get_env_bool("KIDDE_COLLECTOR_GET_EVENTS", False)

# InfluxDB measurement name for device metrics
MEASUREMENT_DEVICE = os.getenv(
    "KIDDE_COLLECTOR_MEASUREMENT_DEVICE", "kidde_collector_device"
)

# The per-device nested metrics written as {name}_value / {name}_status points.
NESTED_ITEMS = ["iaq_temperature", "humidity", "hpa", "tvoc", "iaq", "co2"]

# Offline handling. Kidde keeps unresponsive devices in the account and returns their
# frozen last-known readings with these flags set — writing those every cycle draws
# misleading static lines. When SKIP_OFFLINE_MEASUREMENTS is on (default), an offline
# device (any OFFLINE_FLAG true) writes only a liveness point (online + the identity/
# status LIVENESS_FIELDS) and no environmental measurements, so its trends gap out.
SKIP_OFFLINE_MEASUREMENTS = get_env_bool(
    "KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS", True
)
OFFLINE_FLAGS = ["lost", "offline", "contact_lost"]
LIVENESS_FIELDS = ["last_seen", "lost", "offline", "contact_lost", "battery_state"]

# Logging
LOG_LEVEL = get_env_log_level("KIDDE_COLLECTOR_LOG_LEVEL", "INFO")

# Health check: the heartbeat file the poller touches in EXPORT_FOLDER after every cycle whose
# InfluxDB write succeeded, and its max age (s) for the container to report healthy
HEARTBEAT_FILENAME = ".last_success"
HEALTH_CHECK_MAX_AGE = get_env_int(
    "KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE", 300, min_val=30, max_val=3600
)

# Required environment variables (validated at startup in app/main.py)
REQUIRED_ENV_VARS = [
    "KIDDE_COLLECTOR_INFLUXDB_URL",
    "KIDDE_COLLECTOR_INFLUXDB_TOKEN",
    "KIDDE_COLLECTOR_INFLUXDB_ORG",
    "KIDDE_COLLECTOR_INFLUXDB_BUCKET",
    "KIDDE_COLLECTOR_KIDDE_USERNAME",
    "KIDDE_COLLECTOR_KIDDE_PASSWORD",
]


def describe_settings() -> dict[str, str]:
    """Effective config keyed by env-var name — single source of truth for the startup log."""
    return {
        "KIDDE_COLLECTOR_INFLUXDB_URL": str(redact_url(INFLUXDB_URL)),
        "KIDDE_COLLECTOR_INFLUXDB_ORG": str(INFLUXDB_ORG),
        "KIDDE_COLLECTOR_INFLUXDB_BUCKET": str(INFLUXDB_BUCKET),
        "KIDDE_COLLECTOR_INFLUXDB_TOKEN": str(INFLUXDB_TOKEN),
        "KIDDE_COLLECTOR_KIDDE_USERNAME": str(KIDDE_USERNAME),
        "KIDDE_COLLECTOR_KIDDE_PASSWORD": str(KIDDE_PASSWORD),
        "KIDDE_COLLECTOR_API_BASE_URL": str(redact_url(API_BASE_URL)),
        "KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS": str(FETCH_INTERVAL_SECONDS),
        "KIDDE_COLLECTOR_REQUEST_TIMEOUT": str(REQUEST_TIMEOUT),
        "KIDDE_COLLECTOR_CONNECTION_TIMEOUT": str(CONNECTION_TIMEOUT),
        "KIDDE_COLLECTOR_GET_EVENTS": str(GET_EVENTS),
        "KIDDE_COLLECTOR_WRITE_API_DATA": str(WRITE_API_DATA),
        "KIDDE_COLLECTOR_EXPORT_FOLDER": EXPORT_FOLDER,
        "KIDDE_COLLECTOR_COOKIES_DIR": str(COOKIES_DIR),
        "KIDDE_COLLECTOR_MEASUREMENT_DEVICE": MEASUREMENT_DEVICE,
        "KIDDE_COLLECTOR_SKIP_OFFLINE_MEASUREMENTS": str(SKIP_OFFLINE_MEASUREMENTS),
        "KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE": str(HEALTH_CHECK_MAX_AGE),
        "KIDDE_COLLECTOR_LOG_LEVEL": LOG_LEVEL,
    }
