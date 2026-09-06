"""Tests for KiddeCollector — the poll loop.

poller.py was at 0% coverage (KIDDECOLLE-50). It owns two contracts nothing asserted:
the loop CONTINUES despite a failed cycle (CLAUDE.md), and an expired session clears the
cached cookie so the next cycle re-authenticates.
"""

import asyncio
import json

import pytest

from app.collector.client import KiddeClientAuthError, KiddeDataset
from app.collector.poller import KiddeCollector
from app.core import config


class FakeSession:
    """Stand-in for KiddeSession that records whether the cookie cache was dropped."""

    def __init__(self, client=None):
        self._client = client
        self.invalidate_calls = 0

    async def get_client(self):
        return self._client

    def invalidate(self):
        self.invalidate_calls += 1


class FakeStorage:
    """Stand-in for InfluxDBStorage that records what it was asked to write."""

    def __init__(self):
        self.written = []

    async def write_dataset(self, data):
        self.written.append(data)


class FakeClient:
    def __init__(self, dataset):
        self._dataset = dataset
        self.calls = []

    async def get_data(self, get_devices=False, get_events=False):
        self.calls.append({"get_devices": get_devices, "get_events": get_events})
        return self._dataset


def dataset():
    return KiddeDataset(locations={1: {"id": 1}}, devices={2: {"id": 2}}, events=None)


@pytest.fixture
def collector(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_FOLDER", str(tmp_path / "out"))
    monkeypatch.setattr(config, "FETCH_INTERVAL_SECONDS", 0)
    return KiddeCollector(FakeSession(), FakeStorage())


class TestInit:
    def test_creates_the_export_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "deep" / "out"
        monkeypatch.setattr(config, "EXPORT_FOLDER", str(target))
        KiddeCollector(FakeSession(), FakeStorage())
        assert target.is_dir()

    def test_unwritable_export_dir_raises(self, tmp_path, monkeypatch):
        """A collector that cannot write its capture dir must fail loudly at startup."""
        blocker = tmp_path / "a-file"
        blocker.write_text("not a directory")
        monkeypatch.setattr(config, "EXPORT_FOLDER", str(blocker / "out"))
        with pytest.raises(RuntimeError, match="Error creating export directory"):
            KiddeCollector(FakeSession(), FakeStorage())


class TestCollectOnce:
    async def test_raises_when_no_client(self, collector):
        with pytest.raises(RuntimeError, match="Failed to create KiddeClient"):
            await collector._collect_once()

    async def test_writes_the_dataset_to_storage(self, collector, monkeypatch):
        monkeypatch.setattr(config, "WRITE_API_DATA", False)
        ds = dataset()
        collector.session = FakeSession(FakeClient(ds))
        await collector._collect_once()
        assert collector.storage.written == [ds]

    async def test_honors_the_get_events_flag(self, collector, monkeypatch):
        monkeypatch.setattr(config, "WRITE_API_DATA", False)
        monkeypatch.setattr(config, "GET_EVENTS", True)
        client = FakeClient(dataset())
        collector.session = FakeSession(client)
        await collector._collect_once()
        assert client.calls == [{"get_devices": True, "get_events": True}]

    async def test_raw_dump_skipped_when_disabled(
        self, collector, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "WRITE_API_DATA", False)
        collector.session = FakeSession(FakeClient(dataset()))
        await collector._collect_once()
        assert list((tmp_path / "out").glob("api_data_*.jsonl")) == []

    async def test_raw_dump_written_when_enabled(
        self, collector, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config, "WRITE_API_DATA", True)
        collector.session = FakeSession(FakeClient(dataset()))
        await collector._collect_once()
        assert len(list((tmp_path / "out").glob("api_data_*.jsonl"))) == 1


class TestDumpRaw:
    async def test_appends_one_valid_json_object_per_line(self, collector, tmp_path):
        """NDJSON: each cycle appends ONE parseable line — never a bare concatenation."""
        await collector._dump_raw({"cycle": 1})
        await collector._dump_raw({"cycle": 2})
        files = list((tmp_path / "out").glob("api_data_*.jsonl"))
        assert len(files) == 1, "same day must append to one file, not fan out"
        lines = files[0].read_text().splitlines()
        assert [json.loads(line) for line in lines] == [{"cycle": 1}, {"cycle": 2}]

    async def test_filename_is_date_partitioned(self, collector, tmp_path):
        await collector._dump_raw({"a": 1})
        name = next((tmp_path / "out").glob("api_data_*.jsonl")).name
        assert name.startswith("api_data_")
        assert name.endswith(".jsonl")
        # api_data_YYYY-MM-DD.jsonl
        assert len(name) == len("api_data_2026-09-05.jsonl")


class TestRunLoop:
    async def test_exits_immediately_when_already_shut_down(self, collector):
        calls = []

        async def counted():
            calls.append(1)

        collector._collect_once = counted
        event = asyncio.Event()
        event.set()
        await collector.run(event)
        assert calls == [], "a pre-set shutdown must not run a cycle"

    async def test_runs_cycles_until_shutdown(self, collector):
        event = asyncio.Event()
        calls = []

        async def counted():
            calls.append(1)
            if len(calls) == 3:
                event.set()

        collector._collect_once = counted
        await collector.run(event)
        assert len(calls) == 3

    async def test_a_failed_cycle_does_not_kill_the_loop(self, collector):
        """THE resilience contract: one bad cycle must not end the process."""
        event = asyncio.Event()
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("malformed payload from Kidde")
            event.set()

        collector._collect_once = flaky
        await collector.run(event)  # must return normally, not propagate
        assert len(calls) == 2, "the loop must run again after a failed cycle"

    async def test_expired_session_clears_the_cookie_cache(self, collector):
        """The 401/403 self-heal: drop the cached cookie so the next cycle re-authenticates."""
        event = asyncio.Event()
        calls = []

        async def expired():
            calls.append(1)
            if len(calls) == 1:
                raise KiddeClientAuthError("session expired")
            event.set()

        collector._collect_once = expired
        await collector.run(event)
        assert collector.session.invalidate_calls == 1
        assert len(calls) == 2, "it must retry the cycle after re-auth is armed"

    async def test_shutdown_mid_sleep_ends_the_loop_promptly(
        self, collector, monkeypatch
    ):
        """A SIGTERM during the inter-cycle sleep must break the wait, not burn the interval."""
        monkeypatch.setattr(config, "FETCH_INTERVAL_SECONDS", 30)
        event = asyncio.Event()

        async def one_cycle():
            event.set()  # shut down while the loop is about to sleep 30s

        collector._collect_once = one_cycle
        await asyncio.wait_for(collector.run(event), timeout=5)
