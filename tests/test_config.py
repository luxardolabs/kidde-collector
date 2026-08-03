import pytest

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
