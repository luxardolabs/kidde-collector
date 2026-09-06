import logging

import pytest

from app.core import config
from app.core.config import ConfigValidator


class TestValidateInt:
    def test_valid(self):
        assert ConfigValidator.validate_int("42") == 42

    def test_bounds_clamp_to_default(self):
        assert ConfigValidator.validate_int("999", max_val=100, default=60) == 60
        assert ConfigValidator.validate_int("1", min_val=10, default=60) == 60

    def test_invalid_falls_back_to_default(self):
        assert ConfigValidator.validate_int("notanint", default=60) == 60

    def test_invalid_without_default_raises(self):
        with pytest.raises(ValueError):
            ConfigValidator.validate_int("notanint")


class TestValidateBool:
    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
    def test_truthy(self, value):
        assert ConfigValidator.validate_bool(value) is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
    def test_falsy(self, value):
        assert ConfigValidator.validate_bool(value) is False

    def test_invalid_falls_back_to_default(self):
        assert ConfigValidator.validate_bool("maybe", default=True) is True

    def test_invalid_without_default_raises(self):
        with pytest.raises(ValueError):
            ConfigValidator.validate_bool("maybe")


class TestValidateLogLevel:
    def test_valid(self):
        assert ConfigValidator.validate_log_level("debug") == "DEBUG"

    def test_invalid_falls_back(self):
        assert ConfigValidator.validate_log_level("chatty") == "INFO"


class TestValidateIntNoDefault:
    def test_out_of_range_without_a_default_raises(self):
        """No default means there is no safe fallback — fail rather than silently clamp."""
        with pytest.raises(ValueError, match="out of range"):
            ConfigValidator.validate_int("9999", min_val=1, max_val=10)


class TestCleartextUrlWarning:
    def test_warns_for_http_to_a_remote_host(self, caplog):
        """http:// to a remote host sends the InfluxDB token in the clear."""
        with caplog.at_level(logging.WARNING, logger="kidde_collector"):
            config._warn_if_insecure_url(
                "KIDDE_COLLECTOR_INFLUXDB_URL", "http://influx.example.com:8086"
            )
        assert any("cleartext" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8086",
            "http://127.0.0.1:8086",
            "http://kidde_influxdb:8086",
            "https://influx.example.com",
            None,
        ],
    )
    def test_no_warning_for_local_or_tls(self, caplog, url):
        """Loopback and compose-internal hosts are not on the wire; https is encrypted."""
        with caplog.at_level(logging.WARNING, logger="kidde_collector"):
            config._warn_if_insecure_url("KIDDE_COLLECTOR_INFLUXDB_URL", url)
        assert not [r for r in caplog.records if "cleartext" in r.getMessage()]
