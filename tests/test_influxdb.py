import logging

import pytest
from influxdb_client.client.exceptions import InfluxDBError

from app.collector.client import KiddeDataset
from app.core import config
from app.storage.influxdb import InfluxDBStorage, _age_seconds

DEVICE = {
    "id": 553549,
    "serial_number": "001372CBA9BD",
    "location_id": 356103,
    "label": "Basement",
    "smoke_alarm": False,
    "co_level": 0,
    "temperature": 78,
    "battery_state": "ok",
    "last_test_time": "2026-01-28T18:52:54Z",
    "iaq_temperature": {"value": 73.62, "status": "Good", "Unit": "F"},
    "co2": {"value": 922.37, "status": "Good", "Unit": "PPM"},
    # Nested items not present here (humidity/hpa/tvoc/iaq) must simply be skipped.
}


def _line(point):
    return point.to_line_protocol()


class TestDevicePoints:
    def test_main_point_has_tags_and_scalar_fields(self):
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        main = _line(points[0])
        assert main.startswith("kidde_collector_device,")
        # tags
        assert "serial_number=001372CBA9BD" in main
        assert "location_label=Tyle" in main
        assert "label=Basement" in main
        # scalar fields
        assert "temperature=78" in main
        assert 'battery_state="ok"' in main
        # nested objects must NOT appear as scalar fields on the main point
        assert "iaq_temperature=" not in main

    def test_nested_points_emitted_only_for_present_metrics(self):
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        lines = [_line(p) for p in points]
        # main + 2 nested (iaq_temperature, co2)
        assert len(points) == 3
        joined = "\n".join(lines)
        assert "iaq_temperature_value=73.62" in joined
        assert 'iaq_temperature_status="Good"' in joined
        assert "co2_value=922.37" in joined
        # absent nested metrics produce no points
        assert "humidity_value" not in joined
        assert "tvoc_value" not in joined

    def test_nested_points_carry_same_tags(self):
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        for line in (_line(p) for p in points):
            assert "serial_number=001372CBA9BD" in line
            assert "label=Basement" in line

    def test_numeric_fields_written_as_float_not_int(self):
        # Kidde sends the same field as int on some devices and float on others; writing
        # ints as float keeps the InfluxDB field type consistent so no point is dropped.
        # Line protocol suffixes ints with "i" (e.g. temperature=78i) — a float has none.
        main = _line(InfluxDBStorage._device_points(DEVICE, "Tyle")[0])
        assert "temperature=78 " in main + " "  # float form, not "78i"
        assert "temperature=78i" not in main
        assert "co_level=0i" not in main

    def test_booleans_preserved_not_coerced_to_float(self):
        main = _line(InfluxDBStorage._device_points(DEVICE, "Tyle")[0])
        # bool must stay a boolean field (smoke_alarm=false), not become 0/0.0
        assert "smoke_alarm=false" in main.lower()

    def test_online_device_marked_online_with_full_measurements(self):
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        joined = "\n".join(_line(p) for p in points)
        assert "online=true" in joined.lower()
        assert "co2_value=922.37" in joined  # measurements present for online device
        assert "last_test_time_age_seconds=" in joined  # numeric test age for coloring


OFFLINE_DEVICE = {
    "id": 553551,
    "serial_number": "001372CBA9C4",
    "location_id": 356103,
    "label": "Loft",
    "lost": True,
    "offline": True,
    "contact_lost": True,
    "battery_state": "ok",
    "last_seen": "2026-04-21T05:38:04Z",
    "temperature": 75,
    "co2": {"value": 983.34, "status": "Good", "Unit": "PPM"},
}


class TestOfflineDevice:
    def test_offline_writes_liveness_only_no_measurements(self):
        points = InfluxDBStorage._device_points(OFFLINE_DEVICE, "Tyle")
        assert len(points) == 1  # single liveness point, no nested measurement points
        line = _line(points[0])
        assert "online=false" in line.lower()
        assert 'last_seen="2026-04-21T05:38:04Z"' in line
        # environmental measurements must NOT be written for an offline device
        assert "temperature=" not in line
        assert "co2_value" not in line

    def test_offline_still_tagged_and_discoverable(self):
        line = _line(InfluxDBStorage._device_points(OFFLINE_DEVICE, "Tyle")[0])
        assert "label=Loft" in line
        assert "serial_number=001372CBA9C4" in line

    def test_offline_writes_last_seen_age(self):
        line = _line(InfluxDBStorage._device_points(OFFLINE_DEVICE, "Tyle")[0])
        assert "last_seen_age_seconds=" in line  # numeric age for staleness coloring


class TestAgeSeconds:
    def test_parses_nanosecond_iso_z(self):
        # Kidde sends up to nanosecond precision + trailing Z
        age = _age_seconds("2020-01-01T00:00:00.591744434Z")
        assert age is not None and age > 0

    def test_parses_microsecond_iso(self):
        age = _age_seconds("2026-02-22T20:18:08.469481Z")
        assert age is not None and age > 0

    def test_none_and_garbage_return_none(self):
        assert _age_seconds(None) is None
        assert _age_seconds("not-a-timestamp") is None
        assert _age_seconds(12345) is None


class FakeResponse:
    """Minimal stand-in for the RESTResponse InfluxDBError unwraps.

    InfluxDBError reads .data for the message and getheaders()/getheader() for
    Retry-After, so a bare status object raises AttributeError inside the constructor.
    """

    def __init__(self, status, data=None):
        self.status = status
        self.data = data or b'{"message": "test error"}'

    def getheaders(self):
        return {}

    def getheader(self, _name, default=None):
        return default


class FakeWriteApi:
    """Records write calls; optionally raises to exercise the error arms."""

    def __init__(self, raises=None):
        self.raises = raises
        self.calls = []

    async def write(self, bucket=None, org=None, record=None):
        self.calls.append({"bucket": bucket, "org": org, "record": record})
        if self.raises is not None:
            raise self.raises


def _storage(monkeypatch, write_api=None):
    """An InfluxDBStorage with connect() bypassed — we test the write path, not the client."""
    monkeypatch.setattr(config, "INFLUXDB_URL", "http://influx:8086")
    monkeypatch.setattr(config, "INFLUXDB_TOKEN", "token")
    monkeypatch.setattr(config, "INFLUXDB_ORG", "org")
    monkeypatch.setattr(config, "INFLUXDB_BUCKET", "bucket")
    s = InfluxDBStorage()
    s.write_api = write_api
    return s


class TestWrite:
    async def test_no_points_is_a_noop(self, monkeypatch):
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        await s._write([])
        assert api.calls == []

    async def test_no_write_api_is_a_noop(self, monkeypatch):
        """Never write before connect() — must return quietly, not AttributeError."""
        s = _storage(monkeypatch, None)
        await s._write(InfluxDBStorage._device_points(DEVICE, "Tyle"))

    async def test_points_sent_as_one_batch(self, monkeypatch):
        """The poll cycle IS the batch — one awaited write per cycle, not one per point."""
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        await s._write(points)
        assert len(api.calls) == 1
        assert api.calls[0]["record"] == points
        assert api.calls[0]["bucket"] == "bucket"
        assert api.calls[0]["org"] == "org"

    async def test_influx_error_is_logged_not_raised(self, monkeypatch):
        api = FakeWriteApi(raises=InfluxDBError(response=FakeResponse(401)))
        s = _storage(monkeypatch, api)
        await s._write(InfluxDBStorage._device_points(DEVICE, "Tyle"))

    async def test_transport_error_is_logged_not_raised(self, monkeypatch):
        """A dropped batch self-heals next cycle — it must never propagate into the loop."""
        api = FakeWriteApi(raises=OSError("connection reset"))
        s = _storage(monkeypatch, api)
        await s._write(InfluxDBStorage._device_points(DEVICE, "Tyle"))


class TestWriteErrorGuidance:
    """The 401/404/422 arms exist to tell an operator what to actually fix."""

    def _messages(self, caplog, status):
        caplog.clear()
        InfluxDBStorage._log_write_error(InfluxDBError(response=FakeResponse(status)))
        return " ".join(r.getMessage() for r in caplog.records)

    def test_401_names_the_token_and_bucket(self, caplog):
        with caplog.at_level(logging.ERROR):
            assert "401" in self._messages(caplog, 401)

    def test_404_names_the_missing_bucket_or_org(self, caplog):
        with caplog.at_level(logging.ERROR):
            assert "404" in self._messages(caplog, 404)

    def test_422_is_a_warning_not_an_error(self, caplog):
        """A partial write persisted the valid points — a cycle warning, not a failure."""
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            InfluxDBStorage._log_write_error(InfluxDBError(response=FakeResponse(422)))
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_unknown_status_still_reports(self, caplog):
        with caplog.at_level(logging.ERROR):
            assert "failed" in self._messages(caplog, 500).lower()


class TestWriteDataset:
    async def test_empty_dataset_writes_nothing(self, monkeypatch):
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        await s.write_dataset(KiddeDataset(locations={}, devices=None, events=None))
        assert api.calls == []

    async def test_device_is_tagged_with_its_location_label(self, monkeypatch):
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        await s.write_dataset(
            KiddeDataset(
                locations={356103: {"id": 356103, "label": "Tyle"}},
                devices={553549: DEVICE},
                events=None,
            )
        )
        assert len(api.calls) == 1
        assert "location_label=Tyle" in _line(api.calls[0]["record"][0])

    async def test_unknown_location_falls_back_to_empty_label(self, monkeypatch):
        """A device whose location is missing must still be written, not dropped."""
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        await s.write_dataset(
            KiddeDataset(locations={}, devices={553549: DEVICE}, events=None)
        )
        assert len(api.calls) == 1


class TestCloseClient:
    async def test_close_without_a_client_is_a_noop(self, monkeypatch):
        s = _storage(monkeypatch)
        await s.close()

    async def test_close_releases_the_client(self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        s = _storage(monkeypatch)
        client = FakeClient()
        s.client = client
        await s.close()
        assert client.closed
        assert s.client is None and s.write_api is None

    async def test_a_failing_close_is_recorded_not_raised(self, monkeypatch, caplog):
        """Teardown must not mask the real shutdown cause — but must not vanish either."""

        class ExplodingClient:
            async def close(self):
                raise OSError("socket already gone")

        s = _storage(monkeypatch)
        s.client = ExplodingClient()
        with caplog.at_level(logging.WARNING):
            await s.close()
        assert s.client is None
        assert any(
            "closing the InfluxDB client" in r.getMessage() for r in caplog.records
        )


class FakeAsyncClient:
    """Stand-in for InfluxDBClientAsync covering ping/version/close outcomes."""

    def __init__(self, *, ping=True, ping_raises=None, version_raises=None):
        self._ping = ping
        self._ping_raises = ping_raises
        self._version_raises = version_raises
        self.closed = False

    async def ping(self):
        if self._ping_raises is not None:
            raise self._ping_raises
        return self._ping

    async def version(self):
        if self._version_raises is not None:
            raise self._version_raises
        return "2.7.1"

    def write_api(self):
        return FakeWriteApi()

    async def close(self):
        self.closed = True


class TestConstructorValidation:
    """Config is validated in __init__ so a misconfigured collector never reaches connect()."""

    @pytest.mark.parametrize(
        "missing", ["INFLUXDB_URL", "INFLUXDB_TOKEN", "INFLUXDB_ORG", "INFLUXDB_BUCKET"]
    )
    def test_missing_required_parameter_raises(self, monkeypatch, missing):
        monkeypatch.setattr(config, "INFLUXDB_URL", "http://influx:8086")
        monkeypatch.setattr(config, "INFLUXDB_TOKEN", "token")
        monkeypatch.setattr(config, "INFLUXDB_ORG", "org")
        monkeypatch.setattr(config, "INFLUXDB_BUCKET", "bucket")
        monkeypatch.setattr(config, missing, "")
        with pytest.raises(ValueError, match="Missing required InfluxDB parameter"):
            InfluxDBStorage()

    def test_non_http_url_raises(self, monkeypatch):
        """A bare host is the classic paste error — catch it at startup, not mid-write."""
        monkeypatch.setattr(config, "INFLUXDB_URL", "influx:8086")
        monkeypatch.setattr(config, "INFLUXDB_TOKEN", "token")
        monkeypatch.setattr(config, "INFLUXDB_ORG", "org")
        monkeypatch.setattr(config, "INFLUXDB_BUCKET", "bucket")
        with pytest.raises(ValueError, match="Invalid InfluxDB URL format"):
            InfluxDBStorage()


class TestConnect:
    def _patch_client(self, monkeypatch, fake):
        monkeypatch.setattr(
            "app.storage.influxdb.InfluxDBClientAsync", lambda **kwargs: fake
        )

    async def test_successful_connect_opens_the_write_api(self, monkeypatch):
        fake = FakeAsyncClient()
        self._patch_client(monkeypatch, fake)
        s = _storage(monkeypatch)
        await s.connect()
        assert s.write_api is not None
        assert not fake.closed

    async def test_unreachable_server_exits_rather_than_limping_on(self, monkeypatch):
        """ping() raising means the URL/port is wrong — fail fast at startup, loudly."""
        fake = FakeAsyncClient(ping_raises=OSError("connection refused"))
        self._patch_client(monkeypatch, fake)
        s = _storage(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            await s.connect()
        assert exc.value.code == 1
        assert fake.closed, "the half-open client must be released before exiting"

    async def test_unhealthy_ping_exits(self, monkeypatch):
        """A False ping is a live server that is not ready — equally fatal, not ignorable."""
        fake = FakeAsyncClient(ping=False)
        self._patch_client(monkeypatch, fake)
        s = _storage(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            await s.connect()
        assert exc.value.code == 1
        assert fake.closed

    async def test_version_failure_does_not_abort_a_working_connection(
        self, monkeypatch
    ):
        """version() is cosmetic log detail — ping() already proved the server is healthy."""
        fake = FakeAsyncClient(version_raises=OSError("no version endpoint"))
        self._patch_client(monkeypatch, fake)
        s = _storage(monkeypatch)
        await s.connect()
        assert s.write_api is not None, "the connection must survive a cosmetic failure"


class TestAgeSecondsNaive:
    def test_naive_timestamp_is_assumed_utc(self):
        """Kidde sometimes omits the zone; assuming UTC beats returning None for a real value."""
        assert _age_seconds("2026-01-28T18:52:54") is not None

    def test_future_timestamp_clamps_to_zero(self):
        """Clock skew must not produce a negative age a dashboard would render as garbage."""
        assert _age_seconds("2099-01-01T00:00:00Z") == 0.0


class TestDebugLineProtocol:
    async def test_points_are_dumped_as_line_protocol_at_debug(
        self, monkeypatch, caplog
    ):
        """The debug dump is the operator's only view of what was actually sent."""
        api = FakeWriteApi()
        s = _storage(monkeypatch, api)
        points = InfluxDBStorage._device_points(DEVICE, "Tyle")
        with caplog.at_level(logging.DEBUG, logger="kidde_collector"):
            await s._write(points)
        assert any("kidde_collector_device" in r.getMessage() for r in caplog.records)
