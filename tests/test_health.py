import importlib
import os
import time

import pytest

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


class TestHealthcheckCLI:
    """main() is the Docker HEALTHCHECK entrypoint — its exit code IS the container's health.

    Docker reads only the exit code (0 healthy / non-zero unhealthy) and shows stdout in
    `docker inspect .State.Health.Log`. Both halves are the contract, so both are asserted.
    """

    def test_exits_zero_and_reports_healthy_on_the_liveness_path(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv("KIDDE_COLLECTOR_WRITE_API_DATA", "false")
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "HEALTHY" in out and "UNHEALTHY" not in out
        assert "Process check" in out

    def test_exits_one_when_the_capture_is_stale(self, tmp_path, monkeypatch, capsys):
        """Capture mode with no capture file must fail the healthcheck, not pass it."""
        monkeypatch.setenv("KIDDE_COLLECTOR_WRITE_API_DATA", "true")
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 1
        assert "UNHEALTHY" in capsys.readouterr().out

    def test_exits_zero_when_the_capture_is_fresh(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "api_data_2026-09-05.jsonl"
        f.write_text("{}\n")
        monkeypatch.setenv("KIDDE_COLLECTOR_WRITE_API_DATA", "true")
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 0
        assert "Capture freshness" in capsys.readouterr().out

    def test_reports_the_build_version_for_the_operator(self, monkeypatch, capsys):
        monkeypatch.setenv("KIDDE_COLLECTOR_WRITE_API_DATA", "false")
        monkeypatch.setenv("KIDDE_COLLECTOR_VERSION", "2026.09.0")
        monkeypatch.setenv("KIDDE_COLLECTOR_BUILD_TIMESTAMP", "2026-09-06T00:00:00Z")
        with pytest.raises(SystemExit):
            health.main()
        assert "2026.09.0" in capsys.readouterr().out

    def test_write_api_data_flag_is_case_insensitive(
        self, tmp_path, monkeypatch, capsys
    ):
        """The env var is operator-set text — "True" must select capture mode like "true"."""
        monkeypatch.setenv("KIDDE_COLLECTOR_WRITE_API_DATA", "True")
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path / "absent"))
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 1, (
            "capture mode must be selected, not the liveness path"
        )
