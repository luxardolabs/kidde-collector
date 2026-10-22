"""Tests for KiddeSession — cookie persistence and the re-auth path.

session.py was at 0% coverage (KIDDECOLLE-50) despite owning the most failure-prone
path in the collector: it talks to a third-party cloud, caches a live bearer credential
on disk, and self-heals an expired session by dropping that cache.
"""

import json
import os
from pathlib import Path

import aiofiles
import aiohttp
import pytest

from app.collector.client import (
    KiddeClient,
    KiddeClientAuthError,
    KiddeLoginRejectedError,
)
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

    async def test_cookie_is_owner_only_even_where_chmod_fails(
        self, session, monkeypatch
    ):
        """The cookie is created 0600 under the image's 0022 umask, so it is never readable
        by others, even on a mount (CIFS/NFS) where chmod fails."""
        session.cookies_file_path.write_text('{"sid": "stale"}')
        session.cookies_file_path.chmod(0o644)

        def refuse(*_args, **_kwargs):
            raise PermissionError("chmod not supported on this mount")

        monkeypatch.setattr(Path, "chmod", refuse)
        monkeypatch.setattr(os, "chmod", refuse)
        previous = os.umask(0o022)  # the production image's umask
        try:
            await session.save_cookies({"sid": "abc123"})
        finally:
            os.umask(previous)
        mode = session.cookies_file_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {mode:o}"
        assert await session.load_cookies() == {"sid": "abc123"}
        assert list(session.cookies_file_path.parent.iterdir()) == [
            session.cookies_file_path
        ], "no temp file may be left behind"

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

    async def test_missing_credentials_is_a_login_rejection(self, session, monkeypatch):
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "")
        try:
            with pytest.raises(KiddeLoginRejectedError):
                await session.get_client()
        finally:
            await session.close()

    async def test_a_rejected_login_raises_for_the_poller_to_back_off(
        self, session, monkeypatch
    ):
        """The poller backs off on this error, so it must reach the poller, not become None."""
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def raise_auth(*_args, **_kwargs):
            raise KiddeClientAuthError("bad credentials")

        monkeypatch.setattr(KiddeClient, "from_login", raise_auth)
        try:
            with pytest.raises(KiddeLoginRejectedError):
                await session.get_client()
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

    async def test_non_401_http_error_returns_none(self, session, monkeypatch):
        """A 500 from Kidde is not an auth problem, but it still must not kill the cycle."""
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def raise_500(*_args, **_kwargs):
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=500, message="Server Error"
            )

        monkeypatch.setattr(KiddeClient, "from_login", raise_500)
        try:
            assert await session.get_client() is None
        finally:
            await session.close()

    async def test_unexpected_error_returns_none_not_raises(
        self, session, monkeypatch, caplog
    ):
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
        failed = [r for r in caplog.records if r.levelname == "ERROR"]
        assert failed and failed[0].exc_info is not None, "the traceback must be logged"
        assert isinstance(failed[0].exc_info[1], OSError)

    @pytest.mark.parametrize(
        "content",
        [b"{not json", b"", b"[1, 2]", b'{"sid": 7}', b'\xff\xfe{"sid": "x"}'],
    )
    async def test_a_corrupt_cookie_file_is_dropped_and_replaced_by_a_login(
        self, session, monkeypatch, content
    ):
        """The cache sits on a persistent volume, so a restart keeps it: an unreadable one
        is dropped and replaced by a login in the same call."""
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def fake_login(http, _username, _password):
            return KiddeClient(http, {"sid": "fresh"})

        monkeypatch.setattr(KiddeClient, "from_login", fake_login)
        session.cookies_file_path.write_bytes(content)
        try:
            client = await session.get_client()
        finally:
            await session.close()
        assert client is not None and client.cookies == {"sid": "fresh"}
        assert json.loads(session.cookies_file_path.read_text()) == {"sid": "fresh"}

    async def test_a_cookie_file_the_process_cannot_read_is_replaced_by_a_login(
        self, session, monkeypatch
    ):
        """A file left by another uid (or 0000) is dropped too: unlinking needs only the
        directory's write permission, and a login writes a fresh one."""
        await session.connect()
        monkeypatch.setattr(config, "KIDDE_USERNAME", "u")
        monkeypatch.setattr(config, "KIDDE_PASSWORD", "p")

        async def fake_login(http, _username, _password):
            return KiddeClient(http, {"sid": "fresh"})

        def unreadable(*_args, **_kwargs):
            raise PermissionError(13, "Permission denied", "cookies.json")

        monkeypatch.setattr(KiddeClient, "from_login", fake_login)
        monkeypatch.setattr(aiofiles, "open", unreadable)
        session.cookies_file_path.write_text('{"sid": "theirs"}')
        try:
            client = await session.get_client()
        finally:
            await session.close()
        assert client is not None and client.cookies == {"sid": "fresh"}
        assert json.loads(session.cookies_file_path.read_text()) == {"sid": "fresh"}
