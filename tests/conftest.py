# tests/conftest.py
import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MANDATE_DB", path)
    monkeypatch.setenv("MANDATE_SIGNING_KEY", "test-key-not-for-production")
    from mandate.db import init_schema
    init_schema()
    yield path
    try:
        os.unlink(path)
    except PermissionError:
        pass