"""
tests/test_config.py
====================
Unit tests for app/config.py — centralised Pydantic BaseSettings.
"""

import pytest
from app.config import Settings


class TestSettings:

    def test_default_db_values(self, monkeypatch):
        monkeypatch.delenv("DB_HOST",     raising=False)
        monkeypatch.delenv("DB_PORT",     raising=False)
        monkeypatch.delenv("DB_NAME",     raising=False)
        monkeypatch.delenv("DB_USER",     raising=False)
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        s = Settings(_env_file=None)
        assert s.db_host == "localhost"
        assert s.db_port == 5432
        assert s.db_pool_size == 10

    def test_database_url_computed(self, monkeypatch):
        monkeypatch.setenv("DB_USER",     "alice")
        monkeypatch.setenv("DB_PASSWORD", "secret")
        monkeypatch.setenv("DB_HOST",     "pg-host")
        monkeypatch.setenv("DB_PORT",     "5433")
        monkeypatch.setenv("DB_NAME",     "mydb")
        s = Settings(_env_file=None)
        assert s.database_url == "postgresql+psycopg2://alice:secret@pg-host:5433/mydb"

    def test_redis_cluster_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REDIS_CLUSTER_NODES", raising=False)
        s = Settings(_env_file=None)
        assert s.redis_cluster_enabled is False

    def test_redis_cluster_enabled_when_nodes_set(self, monkeypatch):
        monkeypatch.setenv(
            "REDIS_CLUSTER_NODES",
            "node1:6379,node2:6379,node3:6379",
        )
        s = Settings(_env_file=None)
        assert s.redis_cluster_enabled is True

    def test_log_level_default(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        s = Settings(_env_file=None)
        assert s.log_level == "INFO"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL",  "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "text")
        s = Settings(_env_file=None)
        assert s.log_level == "DEBUG"
        assert s.log_format == "text"

    def test_db_pool_settings_override(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_SIZE",    "20")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "40")
        s = Settings(_env_file=None)
        assert s.db_pool_size == 20
        assert s.db_max_overflow == 40

    def test_missing_llm_key_warns(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.warns(RuntimeWarning, match="LLM_API_KEY"):
            Settings(_env_file=None)

    def test_model_defaults(self, monkeypatch):
        s = Settings(_env_file=None)
        assert s.model1 == "gpt-4o-mini"
        assert s.model2 == "gpt-4.1"
        assert s.model3 == "o3-mini"
        assert s.model4 == "gpt-4o"

    def test_prometheus_enabled_default(self, monkeypatch):
        s = Settings(_env_file=None)
        assert s.prometheus_enabled is True

    def test_prometheus_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_ENABLED", "false")
        s = Settings(_env_file=None)
        assert s.prometheus_enabled is False
