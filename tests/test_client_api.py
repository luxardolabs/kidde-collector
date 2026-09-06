"""Tests for KiddeClient's REST surface, driven against the real emulator.

client.py's request path was the largest remaining gap (KIDDECOLLE-50). Rather than mock
aiohttp — which would assert against a hand-written idea of the Kidde API instead of the
API itself — these run the actual `harness/fake_kidde.py` emulator in-process on an
ephemeral port. That is the same server the e2e stack uses, so a drift between the client
and the emulated contract fails here, not only in `make test-e2e`.
"""

import sys
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import aiohttp
import pytest

from app.collector.client import KiddeClient, KiddeClientAuthError
from app.collector.endpoints import KiddeAPIEndpoints
from harness.fake_kidde import DEVICES, LOCATION_ID, Handler


class _QuietServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not print the shutdown-race traceback.

    aiohttp holds the pooled keep-alive connection open past `server.shutdown()`, so the
    handler thread reads a truncated request and `parse_request` raises ValueError. That
    is test-teardown noise with nothing to fix — but only THAT is silenced: any other
    exception still goes through the normal loud path, so a genuine handler bug is not
    hidden behind this.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ValueError | ConnectionError):
            return
        super().handle_error(request, client_address)


@pytest.fixture
def fake_kidde():
    """Run the emulator on an ephemeral port for the duration of one test."""
    server = _QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/api/v4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def serving(handler_cls):
    """Run a one-off handler on an ephemeral port; yields its /api/v4 base URL."""
    server = _QuietServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/api/v4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def endpoints(fake_kidde, monkeypatch):
    """Point the endpoint builders at the emulator (they are baked at import from config)."""
    monkeypatch.setattr(KiddeAPIEndpoints, "BASE_URL", fake_kidde)
    monkeypatch.setattr(KiddeAPIEndpoints, "LOGIN", f"{fake_kidde}/auth/login")
    monkeypatch.setattr(KiddeAPIEndpoints, "LOCATIONS", f"{fake_kidde}/location")
    monkeypatch.setattr(
        KiddeAPIEndpoints,
        "LOCATION_DEVICES",
        fake_kidde + "/location/{location_id}/device",
    )
    monkeypatch.setattr(
        KiddeAPIEndpoints,
        "LOCATION_EVENTS",
        fake_kidde + "/location/{location_id}/event",
    )
    return fake_kidde


@pytest.fixture
async def http():
    """A real aiohttp session with the cookie jar disabled, as KiddeSession configures it."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        yield session


class TestFromLogin:
    async def test_captures_the_session_cookie(self, endpoints, http):
        client = await KiddeClient.from_login(http, "user@example.com", "pw")
        assert client.cookies, "the session cookie must be read off the login response"
        assert "id_token" in client.cookies

    @pytest.mark.parametrize("status", [401, 403])
    async def test_rejected_credentials_raise_kidde_auth_error(
        self, http, status, monkeypatch
    ):
        """A 401/403 at login must surface as KiddeClientAuthError, not a raw HTTP error.

        KiddeSession catches this specific type to return None (skip the cycle); a bare
        ClientResponseError would fall to the generic arm and lose the distinction.
        """

        class Rejecting(Handler):
            def do_POST(self) -> None:
                self._json({"error": "bad credentials"}, status)

        with serving(Rejecting) as base:
            monkeypatch.setattr(KiddeAPIEndpoints, "LOGIN", f"{base}/auth/login")
            with pytest.raises(KiddeClientAuthError):
                await KiddeClient.from_login(http, "user@example.com", "wrong")

    async def test_server_error_at_login_propagates(self, http, monkeypatch):
        """A 5xx is NOT an auth problem — it must not be masked as one."""

        class Broken(Handler):
            def do_POST(self) -> None:
                self._json({"error": "boom"}, 500)

        with serving(Broken) as base:
            monkeypatch.setattr(KiddeAPIEndpoints, "LOGIN", f"{base}/auth/login")
            with pytest.raises(aiohttp.ClientResponseError):
                await KiddeClient.from_login(http, "u", "p")


class TestGetData:
    async def test_locations_only(self, endpoints, http):
        client = await KiddeClient.from_login(http, "u", "p")
        data = await client.get_data(get_devices=False, get_events=False)
        assert LOCATION_ID in data.locations
        assert data.devices is None
        assert data.events is None

    async def test_devices_are_fetched_per_location_and_indexed_by_id(
        self, endpoints, http
    ):
        client = await KiddeClient.from_login(http, "u", "p")
        data = await client.get_data(get_devices=True, get_events=False)
        assert data.devices is not None
        assert len(data.devices) == len(DEVICES)
        for device_id, device in data.devices.items():
            assert device["id"] == device_id, (
                "the map must be keyed by the device's own id"
            )

    async def test_events_unwrap_the_envelope(self, endpoints, http):
        """Events come back as {"events": [...]} — the client must unwrap, not store the dict."""
        client = await KiddeClient.from_login(http, "u", "p")
        data = await client.get_data(get_devices=False, get_events=True)
        assert data.events == {}

    async def test_full_dataset(self, endpoints, http):
        client = await KiddeClient.from_login(http, "u", "p")
        data = await client.get_data(get_devices=True, get_events=True)
        assert data.locations and data.devices is not None and data.events is not None

    async def test_devices_carry_the_iaq_panel(self, endpoints, http):
        """The air-quality payload is the reason this collector exists — assert it survives."""
        client = await KiddeClient.from_login(http, "u", "p")
        data = await client.get_data(get_devices=True, get_events=False)
        iaq_devices = [d for d in data.devices.values() if "co2" in d]
        assert iaq_devices, "the emulator must expose at least one IAQ device"
        assert "value" in iaq_devices[0]["co2"]


class TestRequestAuth:
    async def test_401_from_a_data_call_raises_auth_error(self, endpoints, http):
        """An expired cookie mid-session is what triggers the poller's re-auth."""

        class Unauthorized(Handler):
            def do_GET(self) -> None:
                self._json({"error": "unauthorized"}, 401)

        with serving(Unauthorized) as base:
            client = KiddeClient(http, {"id_token": "stale"})
            with pytest.raises(KiddeClientAuthError):
                await client._request(f"{base}/location")

    async def test_204_returns_none_rather_than_parsing_an_empty_body(self, http):
        """A No Content response has no JSON to parse — returning None avoids a decode error."""

        class NoContent(Handler):
            def do_GET(self) -> None:
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        with serving(NoContent) as base:
            client = KiddeClient(http, {"id_token": "abc"})
            assert await client._request(f"{base}/location") is None

    async def test_cookies_are_sent_on_every_request(self, endpoints, http):
        """The jar is disabled, so the client must attach cookies explicitly or auth is lost."""
        seen = {}

        class RecordingHandler(Handler):
            def do_GET(self) -> None:
                seen["cookie"] = self.headers.get("Cookie")
                self._json([])

        with serving(RecordingHandler) as base:
            client = KiddeClient(http, {"id_token": "abc123"})
            await client._request(f"{base}/location")
            assert seen["cookie"] is not None
            assert "id_token=abc123" in seen["cookie"]
