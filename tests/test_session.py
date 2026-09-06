"""Tests for KiddeSession — cookie persistence and the re-auth path.

session.py was at 0% coverage (KIDDECOLLE-50) despite owning the most failure-prone
path in the collector: it talks to a third-party cloud, caches a live bearer credential
on disk, and self-heals an expired session by dropping that cache.
"""

import json

import aiohttp
import pytest

from app.collector.client import KiddeClient, KiddeClientAuthError
from app.collector.session import KiddeSession
from app.core import config


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A KiddeSession whose cookie cache lives in a throwaway dir."""
    monkeypatch.setattr(config, "COOKIES_DIR", tmp_path / "cookies")
    return KiddeSession()


class TestInit:
    def test_creates_cookie_dir_and_path(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "cookies"
        monkeypatch.setattr(config, "COOKIES_DIR", target)
        s = KiddeSession()
        assert target.is_dir(), "the cookie dir must be created eagerly"
        assert s.cookies_file_path == target / "cookies.json"


class TestConnectClose:
    async def test_connect_opens_session_with_dummy_jar(self, session):
        """Cookies are replayed manually, so aiohttp's own jar must stay disabled."""
        await session.connect()
        try:
            assert session._http is not None
            assert isinstance(session._http.cookie_jar, aiohttp.DummyCookieJar)
        finally:
            await session.close()

    async def test_close_is_idempotent(self, session):
        await session.connect()
        await session.close()
        assert session._http is None
        await session.close()  # must not raise on a second call

    async def test_get_client_before_connect_raises(self, session):
        with pytest.raises(RuntimeError, match="connect\\(\\) must be called"):
            await session.get_client()


class TestCookieRoundTrip:
    async def test_load_returns_none_when_absent(self, session):
        assert await session.load_cookies() is None

    async def test_save_then_load_round_trips(self, session):
        await session.save_cookies({"sid": "abc123"})
        assert await session.load_cookies() == {"sid": "abc123"}

    async def test_saved_cookie_file_is_owner_only(self, session):
        """The cookie is a live bearer credential on a bind-mounted dir — never group/world readable."""
        await session.save_cookies({"sid": "abc123"})
        mode = session.cookies_file_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {mode:o}"

    async def test_save_overwrites_rather_than_appends(self, session):
        await session.save_cookies({"sid": "first"})
        await session.save_cookies({"sid": "second"})
        assert await session.load_cookies() == {"sid": "second"}
        assert json.loads(session.cookies_file_path.read_text()) == {"sid": "second"}


class TestInvalidate:
    def test_removes_the_cached_cookie(self, session):
        session.cookies_file_path.write_text('{"sid": "stale"}')
        session.invalidate()
        assert not session.cookies_file_path.exists()

    def test_is_a_noop_when_already_absent(self, session):
        session.invalidate()  # missing_ok=True — must not raise
        assert not session.cookies_file_path.exists()

    def test_never_raises_when_the_unlink_fails(self, session, monkeypatch):
        """Best-effort cleanup: a read-only mount must not abort the re-auth it enables."""

        def boom(*_args, **_kwargs):
            raise PermissionError("read-only file system")

        monkeypatch.setattr(type(session.cookies_file_path), "unlink", boom)
        session.invalidate()  # swallowed by contract


class TestGetClient:
    async def test_uses_cached_cookies_without_logging_in(self, session, monkeypatch):
        await session.connect()

        async def fail_login(*_args, **_kwargs):
            raise AssertionError("must not log in when a cached cookie exists")

        monkeypatch.setattr(KiddeClient, "from_login", fail_login)
        try:
            await session.save_cookies({"sid": "cached"})
            client = await session.get_client()
            assert isinstance(client, KiddeClient)
            assert client.cookies == {"sid": "cached"}
        finally:
            await session.close()

    async def test_logs_in_and_caches_when_no_cookie(self, session, monkeypatch):
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "user@example.com")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "secret")

        async def fake_login(http, username, password):
            assert username == "user@example.com"
            return KiddeClient(http, {"sid": "fresh"})

        monkeypatch.setattr(KiddeClient, "from_login", fake_login)
        try:
            client = await session.get_client()
            assert client is not None
            assert client.cookies == {"sid": "fresh"}
            # the freshly-minted cookie must be persisted for the next restart
            assert await session.load_cookies() == {"sid": "fresh"}
        finally:
            await session.close()

    async def test_missing_credentials_returns_none(self, session, monkeypatch):
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "")
        try:
            assert await session.get_client() is None
        finally:
            await session.close()

    async def test_auth_error_returns_none(self, session, monkeypatch):
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def raise_auth(*_args, **_kwargs):
            raise KiddeClientAuthError("bad credentials")

        monkeypatch.setattr(KiddeClient, "from_login", raise_auth)
        try:
            assert await session.get_client() is None
        finally:
            await session.close()

    async def test_http_401_returns_none(self, session, monkeypatch):
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def raise_401(*_args, **_kwargs):
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=401, message="Unauthorized"
            )

        monkeypatch.setattr(KiddeClient, "from_login", raise_401)
        try:
            assert await session.get_client() is None
        finally:
            await session.close()

    async def test_unexpected_error_returns_none_not_raises(self, session, monkeypatch):
        """The resilience contract: a login blowing up skips the cycle, never kills the process."""
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def boom(*_args, **_kwargs):
            raise OSError("connection reset")

        monkeypatch.setattr(KiddeClient, "from_login", boom)
        try:
            assert await session.get_client() is None
        finally:
            await session.close()

    async def test_corrupt_cookie_file_returns_none_not_raises(
        self, session, monkeypatch
    ):
        """A truncated/garbage cookie cache must degrade to 'no client', not crash the loop."""
        await session.connect()
        session.cookies_file_path.write_text("{not json")
        try:
            assert await session.get_client() is None
        finally:
            await session.close()
