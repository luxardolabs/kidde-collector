import importlib

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
