"""Tests for config loading."""
from __future__ import annotations
import os
import pytest
from unittest.mock import patch


def base_env(**overrides):
    e = {
        "API_ID": "12345", "API_HASH": "abc",
        "SESSION_STRING": "sess", "BOT_TOKEN": "123:tok",
        "OWNER_ID": "99999",
    }
    e.update(overrides)
    return e


class TestConfig:
    def test_valid(self):
        from app.config import Config
        with patch.dict(os.environ, base_env(), clear=True):
            c = Config.from_env()
            assert c.api_id == 12345
            assert c.owner_id == 99999

    def test_missing_api_id(self):
        from app.config import Config
        e = base_env()
        e.pop("API_ID")
        with patch.dict(os.environ, e, clear=True):
            with pytest.raises(EnvironmentError, match="API_ID"):
                Config.from_env()

    def test_missing_session(self):
        from app.config import Config
        e = base_env()
        e.pop("SESSION_STRING")
        with patch.dict(os.environ, e, clear=True):
            with pytest.raises(EnvironmentError, match="SESSION_STRING"):
                Config.from_env()

    def test_invalid_api_id(self):
        from app.config import Config
        with patch.dict(os.environ, base_env(API_ID="xyz"), clear=True):
            with pytest.raises(EnvironmentError, match="API_ID"):
                Config.from_env()

    def test_defaults(self):
        from app.config import Config
        with patch.dict(os.environ, base_env(), clear=True):
            c = Config.from_env()
            assert c.max_concurrent_downloads == 3
            assert c.max_retries == 5
            assert c.log_level == "INFO"
