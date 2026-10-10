"""The collector logs through the fleet module (app/core/logging_config.py), wired in app.main.

What matters downstream is the line the aggregator receives: one JSON object per line, carrying
the service name and version, with caller fields under `attributes`. If the entrypoint stops
calling configure_logging(), records fall back to Python's default stderr handler and the
aggregator silently loses them — this runs the real entrypoint and reads what it writes.
"""

import io
import json
import logging
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from app.core import config
from app.core import logging_config as lc

REPO = Path(__file__).resolve().parent.parent


def _run_entrypoint(**env: str) -> subprocess.CompletedProcess[str]:
    # No KIDDE_COLLECTOR_* requirements set: the collector logs its settings, logs the
    # missing-variable error and exits 1 before touching the network.
    clean = {
        k: v for k, v in os.environ.items() if not k.startswith("KIDDE_COLLECTOR_")
    }
    clean.update({"KIDDE_COLLECTOR_BUILD_VERSION": "9.9.9-test", **env})
    return subprocess.run(
        [sys.executable, "-m", "app.main"],
        cwd=REPO,
        env=clean,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# Filled-in required settings with recognisable secrets, so a test can grep for a leak.
_SECRETS = {
    "KIDDE_COLLECTOR_INFLUXDB_TOKEN": "not-a-real-token",
    "KIDDE_COLLECTOR_INFLUXDB_ORG": "o",
    "KIDDE_COLLECTOR_INFLUXDB_BUCKET": "b",
    "KIDDE_COLLECTOR_KIDDE_USERNAME": "me@example.com",
    "KIDDE_COLLECTOR_KIDDE_PASSWORD": "not-a-real-password",
}


def _docker_logs(**env: str) -> list[str]:
    """What `docker logs` shows: the entrypoint's stdout AND stderr, merged."""
    clean = {
        k: v for k, v in os.environ.items() if not k.startswith("KIDDE_COLLECTOR_")
    }
    clean.update(env)
    out = subprocess.run(
        [sys.executable, "-m", "app.main"],
        cwd=REPO,
        env=clean,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def _not_json(lines: list[str]) -> list[str]:
    bad = []
    for line in lines:
        try:
            json.loads(line)
        except ValueError:
            bad.append(line)
    return bad


def _json_lines(out: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_entrypoint_writes_one_json_object_per_line() -> None:
    result = _run_entrypoint()
    assert result.returncode == 1
    lines = _json_lines(result.stdout)
    assert lines, f"no log lines on stdout; stderr was: {result.stderr!r}"
    for line in lines:
        assert line["service"] == "kidde-collector"
        assert line["version"] == "9.9.9-test"
        assert str(line["timestamp"]).endswith("Z")
    errors = [line for line in lines if line["level"] == "ERROR"]
    assert any(
        "Missing required environment variables" in str(e["message"]) for e in errors
    )
    assert errors[0]["logger"] == "app.main"


def test_log_level_setting_reaches_the_root_logger() -> None:
    result = _run_entrypoint(KIDDE_COLLECTOR_LOG_LEVEL="ERROR")
    levels = {line["level"] for line in _json_lines(result.stdout)}
    assert levels == {"ERROR"}, "INFO lines leaked past KIDDE_COLLECTOR_LOG_LEVEL=ERROR"


def test_log_format_text_is_readable_not_json() -> None:
    result = _run_entrypoint(LOG_FORMAT="text")
    first = result.stdout.splitlines()[0]
    assert not first.startswith("{")
    assert "Welcome to Kidde Collector" in first


# ── The module's contract, in process ─────────────────────────────────────────────────────────
# The fields below are what the aggregator keys on; the redactor and collision behaviour are what
# keep a log call from crashing or leaking. The fixture restores the global state that
# configure_logging() replaces (root handlers and level, Logger.makeRecord).


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    # Registered so monkeypatch puts the stock makeRecord back after configure_logging swaps it.
    monkeypatch.setattr(logging.Logger, "makeRecord", logging.Logger.makeRecord)
    out = io.StringIO()
    for var in ("LOG_FORMAT", "LOG_LEVEL", "LOG_MODULE_LEVELS"):
        monkeypatch.delenv(var, raising=False)
    _stream.append(out)
    yield out
    _stream.clear()
    root.handlers[:] = handlers
    root.setLevel(level)


_stream: list[io.StringIO] = []


def _configure(**kwargs: Any) -> None:
    lc.configure_logging(service="svc", version="1.2.3", level="DEBUG", **kwargs)
    # The handler binds sys.stdout when it is built; pytest's capture owns that, so read
    # the line from a stream of the test's own.
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(_stream[0])


def _lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


class TestJsonLine:
    def test_envelope_and_attributes(self, stream: io.StringIO) -> None:
        _configure(environment="prod")
        logging.getLogger("app.x").info("wrote %d", 3, extra={"device_id": 7})
        (line,) = _lines(stream)
        assert line["message"] == "wrote 3"
        assert line["logger"] == "app.x"
        assert line["service"] == "svc" and line["version"] == "1.2.3"
        assert line["environment"] == "prod"
        assert line["function"] == "test_envelope_and_attributes"
        assert line["attributes"] == {"device_id": 7}

    def test_colliding_extra_key_is_kept_not_raised(self, stream: io.StringIO) -> None:
        _configure()
        logging.getLogger("app.x").info("m", extra={"name": "Basement", "module": "m"})
        (line,) = _lines(stream)
        assert line["logger"] == "app.x"
        assert line["attributes"] == {"name": "Basement", "module": "m"}

    def test_no_attributes_key_without_fields(self, stream: io.StringIO) -> None:
        _configure()
        logging.getLogger("app.x").info("plain")
        assert "attributes" not in _lines(stream)[0]

    def test_log_context_and_trace_ids(self, stream: io.StringIO) -> None:
        _configure()
        with lc.log_context(cycle=4, trace_id="t1"):
            logging.getLogger("app.x").info("m", extra={"otelSpanID": "s1"})
        logging.getLogger("app.x").info("after")
        first, after = _lines(stream)
        assert first["trace_id"] == "t1" and first["span_id"] == "s1"
        assert first["attributes"] == {"cycle": 4}
        assert "attributes" not in after

    def test_exception_and_stack(self, stream: io.StringIO) -> None:
        _configure()
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("app.x").exception("failed", stack_info=True)
        (line,) = _lines(stream)
        assert line["exception_type"] == "ValueError"
        assert "ValueError: boom" in line["exception"]
        assert "Stack (most recent call last)" in line["stack"]

    def test_unserialisable_values_still_write_a_line(
        self, stream: io.StringIO
    ) -> None:
        class BadRepr:
            def __repr__(self) -> str:
                raise RuntimeError

        _configure()
        extra = {"nan": float("nan"), "inf": float("inf"), "ok": 1.5, "bad": BadRepr()}
        logging.getLogger("app.x").info("m", extra=extra)
        (line,) = _lines(stream)
        assert line["attributes"]["nan"] == "nan"
        assert line["attributes"]["inf"] == "inf"
        assert line["attributes"]["ok"] == 1.5
        assert "BadRepr object" in line["attributes"]["bad"]

    def test_uvicorn_colour_duplicate_is_dropped(self, stream: io.StringIO) -> None:
        _configure()
        logging.getLogger("app.x").info("m", extra={"color_message": "\x1b[1m"})
        assert "attributes" not in _lines(stream)[0]


class TestRedactor:
    def test_redactor_scrubs_envelope_and_attributes(self, stream: io.StringIO) -> None:
        def scrub(part: dict[str, Any]) -> Mapping[str, Any]:
            hidden = ("email", "message")
            return {k: ("***" if k in hidden else v) for k, v in part.items()}

        _configure(redact=scrub)
        logging.getLogger("app.x").info("hi a@b.c", extra={"email": "a@b.c", "n": 1})
        (line,) = _lines(stream)
        assert line["message"] == "***"
        assert line["attributes"] == {"email": "***", "n": 1}

    def test_raising_redactor_fails_closed(self, stream: io.StringIO) -> None:
        def scrub(part: dict[str, Any]) -> Mapping[str, Any]:
            raise KeyError("x")

        _configure(redact=scrub)
        logging.getLogger("app.x").info("secret", extra={"token": "t"})
        (line,) = _lines(stream)
        assert line["redaction_error"] == "KeyError"
        assert "secret" not in json.dumps(line) and "attributes" not in line

    def test_non_mapping_redactor_fails_closed(self, stream: io.StringIO) -> None:
        def scrub(part: dict[str, Any]) -> Any:
            return None

        _configure(redact=scrub)
        logging.getLogger("app.x").info("secret")
        (line,) = _lines(stream)
        assert line["redaction_error"] == "returned NoneType, not a mapping"

    def test_format_failure_report_withholds_content(
        self, stream: io.StringIO, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _configure(redact=lambda part: part)
        logging.getLogger("app.x").info("%d", "not-a-number")
        err = capsys.readouterr().err
        assert "record content withheld" in err and "not-a-number" not in err

    def test_format_failure_without_redactor_uses_stock_report(
        self, stream: io.StringIO, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _configure()
        logging.getLogger("app.x").info("%d", "not-a-number")
        assert "Logging error" in capsys.readouterr().err


class TestTextFormat:
    def test_text_line_appends_fields(
        self, stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_FORMAT", "text")
        _configure()
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("app.x").exception(
                "failed", extra={"n": 1}, stack_info=True
            )
        out = stream.getvalue()
        assert "ERROR app.x failed n=1" in out
        assert "ValueError: boom" in out and "Stack (most recent call last)" in out

    def test_text_line_without_fields(
        self, stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LOG_FORMAT", "text")
        _configure()
        logging.getLogger("app.x").info("plain")
        assert stream.getvalue().rstrip().endswith("INFO app.x plain")


class TestLevels:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("warning", logging.WARNING), (" 15 ", 15), (30, 30), ("bogus", logging.INFO)],
    )
    def test_level_parsing_never_crashes(self, raw: str | int, expected: int) -> None:
        assert lc._level(raw) == expected

    def test_level_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert lc._level(None) == logging.ERROR

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                '{"app.a": "DEBUG", "app.b": 10, "app.c": true}',
                {"app.a": "DEBUG", "app.b": 10},
            ),
            ("[1, 2]", {}),
            ("not json", {}),
        ],
    )
    def test_module_levels_env(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: dict[str, Any]
    ) -> None:
        monkeypatch.setenv("LOG_MODULE_LEVELS", raw)
        assert lc._env_levels() == expected

    def test_per_logger_levels_and_idempotence(self, stream: io.StringIO) -> None:
        _configure(levels={"app.quiet": "ERROR"})
        _configure(levels={"app.quiet": "ERROR"})
        assert len(logging.getLogger().handlers) == 1
        logging.getLogger("app.quiet").warning("dropped")
        logging.getLogger("app.loud").debug("kept")
        assert [line["message"] for line in _lines(stream)] == ["kept"]


class TestEdges:
    def test_exc_info_without_an_exception_type(self, stream: io.StringIO) -> None:
        _configure()
        logging.getLogger("app.x").info("m", exc_info=(None, None, None))
        assert "exception_type" not in _lines(stream)[0]

    def test_unserialisable_envelope_without_attributes(
        self, stream: io.StringIO
    ) -> None:
        def scrub(part: dict[str, Any]) -> Mapping[str, Any]:
            return {**part, "ratio": float("nan")}

        _configure(redact=scrub)
        logging.getLogger("app.x").info("m")
        (line,) = _lines(stream)
        assert line["ratio"] == "nan" and "attributes" not in line

    def test_format_failure_is_silent_when_raise_exceptions_is_off(
        self,
        stream: io.StringIO,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(logging, "raiseExceptions", False)
        _configure(redact=lambda part: part)
        logging.getLogger("app.x").info("%d", "not-a-number")
        assert capsys.readouterr().err == ""


def test_entrypoint_block_configures_logging_then_exits_on_bad_env(
    stream: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `__main__` block in process: configure_logging() installs the fleet handler
    before main() runs, and a missing variable exits 1 rather than raising."""
    for var in config.REQUIRED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # runpy executes the module fresh as __main__; an already-imported app.main would be
    # shadowed (RuntimeWarning), so take it out of sys.modules for this test only.
    monkeypatch.delitem(sys.modules, "app.main", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("app.main", run_name="__main__")
    assert exit_info.value.code == 1
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, lc.JsonFormatter)
    assert handler.formatter.service == "kidde-collector"


class TestEveryLineIsJson:
    """Config is read at import, before configure_logging(); its warnings must still be JSON."""

    def test_import_time_config_warnings_are_json(self) -> None:
        # The prod template's own URL (.env.example) is cleartext to a remote host, and an
        # out-of-range interval: both warn while config is imported.
        lines = _docker_logs(
            KIDDE_COLLECTOR_INFLUXDB_URL="http://influxdb.example.com:8086",
            KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS="5",
            **_SECRETS,
        )
        assert _not_json(lines) == []
        warnings = [
            str(json.loads(line)["message"])
            for line in lines
            if json.loads(line)["level"] == "WARNING"
        ]
        assert any("cleartext" in w for w in warnings)
        assert any("out of range" in w for w in warnings)

    def test_bad_influxdb_url_says_why_it_exits(self) -> None:
        lines = _docker_logs(KIDDE_COLLECTOR_INFLUXDB_URL="influx:8086", **_SECRETS)
        assert _not_json(lines) == []
        messages = [str(json.loads(line)["message"]) for line in lines]
        assert any(
            "Startup aborted: Invalid InfluxDB URL format" in m for m in messages
        )

    def test_url_credentials_never_reach_the_log(self) -> None:
        lines = _docker_logs(
            KIDDE_COLLECTOR_INFLUXDB_URL="http://admin:not-a-real-url-pass@127.0.0.1:1",
            **_SECRETS,
        )
        joined = "\n".join(lines)
        assert "not-a-real-url-pass" not in joined and "admin" not in joined
        assert "***@127.0.0.1:1" in joined
        for secret in ("not-a-real-token", "not-a-real-password", "me@example.com"):
            assert secret not in joined


@pytest.fixture
async def kidde_login_500() -> Iterator[str]:
    async def login(request: web.Request) -> web.Response:
        await request.json()
        resp = web.Response(status=500, text="boom")
        resp.set_cookie("kidde_session", "not-a-real-cookie")
        return resp

    app = web.Application()
    app.router.add_post("/api/v4/auth/login", login)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}/api/v4/auth/login"
    await runner.cleanup()


async def test_login_failure_traceback_carries_no_credentials(
    kidde_login_500: str,
    stream: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """logger.exception logs the login failure's traceback: never the credentials or cookie."""
    from app.collector import client
    from app.collector.session import KiddeSession

    monkeypatch.setattr(client.KiddeAPIEndpoints, "LOGIN", kidde_login_500)
    monkeypatch.setattr(config, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(config, "KIDDE_USERNAME", "me@example.com")
    monkeypatch.setattr(config, "KIDDE_PASSWORD", "not-a-real-password")
    _configure()
    session = KiddeSession()
    await session.connect()
    try:
        assert await session.get_client() is None
    finally:
        await session.close()
    out = stream.getvalue()
    assert "HTTP error 500" in out and '"exception"' in out
    for secret in ("not-a-real-password", "me@example.com", "not-a-real-cookie"):
        assert secret not in out
