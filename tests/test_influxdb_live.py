"""The write path against a REAL InfluxDB 2.7 — the server the deployed stacks run.

`make test` starts `kidde_influxdb_test` (compose profile `test`) in a project of its own and
hands its URL in as TEST_INFLUXDB_*. The unit tests in test_influxdb.py pin the line protocol;
these prove the server accepts it: connect() pings a live server, write_dataset() lands, and a
Flux query reads back the tags and fields the dashboards select on.
"""

import os
import uuid

import pytest
from influxdb_client import InfluxDBClient

from app.collector.client import KiddeDataset
from app.core import config
from app.storage.influxdb import InfluxDBStorage


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is unset — run the suite with `make test`, which starts InfluxDB"
        )
    return value


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    settings = {
        "url": _env("TEST_INFLUXDB_URL"),
        "token": _env("TEST_INFLUXDB_TOKEN"),
        "org": _env("TEST_INFLUXDB_ORG"),
        "bucket": _env("TEST_INFLUXDB_BUCKET"),
    }
    monkeypatch.setattr(config, "INFLUXDB_URL", settings["url"])
    monkeypatch.setattr(config, "INFLUXDB_TOKEN", settings["token"])
    monkeypatch.setattr(config, "INFLUXDB_ORG", settings["org"])
    monkeypatch.setattr(config, "INFLUXDB_BUCKET", settings["bucket"])
    return settings


def _query(settings: dict[str, str], serial: str) -> dict[str, object]:
    flux = (
        f'from(bucket: "{settings["bucket"]}") |> range(start: -1h) '
        f'|> filter(fn: (r) => r._measurement == "{config.MEASUREMENT_DEVICE}" '
        f'and r.serial_number == "{serial}")'
    )
    # The sync client reads back: the async query API needs the optional aiocsv extra.
    with InfluxDBClient(
        url=settings["url"], token=settings["token"], org=settings["org"]
    ) as client:
        tables = client.query_api().query(flux)
    fields: dict[str, object] = {}
    for table in tables:
        for record in table.records:
            fields[record.get_field()] = record.get_value()
            fields["label"] = record.values.get("label")
            fields["location_label"] = record.values.get("location_label")
    return fields


async def test_dataset_lands_in_a_real_influxdb(live: dict[str, str]) -> None:
    serial = f"LIVE{uuid.uuid4().hex[:8].upper()}"
    device = {
        "id": 1,
        "serial_number": serial,
        "location_id": 7,
        "label": "Basement",
        "smoke_alarm": False,
        "temperature": 78,
        "co2": {"value": 922.37, "status": "Good", "Unit": "PPM"},
    }
    data = KiddeDataset(
        locations={7: {"id": 7, "label": "Live Home"}}, devices={1: device}, events=None
    )

    storage = InfluxDBStorage()
    await storage.connect()
    try:
        await storage.write_dataset(data)
    finally:
        await storage.close()

    fields = _query(live, serial)
    assert fields["label"] == "Basement"
    assert fields["location_label"] == "Live Home"
    assert fields["temperature"] == 78.0
    assert fields["smoke_alarm"] is False
    assert fields["co2_value"] == 922.37
    assert fields["co2_status"] == "Good"


async def _write(data: KiddeDataset) -> bool:
    storage = InfluxDBStorage()
    await storage.connect()
    try:
        return await storage.write_dataset(data)
    finally:
        await storage.close()


def _device(serial: str, **extra: object) -> dict[str, object]:
    return {
        "id": hash(serial) % 1_000_000,
        "serial_number": serial,
        "location_id": 7,
        "label": "Hall",
        "smoke_alarm": False,
        **extra,
    }


@pytest.mark.parametrize("conflict", [True, False])
async def test_a_field_type_conflict_is_not_reported_as_landed(
    live: dict[str, str], monkeypatch: pytest.MonkeyPatch, conflict: bool
) -> None:
    """InfluxDB answers a field type conflict with 422 and drops the WHOLE line, this
    detector's smoke_alarm included, so the heartbeat must not vouch for that cycle."""
    # A measurement of its own: field types persist on the server across tests.
    monkeypatch.setattr(
        config, "MEASUREMENT_DEVICE", f"kidde_conflict_{uuid.uuid4().hex[:8]}"
    )
    first, second = (f"LIVE{uuid.uuid4().hex[:8].upper()}" for _ in range(2))
    locations = {7: {"id": 7, "label": "Live Home"}}
    assert await _write(
        KiddeDataset(locations, {1: _device(first, alarm_state=True)}, None)
    )

    drifted = _device(second, smoke_alarm=True, alarm_state=1 if conflict else False)
    landed = await _write(KiddeDataset(locations, {2: drifted}, None))

    stored = _query(live, second)
    assert landed is not conflict
    assert ("smoke_alarm" in stored) is not conflict


@pytest.mark.parametrize("label", ["Hall\\", "Hall"])
async def test_a_trailing_backslash_label_is_stored_without_mangling(
    live: dict[str, str], label: str
) -> None:
    """A trailing backslash would escape the tag separator; the label is stored "Hall"."""
    serial = f"LIVE{uuid.uuid4().hex[:8].upper()}"
    await _write(
        KiddeDataset(
            {7: {"id": 7, "label": "Live Home"}},
            {1: _device(serial, label=label)},
            None,
        )
    )
    assert _query(live, serial)["label"] == "Hall"


async def test_connect_fails_fast_on_an_unreachable_server(
    live: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Same live config, wrong port: ping() must fail and connect() must exit, not hang.
    monkeypatch.setattr(config, "INFLUXDB_URL", live["url"].rsplit(":", 1)[0] + ":1")
    storage = InfluxDBStorage()
    with pytest.raises(SystemExit):
        await storage.connect()
    # One record, with the transport error's traceback attached.
    (failed,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert "could not reach" in failed.getMessage()
    assert failed.exc_info is not None
