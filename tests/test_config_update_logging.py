"""Saving settings must not print or log the credentials in the request."""
from __future__ import annotations

import logging

from icloudbridge.api.models import ConfigUpdateRequest
from icloudbridge.api.routes import config as config_routes
from icloudbridge.core.config import AppConfig

CREDENTIALS = {
    "reminders_caldav_password": "caldav-pw-1f3a",
    "passwords_vaultwarden_password": "vw-pw-8c2d",
    "passwords_vaultwarden_client_id": "user.vw-client-5e7b",
    "passwords_vaultwarden_client_secret": "vw-cs-9a4f",
    "passwords_nextcloud_app_password": "nc-app-2b6e",
    "notifications_smtp_password": "smtp-pw-7d1c",
}


async def test_config_update_keeps_credentials_out_of_output(tmp_path, monkeypatch, caplog, capsys):
    store = config_routes.CredentialStore
    for name in dir(store):
        if name.startswith("set_") or name.startswith("get_"):
            monkeypatch.setattr(store, name, lambda self, *args, **kwargs: None)
    config = AppConfig(general={"data_dir": tmp_path})
    config.passwords.enabled = True
    update = ConfigUpdateRequest(
        reminders_caldav_username="me",
        passwords_vaultwarden_email="me@example.com",
        passwords_nextcloud_username="me",
        notifications_smtp_username="me",
        **CREDENTIALS,
    )

    with caplog.at_level(logging.DEBUG):
        await config_routes.update_config(update, config)

    output = caplog.text + capsys.readouterr().out
    assert "Received config update request" in caplog.text
    for value in CREDENTIALS.values():
        assert value not in output


def test_loggable_update_masks_only_credentials():
    update = ConfigUpdateRequest(passwords_provider="nextcloud", **CREDENTIALS)

    logged = config_routes._loggable_update(update)

    assert logged["passwords_provider"] == "nextcloud"
    assert all(logged[name] == "***" for name in CREDENTIALS)
