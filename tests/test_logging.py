"""Tests for the logging setup.

The structured JSON formatter is the fleet's log-aggregation path — if it drops `extra=`
fields or emits invalid JSON the aggregator silently loses records, which looks like
"the collector went quiet" rather than a logging bug.
"""

import json
import logging

from app.core import config
from app.utils.logging import ColoredFormatter, StructuredFormatter, setup_logger


def _record(level=logging.INFO, msg="hello %s", args=("world",), **extra):
    record = logging.LogRecord(
        name="kidde_collector",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestStructuredFormatter:
    def test_emits_valid_json_with_the_core_fields(self):
        payload = json.loads(StructuredFormatter().format(_record()))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "kidde_collector"
        assert payload["message"] == "hello world", "lazy %s args must be interpolated"
        assert payload["timestamp"].endswith("+00:00"), (
            "timestamps must be tz-aware UTC"
        )

    def test_extra_fields_are_carried_through(self):
        """`extra=` is the whole point of structured logging — it must survive to the JSON."""
        payload = json.loads(StructuredFormatter().format(_record(device_id=553549)))
        assert payload["device_id"] == 553549

    def test_reserved_logrecord_attrs_are_not_leaked(self):
        """Internal LogRecord machinery must not pollute the aggregated document."""
        payload = json.loads(StructuredFormatter().format(_record()))
        for reserved in ("pathname", "lineno", "msg", "args", "levelno"):
            assert reserved not in payload

    def test_exception_is_serialized_when_present(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record()
            record.exc_info = sys.exc_info()
            payload = json.loads(StructuredFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_output_is_a_single_line(self):
        """A record that spans lines breaks line-delimited log shipping."""
        out = StructuredFormatter().format(_record(msg="a\nb", args=()))
        assert "\n" not in out


class TestColoredFormatter:
    def test_level_is_wrapped_in_its_color(self):
        out = ColoredFormatter("%(levelname)s:%(message)s").format(
            _record(level=logging.ERROR)
        )
        assert "\033[31m" in out and "\033[0m" in out

    def test_unknown_level_passes_through_uncolored(self):
        record = _record()
        record.levelname = "TRACE"
        out = ColoredFormatter("%(levelname)s:%(message)s").format(record)
        assert "\033[" not in out


class TestSetupLogger:
    def test_does_not_propagate_to_root(self, monkeypatch):
        """Propagating would double-log through any root handler an import installs."""
        monkeypatch.setattr(config, "LOG_STRUCTURED", False)
        log = setup_logger("kidde_test_propagate", "INFO")
        assert log.propagate is False

    def test_repeated_setup_does_not_stack_handlers(self, monkeypatch):
        """Re-running setup must replace handlers, not add a duplicate line per call."""
        monkeypatch.setattr(config, "LOG_STRUCTURED", False)
        setup_logger("kidde_test_dupe", "INFO")
        log = setup_logger("kidde_test_dupe", "INFO")
        assert len(log.handlers) == 1

    def test_structured_flag_selects_the_json_formatter(self, monkeypatch):
        monkeypatch.setattr(config, "LOG_STRUCTURED", True)
        log = setup_logger("kidde_test_structured", "INFO")
        assert isinstance(log.handlers[0].formatter, StructuredFormatter)

    def test_default_selects_the_colored_formatter(self, monkeypatch):
        monkeypatch.setattr(config, "LOG_STRUCTURED", False)
        log = setup_logger("kidde_test_colored", "INFO")
        assert isinstance(log.handlers[0].formatter, ColoredFormatter)

    def test_level_is_applied_to_logger_and_handler(self, monkeypatch):
        monkeypatch.setattr(config, "LOG_STRUCTURED", False)
        log = setup_logger("kidde_test_level", "WARNING")
        assert log.level == logging.WARNING
        assert log.handlers[0].level == logging.WARNING
