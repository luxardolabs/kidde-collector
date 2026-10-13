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


class TestMaxAge:
    def test_never_shorter_than_three_poll_intervals(self, monkeypatch):
        """A 15-minute poll must not fail a working collector against the 300s default."""
        monkeypatch.setenv("KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE", "300")
        monkeypatch.setenv("KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS", "900")
        reloaded = importlib.reload(health)
        try:
            assert reloaded.MAX_AGE_SECONDS == 2700
        finally:
            monkeypatch.delenv("KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS")
            importlib.reload(health)

    def test_default_is_the_configured_max_age(self, monkeypatch):
        monkeypatch.delenv("KIDDE_COLLECTOR_HEALTH_CHECK_MAX_AGE", raising=False)
        monkeypatch.delenv("KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS", raising=False)
        assert importlib.reload(health).MAX_AGE_SECONDS == 300


class TestHeartbeat:
    """check_heartbeat — healthy only after a cycle that reached InfluxDB, and recently."""

    def _beat(self, tmp_path, age_seconds):
        """Write the heartbeat file with an mtime age_seconds in the past."""
        f = tmp_path / health.HEARTBEAT_FILENAME
        f.touch()
        past = time.time() - age_seconds
        os.utime(f, (past, past))
        return f

    def test_no_successful_cycle_is_unhealthy(self, tmp_path, monkeypatch):
        """The adversarial case: process alive, Kidde login failing, nothing written."""
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        ok, msg = health.check_heartbeat()
        assert ok is False
        assert "No successful cycle yet" in msg

    def test_missing_output_dir_is_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path / "nope"))
        ok, _ = health.check_heartbeat()
        assert ok is False

    def test_fresh_heartbeat_is_healthy(self, tmp_path, monkeypatch):
        self._beat(tmp_path, age_seconds=5)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_heartbeat()
        assert ok is True
        assert "Healthy" in msg

    def test_stale_heartbeat_is_unhealthy(self, tmp_path, monkeypatch):
        """Writes stopped succeeding an hour ago: the collector is broken, not healthy."""
        self._beat(tmp_path, age_seconds=3600)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_heartbeat()
        assert ok is False
        assert "3600s ago" in msg or "s ago" in msg

    def test_age_is_measured_on_the_absolute_epoch(self, tmp_path, monkeypatch):
        """The age must track real elapsed time, not local wall-clock.

        Guards the tz-aware subtraction: both operands are absolute UTC, so the result is
        the true elapsed time (a naive local subtraction reads 0s across a DST fall-back).
        """
        self._beat(tmp_path, age_seconds=120)
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        ok, msg = health.check_heartbeat()
        assert ok is True
        reported = int(msg.split("cycle ")[1].split("s ago")[0])
        assert 115 <= reported <= 125


class TestHealthcheckCLI:
    """main() is the Docker HEALTHCHECK entrypoint — its exit code IS the container's health.

    Docker reads only the exit code (0 healthy / non-zero unhealthy) and shows stdout in
    `docker inspect .State.Health.Log`. Both halves are the contract, so both are asserted.
    """

    def test_exits_one_before_any_successful_cycle(self, tmp_path, monkeypatch, capsys):
        """Process-alive is not health: with no successful cycle the check must fail."""
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 1
        assert "UNHEALTHY" in capsys.readouterr().out

    def test_exits_zero_after_a_recent_successful_cycle(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / health.HEARTBEAT_FILENAME).touch()
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(health, "MAX_AGE_SECONDS", 300)
        with pytest.raises(SystemExit) as exc:
            health.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "HEALTHY" in out and "UNHEALTHY" not in out
        assert "Last successful cycle" in out

    def test_reports_the_build_version_for_the_operator(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(health, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("KIDDE_COLLECTOR_VERSION", "2026.09.0")
        monkeypatch.setenv("KIDDE_COLLECTOR_BUILD_TIMESTAMP", "2026-09-06T00:00:00Z")
        with pytest.raises(SystemExit):
            health.main()
        assert "2026.09.0" in capsys.readouterr().out
