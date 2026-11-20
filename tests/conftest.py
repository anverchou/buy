import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.db import Session  # noqa: E402
from app.services.context import make_ctx  # noqa: E402

# 2026-11-19 13:00 Pacific
FIXED_NOW = datetime(2026, 11, 19, 21, 0, 0)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("SERPAPI_API_KEY", "KEEPA_API_KEY", "APP_PASSWORD", "NTFY_TOPIC", "DATA_MODE"):
        monkeypatch.delenv(var, raising=False)
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "WTF_CSRF_DISABLED": True,
        "SERPAPI_API_KEY": "",
        "KEEPA_API_KEY": "",
        "APP_PASSWORD": "",
        "NTFY_TOPIC": "",
        "DATA_MODE": "auto",
        "HTTP_RETRY_DELAY": 0,
    })
    with app.app_context():
        yield app
        Session.remove()


@pytest.fixture()
def session(app):
    return Session()


@pytest.fixture()
def ctx(app, session):
    return make_ctx(session, app.config, now=FIXED_NOW)


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


class FakeHTTP:
    """Records requests and answers from a function(params) -> (data, status)."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        data, status = self.handler(url, params or {})
        return FakeResp(data, status)
