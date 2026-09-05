import importlib
import os
import time

health = importlib.import_module("app.health.check")


class TestIntEnv:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("KIDDE_TEST_AGE", raising=False)
        assert health._int_env("KIDDE_TEST_AGE", 300, 30, 3600) == 300

    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("KIDDE_TEST_AGE", "120")
        assert health._int_env("KIDDE_TEST_AGE", 300, 30, 3600) == 120

    def test_clamped_to_bounds(self, monkeypatch):
        monkeypatch.setenv("KIDDE_TEST_AGE", "999999")
        assert health._int_env("KIDDE_TEST_AGE", 300, 30, 3600) == 3600
        monkeypatch.setenv("KIDDE_TEST_AGE", "1")
        assert health._int_env("KIDDE_TEST_AGE", 300, 30, 3600) == 30

    def test_non_numeric_falls_back_no_raise(self, monkeypatch):
        monkeypatch.setenv("KIDDE_TEST_AGE", "not-a-number")
        assert health._int_env("KIDDE_TEST_AGE", 300, 30, 3600) == 300


class TestProcessAlive:
    def test_reports_healthy(self):
        ok, msg = health.check_process_alive()
        assert ok is True
        assert "healthy" in msg.lower()


class TestRecentCapture:
    """Cover check_recent_capture — the freshness strategy used when raw capture is on.

    This function had NO test coverage, which is how the naive-datetime defect it now
    guards against went unnoticed.
    """

    def _capture(self, tmp_path, age_seconds):
        """Write a capture file whose mtime is age_seconds in the past."""
        f = tmp_path / "api_data_2026-09-05.jsonl"
        f.write_text('{"devices": {}}\n')
        past = time.time() - age_seconds
        os.utime(f, (past, past))
        return f

    def test_missing_output_dir_is_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path / "nope"))
        ok, msg = health.check_recent_capture()
        assert ok is False
        assert "does not exist" in msg

    def test_no_capture_file_is_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        ok, msg = health.check_recent_capture()
        assert ok is False
        assert "No api_data_*.jsonl" in msg

    def test_fresh_capture_is_healthy(self, tmp_path, monkeypatch):
        self._capture(tmp_path, age_seconds=5)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_recent_capture()
        assert ok is True
        assert "Healthy" in msg

    def test_stale_capture_is_unhealthy(self, tmp_path, monkeypatch):
        self._capture(tmp_path, age_seconds=3600)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_recent_capture()
        assert ok is False
        assert "old" in msg

    def test_newest_capture_wins(self, tmp_path, monkeypatch):
        """Several days of captures: freshness is judged on the newest, not an arbitrary one."""
        for day, age in (("01", 90000), ("02", 45000), ("05", 10)):
            f = tmp_path / f"api_data_2026-09-{day}.jsonl"
            f.write_text("{}\n")
            os.utime(f, (time.time() - age, time.time() - age))
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, _ = health.check_recent_capture()
        assert ok is True

    def test_age_is_measured_on_the_absolute_epoch(self, tmp_path, monkeypatch):
        """The age must track real elapsed time, not local wall-clock.

        Guards the tz-aware subtraction: both operands are absolute UTC, so the result is
        the true elapsed time. (The DST-fold case this protects against needs a frozen
        clock inside the repeated hour to reproduce as a unit test; it is verified by
        direct computation instead — see the comment in check_recent_capture.)
        """
        self._capture(tmp_path, age_seconds=120)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_recent_capture()
        assert ok is True
        reported = int(msg.split("update ")[1].split("s ago")[0])
        assert 115 <= reported <= 125
