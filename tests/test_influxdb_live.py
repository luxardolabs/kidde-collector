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
        f'|> filter(fn: (r) => r._measurement == "kidde_collector_device" '
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


async def test_connect_fails_fast_on_an_unreachable_server(
    live: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same live config, wrong port: ping() must fail and connect() must exit, not hang.
    monkeypatch.setattr(config, "INFLUXDB_URL", live["url"].rsplit(":", 1)[0] + ":1")
    storage = InfluxDBStorage()
    with pytest.raises(SystemExit):
        await storage.connect()
