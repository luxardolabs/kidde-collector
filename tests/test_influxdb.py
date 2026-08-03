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
