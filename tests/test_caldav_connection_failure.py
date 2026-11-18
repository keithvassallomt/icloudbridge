"""A CalDAV server that can't be reached must not look like one with no calendars.

These talk to a real local port that nothing listens on, so the caldav
library fails the way it does for an unreachable server.
"""
from __future__ import annotations

import socket

import pytest
from fastapi import HTTPException

from icloudbridge.api.routes import config as config_routes
from icloudbridge.core.config import AppConfig, RemindersConfig
from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter
from icloudbridge.utils import settings_db
from icloudbridge.utils.exceptions import SourceUnavailableError


def _unreachable_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/remote.php/dav"


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.list_calendars(),
        lambda a: a.list_calendar_urls(),
        lambda a: a.get_todos("Tasks"),
        lambda a: a.create_calendar("Tasks"),
        lambda a: a.delete_todo("http://127.0.0.1/todo.ics"),
    ],
    ids=["list_calendars", "list_calendar_urls", "get_todos", "create_calendar", "delete_todo"],
)
async def test_an_unreachable_server_raises_instead_of_looking_empty(call):
    adapter = CalDAVAdapter(_unreachable_url(), "me", "pw")

    with pytest.raises(SourceUnavailableError, match="Could not connect to the CalDAV server at http://127.0.0.1"):
        await call(adapter)


def _saved_config(tmp_path, monkeypatch, **reminders) -> AppConfig:
    config = AppConfig(general={"data_dir": tmp_path})
    for name, value in reminders.items():
        setattr(config.reminders, name, value)
    config.save_to_file(config.default_config_path)
    monkeypatch.setattr(settings_db, "get_config_path", lambda: config.default_config_path)
    monkeypatch.setattr(RemindersConfig, "get_caldav_password", lambda self: "pw")
    return config


async def test_connection_test_fails_for_an_unreachable_server(tmp_path, monkeypatch):
    config = _saved_config(tmp_path, monkeypatch, caldav_url=_unreachable_url(), caldav_username="me")

    result = await config_routes.test_connection("reminders", config)

    assert result["success"] is False
    assert "Could not connect to the CalDAV server" in result["message"]


@pytest.mark.parametrize(
    "reminders",
    [{"caldav_username": "me"}, {"caldav_url": "https://example.invalid/remote.php/dav"}],
    ids=["no-url", "no-username"],
)
async def test_connection_test_says_what_is_missing(tmp_path, monkeypatch, reminders):
    config = _saved_config(tmp_path, monkeypatch, **reminders)

    with pytest.raises(HTTPException) as raised:
        await config_routes.test_connection("reminders", config)

    assert raised.value.status_code == 400
    assert raised.value.detail == "CalDAV URL and username must be configured"
