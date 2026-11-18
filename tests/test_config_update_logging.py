"""Saving settings must not print or log the credentials in the request."""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from pydantic import BaseModel

import icloudbridge.api.routes
from icloudbridge.api import models as api_models
from icloudbridge.api.models import ConfigUpdateRequest, credential_field, credential_fields
from icloudbridge.api.routes import config as config_routes
from icloudbridge.core.config import AppConfig

# Field names that look like they hold a secret. Only a tripwire: what gets
# masked is decided by credential_field(), not by the name.
SECRET_NAME_ENDINGS = ("password", "secret", "client_id", "token", "api_key", "passphrase")

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


def test_the_config_credentials_are_marked():
    assert credential_fields(ConfigUpdateRequest) == set(CREDENTIALS)


def test_repr_leaves_out_credentials():
    update = ConfigUpdateRequest(reminders_caldav_username="me", **CREDENTIALS)

    text = repr(update) + str(update)

    assert "reminders_caldav_username='me'" in text
    for value in CREDENTIALS.values():
        assert value not in text


def _api_models() -> list[type[BaseModel]]:
    modules = [api_models] + [
        importlib.import_module(f"icloudbridge.api.routes.{info.name}")
        for info in pkgutil.iter_modules(icloudbridge.api.routes.__path__)
    ]
    return [
        cls
        for module in modules
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, BaseModel) and cls.__module__ == module.__name__
    ]


def _unmarked_secret_fields(model: type[BaseModel]) -> set[str]:
    return {
        name
        for name in model.model_fields
        if name.endswith(SECRET_NAME_ENDINGS) and name not in credential_fields(model)
    }


def test_every_field_that_looks_like_a_secret_is_marked():
    models = _api_models()
    assert ConfigUpdateRequest in models

    unmarked = {m.__name__: _unmarked_secret_fields(m) for m in models}

    assert {name: fields for name, fields in unmarked.items() if fields} == {}


def test_the_tripwire_catches_an_unmarked_secret():
    class Example(BaseModel):
        api_token: str | None = None
        smtp_password: str | None = credential_field("marked")

    assert _unmarked_secret_fields(Example) == {"api_token"}
