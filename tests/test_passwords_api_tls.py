"""Password API preserves configured certificate verification for Nextcloud."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="API imports native macOS adapters")
async def test_nextcloud_api_preserves_custom_ca(monkeypatch):
    from icloudbridge.api.routes import passwords

    config = SimpleNamespace(
        passwords=SimpleNamespace(
            provider="nextcloud",
            nextcloud_username="fixture",
            nextcloud_url="https://cloud.invalid",
            passwords_ssl_verify_cert="/synthetic/test-ca.pem",
        )
    )
    store = Mock()
    store.get_nextcloud_credentials.return_value = {"app_password": "synthetic"}
    monkeypatch.setattr(passwords, "CredentialStore", lambda: store)
    provider = AsyncMock()
    factory = Mock(return_value=provider)
    monkeypatch.setattr(passwords, "NextcloudPasswordsProvider", factory)
    assert await passwords._build_password_provider(config) is provider
    factory.assert_called_once_with(
        "https://cloud.invalid", "fixture", "synthetic", ssl_verify_cert="/synthetic/test-ca.pem"
    )
    provider.authenticate.assert_awaited_once()
