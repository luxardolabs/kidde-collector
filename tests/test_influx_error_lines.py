"""An InfluxDB write failure is logged with InfluxDB's own message, in JSON and text lines.

A real rejection from InfluxDBClientAsync raises an ApiException whose `.response` wraps
the live aiohttp ClientResponse, an object graph holding a reference cycle. The operator
needs the status and InfluxDB's message on the `InfluxDB write failed: %s` and
`Error writing to InfluxDB: %s` lines, so the logging module must write such an exception
as its text: only a validation error's input is withheld, and none is involved here.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from influxdb_client import Point
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.core.logging_config import JsonFormatter, TextFormatter
from app.storage.influxdb import InfluxDBStorage

INFLUX_MESSAGE = "unable to parse 'kidde_collector_device x=': missing field value"


class _Influx400(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"code": "invalid", "message": INFLUX_MESSAGE}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def influx_url() -> Iterator[str]:
    srv = HTTPServer(("127.0.0.1", 0), _Influx400)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def _while_client_open(url: str, use: Callable[[InfluxDBError], str]) -> str:
    """Make a real write fail and log it while the client is still open, as kidde's
    long-lived `InfluxDBStorage` client is when `_write`'s except arm logs."""

    async def run() -> str:
        client = InfluxDBClientAsync(url=url, token="t", org="o", enable_gzip=True)
        try:
            await client.write_api().write(
                bucket="b",
                org="o",
                record=[Point("kidde_collector_device").field("x", 1)],
            )
        except InfluxDBError as e:
            return use(e)
        finally:
            await client.close()
        raise AssertionError("the fake InfluxDB accepted the write")

    return asyncio.run(run())


def _formatted(formatter: logging.Formatter, call: Callable[[], None]) -> str:
    out: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            out.append(self.format(record))

    lg = logging.getLogger("app.storage.influxdb")
    h = _Cap()
    h.setFormatter(formatter)
    lg.addHandler(h)
    try:
        call()
    finally:
        lg.removeHandler(h)
    return "\n".join(out)


@pytest.mark.parametrize(
    "formatter",
    [JsonFormatter(service="kidde-collector", version="t"), TextFormatter()],
    ids=["json", "text"],
)
def test_influx_write_failure_line_keeps_influxdbs_message(
    influx_url: str, formatter: logging.Formatter
) -> None:
    line = _while_client_open(
        influx_url,
        lambda err: _formatted(
            formatter, lambda: InfluxDBStorage._log_write_error(err)
        ),
    )
    assert "withheld" not in line, line
    assert "missing field value" in line, line


def test_catch_all_write_error_traceback_keeps_the_exception_text(
    influx_url: str,
) -> None:
    logger = logging.getLogger("app.storage.influxdb")

    def log(err: InfluxDBError) -> str:
        def call() -> None:
            try:
                raise err
            except InfluxDBError as e:
                logger.exception("Error writing to InfluxDB: %s", e)

        return _formatted(JsonFormatter(service="kidde-collector", version="t"), call)

    line = json.loads(_while_client_open(influx_url, log))
    assert "withheld" not in line["message"], line["message"]
    assert "missing field value" in line["exception"], line["exception"]


class _Node:
    def __init__(self) -> None:
        self.me = self


class _CarriesNode(RuntimeError):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.node = _Node()


def test_plain_exception_with_a_reference_cycle_is_not_withheld() -> None:
    """The minimal shape: no pydantic, one self-referencing attribute."""
    err = _CarriesNode("disk full on /var/lib/influxdb")
    logger = logging.getLogger("app.storage.influxdb")
    line = json.loads(
        _formatted(
            JsonFormatter(service="kidde-collector", version="t"),
            lambda: logger.error("InfluxDB write failed: %s", err),
        )
    )
    assert line["message"] == "InfluxDB write failed: disk full on /var/lib/influxdb"
