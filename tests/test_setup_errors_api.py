"""A service that isn't set up gets a clear 400 the web UI can show, not "Internal server error"."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from icloudbridge.api import dependencies
from icloudbridge.api.app import app


def photos_config(tmp_path, **photos):
    settings = {"enabled": True, "sync_mode": "bidirectional", "sources": {}, **photos}
    settings.setdefault("export", SimpleNamespace(export_folder=None, organize_by="date"))
    return SimpleNamespace(
        general=SimpleNamespace(data_dir=tmp_path), photos=SimpleNamespace(**settings), ensure_data_dir=lambda: None
    )


@pytest.mark.parametrize("path", ["/api/photos/sync", "/api/photos/export"])
def test_photos_turned_off_is_a_clear_error(tmp_path, path):
    app.dependency_overrides[dependencies.get_config] = lambda: photos_config(tmp_path, enabled=False)
    try:
        response = TestClient(app).post(path, json={"dry_run": False})
    finally:
        app.dependency_overrides.clear()

    # Before: 500 {"error": "Internal server error"}.
    assert response.status_code == 400
    assert response.json()["detail"] == "Photos sync is turned off. Turn on Enable Photos Sync in Settings."


@pytest.mark.parametrize(
    ("dependency", "config", "message"),
    [
        (
            dependencies.get_notes_sync_engine,
            lambda tmp: SimpleNamespace(
                general=SimpleNamespace(data_dir=tmp), notes=SimpleNamespace(remote_folder=None),
                ensure_data_dir=lambda: None,
            ),
            "Choose a Notes Folder in Settings",
        ),
        (
            dependencies.get_reminders_sync_engine,
            lambda tmp: SimpleNamespace(
                general=SimpleNamespace(data_dir=tmp), reminders=SimpleNamespace(get_caldav_password=lambda: None),
                ensure_data_dir=lambda: None,
            ),
            "Enter it under Reminders Sync in Settings",
        ),
        (
            dependencies.get_photos_export_engine,
            lambda tmp: photos_config(tmp, sync_mode="import"),
            "Photo Sync Mode set to Bidirectional Sync",
        ),
        (
            dependencies.get_photos_export_engine,
            lambda tmp: photos_config(tmp),
            "Add a photos source folder in Settings",
        ),
    ],
)
async def test_setup_problems_say_what_to_set_up(tmp_path, dependency, config, message):
    with pytest.raises(HTTPException) as exc:
        await dependency(config(tmp_path))
    assert exc.value.status_code == 400
    assert message in exc.value.detail
