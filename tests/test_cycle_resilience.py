"""Whole poll cycles against a hostile Kidde server: the real session, client and poller.

The unit tests fake one layer at a time; these cover what a cycle does when the pieces
meet: a rejected password backs off, a corrupt cookie file is replaced by a login, an
events anomaly leaves the detectors written, and no request follows a redirect. Each test
runs real cycles against an in-process aiohttp server and asserts on what that server
received.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from app.collector.client import KiddeClient
from app.collector.endpoints import KiddeAPIEndpoints
from app.collector.poller import KiddeCollector
from app.collector.session import KiddeSession
from app.core import config
from app.storage.influxdb import InfluxDBStorage

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class Server:
    """An in-process aiohttp server that records every request it is sent."""

    def __init__(self) -> None:
        self.app = web.Application()
        self.hits: list[dict[str, Any]] = []
        self.runner: web.AppRunner | None = None
        self.port = 0

    def route(self, method: str, path: str, handler: Handler) -> None:
        async def recorded(request: web.Request) -> web.StreamResponse:
            body = await request.read()
            self.hits.append(
                {
                    "method": request.method,
                    "path": request.path,
                    "cookie": request.headers.get("Cookie"),
                    "body": body.decode(errors="replace"),
                }
            )
            return await handler(request)

        self.app.router.add_route(method, path, recorded)

    async def start(self) -> Server:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def logins(self) -> int:
        return sum(1 for h in self.hits if h["path"].endswith("/auth/login"))


def point_endpoints(monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    monkeypatch.setattr(KiddeAPIEndpoints, "BASE_URL", base)
    monkeypatch.setattr(KiddeAPIEndpoints, "LOGIN", f"{base}/auth/login")
    monkeypatch.setattr(KiddeAPIEndpoints, "LOCATIONS", f"{base}/location")
    monkeypatch.setattr(
        KiddeAPIEndpoints, "LOCATION_DEVICES", base + "/location/{location_id}/device"
    )
    monkeypatch.setattr(
        KiddeAPIEndpoints, "LOCATION_EVENTS", base + "/location/{location_id}/event"
    )


def device(i: int, **extra: Any) -> dict[str, Any]:
    d = {
        "id": i,
        "serial_number": f"SN{i}",
        "location_id": 1,
        "label": f"D{i}",
        "lost": False,
        "smoke_alarm": i == 1,
    }
    d.update(extra)
    return d


async def kidde(
    devices: list[dict[str, Any]],
    events: list[dict[str, Any]] | None = None,
    login_status: int = 200,
) -> Server:
    """A Kidde fake with two locations; `login_status` != 200 rejects the password."""
    s = Server()

    async def login(_r: web.Request) -> web.StreamResponse:
        if login_status != 200:
            return web.json_response({"error": "bad creds"}, status=login_status)
        resp = web.json_response({})
        resp.set_cookie("session", "s1")
        return resp

    async def locations(_r: web.Request) -> web.StreamResponse:
        return web.json_response(
            [{"id": 1, "label": "Home"}, {"id": 2, "label": "Cabin"}]
        )

    async def location_devices(r: web.Request) -> web.StreamResponse:
        lid = int(r.match_info["lid"])
        return web.json_response([d for d in devices if d["location_id"] == lid])

    async def location_events(r: web.Request) -> web.StreamResponse:
        lid = int(r.match_info["lid"])
        return web.json_response(
            {"events": [e for e in (events or []) if e["loc"] == lid]}
        )

    s.route("POST", "/api/v4/auth/login", login)
    s.route("GET", "/api/v4/location", locations)
    s.route("GET", "/api/v4/location/{lid}/device", location_devices)
    s.route("GET", "/api/v4/location/{lid}/event", location_events)
    return await s.start()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InfluxDBStorage:
    """A real InfluxDBStorage whose write API records instead of sending."""
    for name, value in (
        ("INFLUXDB_URL", "http://influx:8086"),
        ("INFLUXDB_TOKEN", "t"),
        ("INFLUXDB_ORG", "o"),
        ("INFLUXDB_BUCKET", "b"),
    ):
        monkeypatch.setattr(config, name, value)
    s = InfluxDBStorage()
    s.write_api = AsyncMock()
    return s


def written_serials(storage: InfluxDBStorage) -> set[str]:
    write = storage.write_api.write  # type: ignore[union-attr]
    serials: set[str] = set()
    for call in write.await_args_list:
        serials |= {p._tags.get("serial_number") for p in call.kwargs["record"]}
    return serials


async def run_cycles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    server: Server,
    storage: InfluxDBStorage,
    cycles: int,
    get_events: bool = False,
) -> None:
    point_endpoints(monkeypatch, f"http://127.0.0.1:{server.port}/api/v4")
    monkeypatch.setattr(config, "COOKIES_DIR", tmp_path)
    monkeypatch.setattr(config, "EXPORT_FOLDER", str(tmp_path))
    monkeypatch.setattr(config, "KIDDE_USERNAME", "owner@example.com")
    monkeypatch.setattr(config, "KIDDE_PASSWORD", "pw")
    monkeypatch.setattr(config, "GET_EVENTS", get_events)
    monkeypatch.setattr(config, "FETCH_INTERVAL_SECONDS", 0)
    session = KiddeSession()
    await session.connect()
    collector = KiddeCollector(session, storage)
    stop = asyncio.Event()
    count = 0
    collect_once = collector._collect_once

    async def counted() -> None:
        nonlocal count
        count += 1
        if count >= cycles:
            stop.set()
        await collect_once()

    monkeypatch.setattr(collector, "_collect_once", counted)
    try:
        await collector.run(stop)
    finally:
        await session.close()
        await server.stop()


class TestRejectedPassword:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_login_backs_off(
        self, monkeypatch, tmp_path, storage, status
    ):
        """Each failed login counts against a life-safety account: six cycles inside the
        backoff make one attempt."""
        server = await kidde([device(1)], login_status=status)
        await run_cycles(monkeypatch, tmp_path, server, storage, 6)
        assert server.logins() == 1

    async def test_a_good_login_is_made_once_and_its_cookie_reused(
        self, monkeypatch, tmp_path, storage
    ):
        server = await kidde([device(1)])
        await run_cycles(monkeypatch, tmp_path, server, storage, 6)
        assert server.logins() == 1
        assert "SN1" in written_serials(storage)

    async def test_the_backoff_doubles_and_is_capped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "EXPORT_FOLDER", str(tmp_path))
        collector = KiddeCollector(AsyncMock(), AsyncMock())
        delays = []
        for _ in range(10):
            collector._login_rejections += 1
            delays.append(collector._login_backoff_seconds())
        assert delays[:3] == [
            config.LOGIN_BACKOFF_BASE_SECONDS,
            config.LOGIN_BACKOFF_BASE_SECONDS * 2,
            config.LOGIN_BACKOFF_BASE_SECONDS * 4,
        ]
        assert max(delays) == config.LOGIN_BACKOFF_MAX_SECONDS


class TestCorruptCookieFile:
    @pytest.mark.parametrize(
        "content", [b'{"session": "s1"', b"", b"[1, 2]", b"\xff\xfe{}"]
    )
    async def test_a_corrupt_cookie_file_is_dropped_and_the_cycle_logs_in(
        self, monkeypatch, tmp_path, storage, content
    ):
        """The file lives on a persistent volume, so a restart keeps it: the cycle itself
        drops an unreadable one and logs in."""
        (tmp_path / "cookies.json").write_bytes(content)
        server = await kidde([device(1)])
        await run_cycles(monkeypatch, tmp_path, server, storage, 1)
        assert server.logins() == 1
        assert "SN1" in written_serials(storage)

    async def test_a_valid_cookie_file_is_reused_without_a_login(
        self, monkeypatch, tmp_path, storage
    ):
        (tmp_path / "cookies.json").write_text('{"session": "s1"}')
        server = await kidde([device(1)])
        await run_cycles(monkeypatch, tmp_path, server, storage, 1)
        assert server.logins() == 0
        assert "SN1" in written_serials(storage)


class TestEventsFeed:
    @pytest.mark.parametrize("duplicate", [True, False])
    async def test_an_events_anomaly_does_not_drop_the_detectors(
        self, monkeypatch, tmp_path, storage, duplicate
    ):
        """Events are never written to InfluxDB; their failure must not cost a device."""
        devices = [device(1), device(2, location_id=2)]
        events = [{"id": 7, "loc": 1}, {"id": 7 if duplicate else 8, "loc": 2}]
        server = await kidde(devices, events)
        await run_cycles(monkeypatch, tmp_path, server, storage, 1, get_events=True)
        assert written_serials(storage) == {"SN1", "SN2"}


class TestRedirects:
    async def _third_party(self) -> Server:
        third = Server()

        async def ok(_r: web.Request) -> web.StreamResponse:
            resp = web.json_response([])
            resp.set_cookie("session", "x")
            return resp

        third.route("*", "/{tail:.*}", ok)
        return await third.start()

    @pytest.mark.parametrize("status", [307, 308])
    async def test_login_never_replays_the_password_to_a_redirect_target(
        self, monkeypatch, status
    ):
        third = await self._third_party()
        kidde_srv = Server()

        redirect = (
            web.HTTPTemporaryRedirect if status == 307 else web.HTTPPermanentRedirect
        )

        async def login(_r: web.Request) -> web.StreamResponse:
            # 127.0.0.1 -> localhost is a different origin.
            raise redirect(f"http://localhost:{third.port}/steal")

        kidde_srv.route("POST", "/api/v4/auth/login", login)
        await kidde_srv.start()
        point_endpoints(monkeypatch, f"http://127.0.0.1:{kidde_srv.port}/api/v4")
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as http:
                with pytest.raises(aiohttp.ClientResponseError) as refused:
                    await KiddeClient.from_login(
                        http, "owner@example.com", "Hunter2-Secret"
                    )
        finally:
            await kidde_srv.stop()
            await third.stop()
        assert refused.value.status == status
        assert not [h for h in third.hits if "Hunter2-Secret" in h["body"]]

    async def test_a_data_call_never_follows_a_redirect(self, monkeypatch):
        third = await self._third_party()
        kidde_srv = Server()

        async def locations(_r: web.Request) -> web.StreamResponse:
            raise web.HTTPFound(f"http://localhost:{third.port}/steal")

        kidde_srv.route("GET", "/api/v4/location", locations)
        await kidde_srv.start()
        point_endpoints(monkeypatch, f"http://127.0.0.1:{kidde_srv.port}/api/v4")
        try:
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as http:
                client = KiddeClient(http, {"session": "SECRET"})
                with pytest.raises(aiohttp.ClientResponseError):
                    await client.get_data(get_devices=False, get_events=False)
        finally:
            await kidde_srv.stop()
            await third.stop()
        assert third.hits == []
