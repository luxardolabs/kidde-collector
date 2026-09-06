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


class FakeStorage:
    def __init__(self):
        self.events = []

    async def connect(self):
        self.events.append("storage.connect")

    async def close(self):
        self.events.append("storage.close")


class FakeSession:
    def __init__(self, log):
        self.log = log

    async def connect(self):
        self.log.append("session.connect")

    async def close(self):
        self.log.append("session.close")


class FakeCollector:
    def __init__(self, session, storage, raises=None):
        self.session = session
        self.storage = storage
        self.raises = raises
        self.ran_with = None

    async def run(self, shutdown_event):
        self.ran_with = shutdown_event
        if self.raises is not None:
            raise self.raises


def _wire(monkeypatch, raises=None):
    """Replace main()'s three collaborators with fakes sharing one ordered event log."""
    from app import main as main_mod

    log = []
    storage = FakeStorage()
    storage.events = log
    session = FakeSession(log)
    holder = {}

    def make_collector(sess, stor):
        holder["collector"] = FakeCollector(sess, stor, raises)
        return holder["collector"]

    monkeypatch.setattr(main_mod, "InfluxDBStorage", lambda: storage)
    monkeypatch.setattr(main_mod, "KiddeSession", lambda: session)
    monkeypatch.setattr(main_mod, "KiddeCollector", make_collector)
    monkeypatch.setattr(
        main_mod, "validate_environment", lambda: log.append("validate")
    )
    return log, holder


class TestMainOrchestration:
    async def test_starts_and_shuts_down_in_order(self, monkeypatch):
        """Validate first, then open both clients, then always close them."""
        from app.main import main

        log, holder = _wire(monkeypatch)
        await main()
        assert log == [
            "validate",
            "storage.connect",
            "session.connect",
            "session.close",
            "storage.close",
        ]
        assert holder["collector"].ran_with is not None

    async def test_clients_are_closed_even_when_the_loop_raises(self, monkeypatch):
        """The finally block is the only thing preventing a leaked aiohttp session."""
        from app.main import main

        log, _ = _wire(monkeypatch, raises=RuntimeError("loop exploded"))
        with pytest.raises(RuntimeError, match="loop exploded"):
            await main()
        assert "session.close" in log and "storage.close" in log

    async def test_a_failed_validation_never_opens_a_client(self, monkeypatch):
        """Fail fast: a missing env var must abort before any connection is made."""
        from app import main as main_mod

        log, _ = _wire(monkeypatch)

        def boom():
            log.append("validate")
            raise ValueError("Missing required environment variables")

        monkeypatch.setattr(main_mod, "validate_environment", boom)
        with pytest.raises(ValueError):
            await main_mod.main()
        assert log == ["validate"], "nothing may be opened after validation fails"

    async def test_sigterm_and_sigint_are_both_wired(self, monkeypatch):
        """Both signals must set the shutdown event — SIGTERM is what `docker stop` sends."""
        import signal

        from app.main import main

        _wire(monkeypatch)
        registered = {}
        loop = __import__("asyncio").get_running_loop()
        real_add = loop.add_signal_handler

        def spy(sig, handler, *args):
            registered[sig] = (handler, args)
            return real_add(sig, handler, *args)

        monkeypatch.setattr(loop, "add_signal_handler", spy)
        await main()
        assert signal.SIGTERM in registered and signal.SIGINT in registered

    async def test_the_signal_handler_sets_the_shutdown_event(self, monkeypatch):
        """The handler is what turns a signal into a graceful stop — assert it actually does."""
        import signal

        from app.main import main

        _, holder = _wire(monkeypatch)
        captured = {}
        loop = __import__("asyncio").get_running_loop()
        real_add = loop.add_signal_handler

        def spy(sig, handler, *args):
            captured[sig] = (handler, args)
            return real_add(sig, handler, *args)

        monkeypatch.setattr(loop, "add_signal_handler", spy)
        await main()
        event = holder["collector"].ran_with
        assert not event.is_set()
        handler, args = captured[signal.SIGTERM]
        handler(*args)
        assert event.is_set(), "the handler must trip the event the poll loop waits on"

    async def test_missing_signal_support_is_tolerated(self, monkeypatch):
        """add_signal_handler raises NotImplementedError on some platforms — must not crash."""
        from app.main import main

        _wire(monkeypatch)
        loop = __import__("asyncio").get_running_loop()

        def unsupported(*_args, **_kwargs):
            raise NotImplementedError

        monkeypatch.setattr(loop, "add_signal_handler", unsupported)
        await main()
