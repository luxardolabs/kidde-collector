import pytest

from app.collector.client import KiddeDataset, _dict_by_ids
from app.collector.endpoints import KiddeAPIEndpoints


class TestDictByIds:
    def test_keys_by_id(self):
        items = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
        result = _dict_by_ids(items)
        assert result == {1: {"id": 1, "v": "a"}, 2: {"id": 2, "v": "b"}}

    def test_duplicate_ids_raise(self):
        with pytest.raises(ValueError, match="Duplicate IDs"):
            _dict_by_ids([{"id": 1}, {"id": 1}])


class TestEndpoints:
    def test_login_and_locations_absolute(self):
        assert KiddeAPIEndpoints.LOGIN.endswith("/auth/login")
        assert KiddeAPIEndpoints.LOCATIONS.endswith("/location")

    def test_device_and_event_templates(self):
        assert KiddeAPIEndpoints.LOCATION_DEVICES.format(location_id=356103).endswith(
            "/location/356103/device"
        )
        assert KiddeAPIEndpoints.LOCATION_EVENTS.format(location_id=356103).endswith(
            "/location/356103/event"
        )


class TestKiddeDataset:
    def test_dataset_holds_maps(self):
        ds = KiddeDataset(locations={1: {"id": 1}}, devices={2: {"id": 2}}, events=None)
        assert ds.locations[1]["id"] == 1
        assert ds.devices[2]["id"] == 2
        assert ds.events is None
