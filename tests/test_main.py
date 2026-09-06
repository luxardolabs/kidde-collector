"""Tests for the entrypoint — secret masking and fail-fast env validation.

main.py was at 0% coverage (KIDDECOLLE-50). `_obscure` is the guard that keeps the Kidde
password and the InfluxDB token out of the startup log, and `validate_environment` is the
fail-fast that stops the collector booting into a half-configured state.
"""

import pytest

from app.core import config
from app.main import _obscure, validate_environment


class TestObscure:
    def test_long_secret_keeps_only_the_ends(self):
        assert _obscure("supersecretvalue") == "su************ue"

    def test_masked_length_matches_the_original(self):
        """The mask must not leak the secret's length by being a fixed width."""
        for secret in ("abcdefgh", "abcdefghijklmnop"):
            assert len(_obscure(secret)) == len(secret)

    @pytest.mark.parametrize("secret", ["a", "ab", "abc", "abcd"])
    def test_short_secrets_are_fully_redacted(self, secret):
        """<=4 chars cannot be partially masked without revealing most of it."""
        assert _obscure(secret) == "*" * len(secret)
        assert not any(c.isalnum() for c in _obscure(secret))

    def test_empty_value_passes_through(self):
        assert _obscure("") == ""


class TestValidateEnvironment:
    def test_raises_when_a_required_var_is_missing(self, monkeypatch):
        monkeypatch.setattr(config, "REQUIRED_ENV_VARS", ["KIDDE_TEST_REQUIRED"])
        monkeypatch.delenv("KIDDE_TEST_REQUIRED", raising=False)
        with pytest.raises(ValueError, match="Missing required environment variables"):
            validate_environment()

    def test_passes_when_all_present(self, monkeypatch):
        monkeypatch.setattr(config, "REQUIRED_ENV_VARS", ["KIDDE_TEST_REQUIRED"])
        monkeypatch.setenv("KIDDE_TEST_REQUIRED", "value")
        validate_environment()

    def test_empty_string_counts_as_missing(self, monkeypatch):
        """An unset var and a var set to "" are the same misconfiguration."""
        monkeypatch.setattr(config, "REQUIRED_ENV_VARS", ["KIDDE_TEST_REQUIRED"])
        monkeypatch.setenv("KIDDE_TEST_REQUIRED", "")
        with pytest.raises(ValueError, match="Missing required environment variables"):
            validate_environment()

    def test_error_names_every_missing_var(self, monkeypatch, caplog):
        """The operator needs the full list, not just the first failure."""
        import logging

        monkeypatch.setattr(config, "REQUIRED_ENV_VARS", ["KIDDE_A", "KIDDE_B"])
        monkeypatch.delenv("KIDDE_A", raising=False)
        monkeypatch.delenv("KIDDE_B", raising=False)
        with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
            validate_environment()
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "KIDDE_A" in logged and "KIDDE_B" in logged

    def test_secrets_are_masked_in_the_startup_log(self, monkeypatch, caplog):
        """A password/token/username must never reach the log in the clear.

        The placeholder values are deliberately low-entropy: a realistic-looking fake
        token trips the fleet gitleaks pre-commit hook (generic-api-key), and the right
        answer to that is a non-secret-shaped fixture, not a scanner allowlist.
        """
        import logging

        monkeypatch.setattr(config, "REQUIRED_ENV_VARS", [])
        monkeypatch.setattr(
            config,
            "describe_settings",
            lambda: {
                "KIDDE_COLLECTOR_KIDDE_PASSWORD": "not-a-real-password",
                "KIDDE_COLLECTOR_INFLUXDB_TOKEN": "not-a-real-token",
                "KIDDE_COLLECTOR_LOG_LEVEL": "INFO",
            },
        )
        with caplog.at_level(logging.INFO):
            validate_environment()
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "not-a-real-password" not in logged
        assert "not-a-real-token" not in logged
        assert "INFO" in logged, "non-sensitive settings must still be visible"
