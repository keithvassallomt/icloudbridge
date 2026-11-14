"""Review regressions using disposable databases and HTTP response fixtures."""

import json
import logging
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest

from icloudbridge.core.passwords_sync import PasswordsSyncEngine
from icloudbridge.sources.passwords.apple_csv import ApplePasswordsCSVParser
from icloudbridge.sources.passwords.diagnostics import log_failure, safe_failure
from icloudbridge.sources.passwords.models import PasswordEntry
from icloudbridge.sources.passwords.providers.nextcloud import NextcloudPasswordsProvider
from icloudbridge.sources.passwords.providers.vaultwarden import VaultwardenProvider
from icloudbridge.sources.passwords.retry import retry_addition
from icloudbridge.utils.db import PasswordsDB


def e(title="Example", username="", url=None, **kw):
    return PasswordEntry(title=title, username=username, url=url, password="fixture-only", **kw)


def remote(entries):
    p = AsyncMock()
    p.list_passwords.return_value = [
        {
            "title": x.title,
            "username": x.username,
            "password": x.password,
            "url": x.url,
            "notes": x.notes,
            "otp_auth": x.otp_auth,
            "extra_urls": x.extra_urls,
            "id": str(i),
        }
        for i, x in enumerate(entries)
    ]
    return p


async def test_three_repeated_syncs_keep_blank_username_and_url(tmp_path):
    entries = [
        e(),
        e("Google", "fixture", "https://accounts.google.invalid"),
        e("YouTube", "fixture", "https://accounts.google.invalid"),
    ]
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file(entries, path)
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    for _ in range(3):
        result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote(entries))
        assert result["push"]["conflicts"] == []
        assert result["pull"]["new_entries"] == 0
    mappings = await db.get_all_password_mappings()
    assert len(mappings) == 3 and all(m["confirmed"] for m in mappings)
    assert (await db.get_last_sync("apple_import"))["entry_count"] == 3


async def test_legacy_null_duplicates_unconfirmed_until_seen_both(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("ALTER TABLE password_mapping DROP COLUMN confirmed")
        for _ in range(2):
            conn.execute(
                "INSERT INTO password_mapping(title,url,username,provider_type,last_sync_timestamp,created_at) VALUES ('Example',NULL,'','asyncmock',1,1)"
            )
    await db.initialize()
    mappings = await db.get_all_password_mappings()
    assert len(mappings) == 1 and mappings[0]["confirmed"] == 0 and mappings[0]["url"] == ""
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([], path)
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=path,
        provider=remote([e(username="fixture", url="https://example.invalid")]),
        output_apple_csv=tmp_path / "export.csv",
    )
    assert result["pull"]["new_entries"] == 1 and result["pull"]["conflicts"] == []


async def test_converged_rename_retires_old_tracking(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    x = e("Old")
    await db.upsert_password_mapping(
        title=x.title,
        username=x.username,
        url=x.url,
        provider_id="0",
        provider_type="asyncmock",
        last_apple_hash=x.get_password_hash(),
        last_provider_hash=x.get_password_hash(),
    )
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([e("New")], path)
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([e("New")]))
    assert result["push"]["conflicts"] == []
    assert [m["title"] for m in await db.get_all_password_mappings()] == ["New"]


def test_notes_and_otp_roundtrip(tmp_path):
    x = e(notes=" leading note\n", otp_auth="otpauth://totp/fixture?secret=ABC&issuer=test")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([x], path)
    actual = ApplePasswordsCSVParser.parse_file(path)[0]
    assert actual.notes == x.notes
    assert PasswordsSyncEngine._same_otp(x.otp_auth, "ABC")
    assert not PasswordsSyncEngine._same_otp(x.otp_auth, x.otp_auth + "&digits=8")


def test_bare_otp_secret_matches_any_parameters():
    # Older Nextcloud entries kept only the secret, dropping digits/period.
    eight = "otpauth://totp/fixture?secret=ABC&digits=8&period=60"
    assert PasswordsSyncEngine._same_otp(eight, "abc")
    assert not PasswordsSyncEngine._same_otp(eight, "XYZ")
    assert not PasswordsSyncEngine._same_otp(eight, None)


def test_notes_compare_ignoring_line_endings_and_edge_whitespace():
    apple = e(notes="first line\r\nsecond line\n")
    provider = e(notes="first line\nsecond line")
    assert PasswordsSyncEngine._same(apple, provider)
    assert not PasswordsSyncEngine._same(apple, e(notes="first line"))


async def test_provider_login_sharing_site_with_apple_login_is_not_exported(tmp_path):
    # Apple keeps one login per website and username; importing a second overwrites the first.
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    google = e("Google", "fixture", "https://accounts.google.invalid/")
    youtube = e("YouTube", "fixture", "https://accounts.google.invalid")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([google], path)
    for i in range(2):
        result = await PasswordsSyncEngine(db).sync(
            apple_csv_path=path, provider=remote([google, youtube]), output_apple_csv=tmp_path / f"out{i}.csv"
        )
        assert result["pull"]["planned_new_entries"] == 0
        assert [(r["title"], r["reason"]) for r in result["pull"]["conflicts"]] == [
            ("YouTube", "apple_one_login_per_site")
        ]
    assert [m["title"] for m in await db.get_all_password_mappings()] == ["Google"]


async def test_two_new_provider_logins_for_one_site_are_not_exported(tmp_path):
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([], path)
    db = AsyncMock()
    db.get_all_password_mappings.return_value = []
    pair = [e("Pair A", "fixture", "https://example.invalid/"), e("Pair B", "fixture", "https://example.invalid/"),
            e("Other", "fixture", "https://other.invalid/")]
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=path, provider=remote(pair), output_apple_csv=tmp_path / "export.csv"
    )
    assert [x.title for x in ApplePasswordsCSVParser.parse_file(tmp_path / "export.csv")] == ["Other"]
    assert sorted(r["title"] for r in result["pull"]["conflicts"]) == ["Pair A", "Pair B"]


async def test_rename_on_one_side_is_still_held(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    old = e("Old", "fixture", "https://example.invalid/")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([old], path)
    await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([old]))
    path = tmp_path / "apple-2.csv"
    ApplePasswordsCSVParser.write_file([e("New", "fixture", "https://example.invalid/")], path)
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([old]))
    assert result["push"]["planned_created"] == 0
    assert result["pull"]["planned_new_entries"] == 0
    assert {r["reason"] for r in result["push"]["conflicts"]} >= {"possible_renamed_identity"}


def test_user_facing_errors_keep_their_message():
    from icloudbridge.sources.passwords.diagnostics import InvalidInputError, ProviderAuthError

    assert safe_failure(ProviderAuthError("VaultWarden rejected the login (HTTP 400): Username or password is incorrect.")).endswith("incorrect.")
    assert "Apple Passwords export" in safe_failure(InvalidInputError("This isn't an Apple Passwords export."))
    # Messages we did not write are still replaced.
    assert "hunter2" not in safe_failure(ValueError("hunter2"))
    assert "hunter2" not in safe_failure(RuntimeError("hunter2"))


def test_vaultwarden_auth_failure_message_uses_server_reason():
    from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient

    body = {"error": "invalid_grant", "error_description": "invalid_username_or_password",
            "ErrorModel": {"Message": "Username or password is incorrect. Try again"}}
    response = httpx.Response(400, json=body)
    message = VaultwardenAPIClient._auth_failure_message(response)
    assert message == "VaultWarden rejected the login (HTTP 400): Username or password is incorrect. Try again"
    assert VaultwardenAPIClient._auth_failure_message(httpx.Response(502, text="<html>")) == "VaultWarden rejected the login (HTTP 502)."


@pytest.mark.parametrize("encoded", [False, True])
async def test_nextcloud_returns_custom_fields(encoded):
    fields = [
        {"label": "Website 2", "value": "https://second.invalid", "type": "url"},
        {"label": "TOTP", "value": "ABC", "type": "secret"},
    ]
    p = NextcloudPasswordsProvider("https://fixture.invalid", "fixture", "throwaway")
    await p._client.aclose()
    p._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=[{"id": "x", "customFields": json.dumps(fields) if encoded else fields}]
            )
        )
    )
    rows = await p.list_passwords()
    assert rows[0]["otp_auth"] == "ABC" and rows[0]["extra_urls"] == ["https://second.invalid"]
    await p.close()


async def test_vaultwarden_batch_has_one_shared_push():
    p = VaultwardenProvider.__new__(VaultwardenProvider)
    p.client = AsyncMock()
    p.client.list_folders.return_value = []
    p.client.push_passwords.return_value = {"created": 200, "failed": 0}
    rows = [e(str(i)) for i in range(200)]
    result = await p.create_passwords(rows)
    assert result["created"] == 200
    p.client.push_passwords.assert_awaited_once()
    assert len(p.client.push_passwords.call_args.kwargs["entries"]) == 200


async def test_retry_rechecks_before_replaying(monkeypatch):
    monkeypatch.setattr("icloudbridge.sources.passwords.retry.asyncio.sleep", AsyncMock())
    request = httpx.Request("POST", "https://fixture.invalid")
    create = AsyncMock(side_effect=[httpx.ReadTimeout("secret", request=request), None])
    exists = AsyncMock(return_value=True)
    assert not await retry_addition(create, exists)
    create.assert_awaited_once()
    exists.assert_awaited_once()


async def test_retry_429_then_success(monkeypatch):
    monkeypatch.setattr("icloudbridge.sources.passwords.retry.asyncio.sleep", AsyncMock())
    response = httpx.Response(429, request=httpx.Request("POST", "https://fixture.invalid"))
    create = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError("secret", request=response.request, response=response),
            None,
        ]
    )
    assert await retry_addition(create, AsyncMock(return_value=False))
    assert create.await_count == 2


def test_diagnostics_do_not_log_exception_secret(caplog):
    try:
        raise FileExistsError("sensitive-fixture-password")
    except Exception as exc:
        with caplog.at_level(logging.ERROR):
            log_failure(logging.getLogger("test"), exc)
        assert "choose a new" in safe_failure(exc)
    assert "sensitive-fixture-password" not in caplog.text
    assert "test_diagnostics_do_not_log_exception_secret" in caplog.text


async def test_login_without_password_is_added(tmp_path):
    # Apple Passwords imports and exports logins with an empty password.
    x = PasswordEntry(title="No password fixture", username="fixture", password="", url="https://example.invalid/")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([x], path)
    db = AsyncMock()
    db.get_all_password_mappings.return_value = []
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([]), simulate=True)
    assert result["push"]["planned_created"] == 1
    assert result["push"]["conflicts"] == []
    db.confirm_password_snapshot.assert_not_awaited()


async def test_both_deleted_only_removes_tracking(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    x = e()
    await db.upsert_password_mapping(
        title=x.title,
        username=x.username,
        url=x.url,
        provider_id="0",
        provider_type="asyncmock",
        last_apple_hash=x.get_password_hash(),
        last_provider_hash=x.get_password_hash(),
    )
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([], path)
    p = remote([])
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=p)
    assert not await db.get_all_password_mappings()
    assert not result["push"]["conflicts"]
    p.delete_password.assert_not_awaited()


async def test_failed_retry_read_never_replays_post(monkeypatch):
    monkeypatch.setattr("icloudbridge.sources.passwords.retry.asyncio.sleep", AsyncMock())
    create = AsyncMock(side_effect=httpx.ReadTimeout("fixture"))
    with pytest.raises(RuntimeError):
        await retry_addition(create, AsyncMock(side_effect=RuntimeError("read failed")))
    create.assert_awaited_once()


async def test_one_sided_old_identity_stays_held_after_new_pair_matches(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    x = e("Old")
    await db.upsert_password_mapping(
        title=x.title,
        username=x.username,
        url=x.url,
        provider_id="0",
        provider_type="asyncmock",
        last_apple_hash=x.get_password_hash(),
        last_provider_hash=x.get_password_hash(),
    )
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([x, e("New")], path)
    p = remote([e("New")])
    for _ in range(2):
        result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=p)
        assert result["push"]["created"] == 0
        assert result["push"]["conflicts"][0]["reason"] == "missing_side_review"
    p.create_password.assert_not_awaited()
    assert len(await db.get_all_password_mappings()) == 2


async def test_current_vaultwarden_profile_ownership_reaches_cipher_payload():
    from icloudbridge.sources.passwords.bitwarden_crypto import encrypt_string
    from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient

    master = b"0" * 32
    user_key = "A" * 64
    client = VaultwardenAPIClient("https://fixture.invalid", "fixture@example.invalid", "throwaway")
    await client._client.aclose()
    client.access_token = "fixture-token"
    client._master_key = master
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "id": "fixture-user",
                    "userKeyId": "fixture-key-id",
                    "key": encrypt_string(user_key, master),
                },
            )
        )
    )
    key = await client._ensure_user_key()
    assert key == user_key.encode()
    payload = client._build_cipher_payload(e(), None, key, include_folder_id=True)
    assert payload["encryptedFor"] == "fixture-user"
    assert payload["encryptedByKeyId"] == "fixture-key-id"
    await client.close()


async def test_safari_root_url_canonicalization_confirms_existing_pair(tmp_path):
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    a = e(username="fixture", url="https://example.invalid/")
    r = e(username="fixture", url="https://example.invalid")
    await db.upsert_password_mapping(
        title=r.title,
        username=r.username,
        url=r.url,
        provider_id="0",
        provider_type="asyncmock",
        last_apple_hash=r.get_password_hash(),
        last_provider_hash=r.get_password_hash(),
    )
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([a], path)
    for _ in range(3):
        result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([r]))
        assert not result["push"]["conflicts"]
    mappings = await db.get_all_password_mappings()
    assert len(mappings) == 1 and mappings[0]["url"] == a.url
    # Apple keeps only the host, so pages on one site share a key.
    assert (
        e(url="https://example.invalid/login").get_dedup_key()
        == e(url="https://EXAMPLE.invalid/").get_dedup_key()
    )
    assert (
        e(url="https://a.example.invalid/").get_dedup_key()
        != e(url="https://b.example.invalid/").get_dedup_key()
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (" HTTPS://EXAMPLE.INVALID ", "https://example.invalid/"),
        # Apple Passwords exports https://host/ for every web login (path, query,
        # fragment and user info dropped), as seen in real exports.
        ("https://EXAMPLE.INVALID/Path?Key=Value#Fragment", "https://example.invalid/"),
        ("https://User:Secret@EXAMPLE.INVALID/login", "https://example.invalid/"),
        ("http://example.invalid/login", "https://example.invalid/"),
        ("https://example.invalid:8443/x", "https://example.invalid:8443/"),
        ("example.invalid/login", "https://example.invalid/"),
        ("androidapp://Example/Path", "androidapp://Example/Path"),
        ("", None),
        (None, None),
    ],
)
def test_url_normalization_matches_what_apple_keeps(url, expected):
    assert PasswordEntry.normalize_url(url) == expected


async def test_provider_login_with_path_matches_its_apple_copy(tmp_path):
    # Exporting https://example.invalid/login to Apple comes back as https://example.invalid/.
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    provider_entry = e("Example", "fixture", "https://example.invalid/login?next=%2F")
    apple_copy = e("Example", "fixture", "https://example.invalid/")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([apple_copy], path)
    for _ in range(2):
        result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([provider_entry]))
        assert result["push"]["conflicts"] == []
        assert result["push"]["planned_created"] == 0 and result["pull"]["planned_new_entries"] == 0


async def test_apple_site_check_is_case_sensitive_on_username(tmp_path):
    # Apple keeps "probecase" and "ProbeCase" on one host as separate logins.
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([], path)
    db = AsyncMock()
    db.get_all_password_mappings.return_value = []
    pair = [e("Lower", "probecase", "https://example.invalid/"), e("Upper", "ProbeCase", "https://example.invalid/")]
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=path, provider=remote(pair), output_apple_csv=tmp_path / "export.csv", simulate=True
    )
    assert result["pull"]["planned_new_entries"] == 2
    assert result["pull"]["conflicts"] == []


@pytest.mark.parametrize("simulate", [True, False])
async def test_first_sync_holds_possible_renamed_login_without_baseline(tmp_path, simulate):
    apple = e("Apple label", "fixture", "https://example.invalid/")
    provider_entry = e("Provider label", "fixture", "https://example.invalid")
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([apple], path)
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    provider = remote([provider_entry])
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=path,
        provider=provider,
        simulate=simulate,
        output_apple_csv=tmp_path / "export.csv",
    )
    assert result["push"]["planned_created"] == result["push"]["created"] == 0
    assert result["pull"]["planned_new_entries"] == result["pull"]["new_entries"] == 0
    assert {r["reason"] for r in result["pull"]["conflicts"]} == {"possible_renamed_identity"}
    provider.create_password.assert_not_called()
    assert not (tmp_path / "export.csv").exists()


@pytest.mark.parametrize("row", [e(url="https://example.invalid/"), e(username="fixture")])
async def test_logins_without_username_or_url_are_exported(tmp_path, row):
    # Apple Passwords imports rows with no username or no URL and exports them back.
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([], path)
    db = AsyncMock()
    db.get_all_password_mappings.return_value = []
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=path, provider=remote([row]), output_apple_csv=tmp_path / "export.csv"
    )
    assert result["pull"]["new_entries"] == 1
    assert result["pull"]["conflicts"] == []
    assert ApplePasswordsCSVParser.parse_file(tmp_path / "export.csv")[0].title == row.title


async def test_multi_url_login_matches_whatever_order_apple_exports(tmp_path):
    # Exported as multi-1, multi-2; Apple exported it back as multi-2, multi-1.
    provider_entry = e("Multi", "fixture", "https://multi-1.invalid/", extra_urls=["https://multi-2.invalid/"])
    apple_copy = e("Multi", "fixture", "https://multi-2.invalid/", extra_urls=["https://multi-1.invalid/"])
    assert provider_entry.get_dedup_key() == apple_copy.get_dedup_key()
    db = PasswordsDB(tmp_path / "db.sqlite")
    await db.initialize()
    path = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([apple_copy], path)
    for _ in range(2):
        result = await PasswordsSyncEngine(db).sync(apple_csv_path=path, provider=remote([provider_entry]))
        assert result["push"]["conflicts"] == []
    assert len(await db.get_all_password_mappings()) == 1
