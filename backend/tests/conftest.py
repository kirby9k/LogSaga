"""
Shared pytest fixtures for LogSAGA tests.
"""

import os
os.environ.setdefault("KODI_DB_PASSWORD", "testpassword")

import pytest
from unittest.mock import MagicMock, patch

from logsaga.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Return a Settings instance with test values (no real DB needed)."""
    return Settings(
        kodi_db_host="127.0.0.1",
        kodi_db_port=3306,
        kodi_db_user="logsaga",
        kodi_db_password="testpassword",
        kodi_db_name="MyVideos131",
        kodi_jsonrpc_url="http://127.0.0.1:8080/jsonrpc",
    )
