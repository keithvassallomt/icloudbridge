"""Synthetic reconciliation snapshots; no vault or credential access."""

from unittest.mock import AsyncMock

import pytest

from icloudbridge.core.passwords_sync import PasswordsSyncEngine
from icloudbridge.sources.passwords.apple_csv import ApplePasswordsCSVParser
from icloudbridge.sources.passwords.bitwarden_csv import BitwardenCSVParser
from icloudbridge.sources.passwords.models import PasswordEntry
from icloudbridge.sources.passwords.secure_csv import private_csv


def entry(password="synthetic", **kwargs):
    return PasswordEntry(
        title="Example",
        username="fixture",
        password=password,
        url="https://example.invalid",
        **kwargs,
    )


def database(mappings=()):
    db = AsyncMock()
    db.get_all_password_mappings.return_value = list(mappings)
    return db


def mapping(password="synthetic"):
    e = entry(password)
    return {
        "title": e.title,
        "username": e.username,
        "url": e.url,
        "provider_id": "fake-id",
        "last_apple_hash": e.get_password_hash(),
        "last_provider_hash": e.get_password_hash(),
        "last_sync_timestamp": 9999999999,
        "confirmed": True,
    }


def provider(entries=()):
    p = AsyncMock()
    p.list_passwords.return_value = [
        {
            "title": e.title,
            "username": e.username,
            "password": e.password,
            "url": e.url,
            "notes": e.notes,
            "otp_auth": e.otp_auth,
            "id": "fake-id",
        }
        for e in entries
    ]
    return p


@pytest.mark.parametrize(
    ("a", "r", "reason"),
    [
        ("changed-a", "changed-r", "both_changed"),
        ("changed-a", "synthetic", "apple_changed"),
        ("synthetic", "changed-r", "provider_changed"),
    ],
)
async def test_three_way_changes_are_review_only(tmp_path, a, r, reason):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry(a)], csv)
    db, remote = database([mapping()]), provider([entry(r)])
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["conflicts"][0]["reason"] == reason
    remote.create_password.assert_not_awaited()
    remote.update_password.assert_not_awaited()
    remote.delete_password.assert_not_awaited()
    db.upsert_password_mapping.assert_not_awaited()


async def test_dry_run_fresh_database_is_read_only(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry()], csv)
    db, remote = database(), provider()
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote, simulate=True)
    assert result["push"]["planned_created"] == 1
    assert result["push"]["created"] == 0
    assert result["push"]["entries"][0]["action"] == "would_create"
    remote.create_password.assert_not_awaited()
    db.upsert_entry.assert_not_awaited()
    db.upsert_password_mapping.assert_not_awaited()


async def test_addition_does_not_claim_confirmed_sync(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry()], csv)
    db, remote = database(), provider()
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["created"] == 1
    remote.create_password.assert_awaited_once()
    db.upsert_password_mapping.assert_not_awaited()
    db.upsert_entry.assert_not_awaited()


async def test_failed_read_fails_closed(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry()], csv)
    db, remote = database(), provider()
    remote.list_passwords.side_effect = RuntimeError("synthetic read failure")
    with pytest.raises(RuntimeError):
        await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote)
    remote.create_password.assert_not_awaited()
    db.upsert_entry.assert_not_awaited()


@pytest.mark.parametrize("apple_present", [True, False])
async def test_missing_mapped_side_requires_review(tmp_path, apple_present):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry()] if apple_present else [], csv)
    db, remote = database([mapping()]), provider([] if apple_present else [entry()])
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["conflicts"][0]["reason"] == "missing_side_review"
    assert result["pull"]["new_entries"] == 0
    remote.delete_password.assert_not_awaited()
    db.delete_password_mapping.assert_not_awaited()


async def test_equal_changed_pair_establishes_observed_baseline(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry("same-new")], csv)
    db, remote = database([mapping()]), provider([entry("same-new")])
    result = await PasswordsSyncEngine(db).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["conflicts"] == []
    db.confirm_password_snapshot.assert_awaited_once()
    assert "password" not in db.confirm_password_snapshot.call_args.args[0][0]


async def test_export_is_not_confirmed_apple_import(tmp_path):
    csv, output = tmp_path / "apple.csv", tmp_path / "new.csv"
    ApplePasswordsCSVParser.write_file([], csv)
    db, remote = database(), provider([entry()])
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=csv, provider=remote, output_apple_csv=output
    )
    assert result["pull"]["new_entries"] == 1
    assert output.stat().st_mode & 0o777 == 0o600
    db.upsert_password_mapping.assert_not_awaited()


async def test_pull_without_fresh_apple_snapshot_is_held():
    db, remote = database(), provider([entry()])
    result = await PasswordsSyncEngine(db).sync(
        apple_csv_path=None, provider=remote, run_push=False
    )
    assert result["pull"]["new_entries"] == 0
    assert result["pull"]["conflicts"] == [
        {"action": "review", "reason": "fresh_apple_export_required"}
    ]


@pytest.mark.parametrize("kind", ["notes", "otp_auth"])
async def test_metadata_difference_is_reviewed(tmp_path, kind):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry(**{kind: "synthetic-metadata"})], csv)
    remote = provider([entry()])
    result = await PasswordsSyncEngine(database([mapping()])).sync(
        apple_csv_path=csv, provider=remote
    )
    assert result["push"]["conflicts"][0]["reason"] == "metadata_difference"
    remote.update_password.assert_not_awaited()
    assert "synthetic-metadata" not in str(result)


async def test_conflict_without_baseline(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry("a")], csv)
    result = await PasswordsSyncEngine(database()).sync(
        apple_csv_path=csv, provider=provider([entry("b")])
    )
    assert result["push"]["conflicts"][0]["reason"] == "no_baseline"


async def test_duplicate_identity_is_not_silently_chosen(tmp_path):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry("a"), entry("b")], csv)
    remote = provider()
    result = await PasswordsSyncEngine(database()).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["created"] == 0
    assert result["push"]["conflicts"][0]["reason"] == "ambiguous_identity"
    remote.create_password.assert_not_awaited()


@pytest.mark.parametrize("writer", [ApplePasswordsCSVParser, BitwardenCSVParser])
def test_csv_refuses_existing_files_and_symlinks(tmp_path, writer):
    target, link = tmp_path / "target", tmp_path / "link"
    target.write_text("keep")
    link.symlink_to(target)
    for path in (target, link):
        with pytest.raises(FileExistsError):
            writer.write_file([entry()], path)
    assert target.read_text() == "keep"


def test_private_file_permissions_before_write_and_failure_cleanup(tmp_path):
    path = tmp_path / "export.csv"
    with pytest.raises(RuntimeError), private_csv(path) as stream:
        assert path.stat().st_mode & 0o777 == 0o600
        stream.write("synthetic")
        raise RuntimeError("failure")
    assert not path.exists()


@pytest.mark.parametrize("parser", [ApplePasswordsCSVParser, BitwardenCSVParser])
def test_password_whitespace_roundtrip(tmp_path, parser):
    path = tmp_path / "export.csv"
    parser.write_file([entry("  synthetic  ")], path)
    assert parser.parse_file(path)[0].password == "  synthetic  "


async def test_mapped_url_change_is_not_a_new_login(tmp_path):
    csv = tmp_path / "apple.csv"
    renamed = entry()
    renamed.url = "https://changed.invalid"
    ApplePasswordsCSVParser.write_file([renamed], csv)
    remote = provider()
    result = await PasswordsSyncEngine(database([mapping()])).sync(
        apple_csv_path=csv, provider=remote
    )
    assert result["push"]["created"] == 0
    assert "mapped_identity_changed" in {r["reason"] for r in result["push"]["conflicts"]}
    remote.create_password.assert_not_awaited()


async def test_invalid_output_fails_before_provider_mutation(tmp_path):
    csv, output = tmp_path / "apple.csv", tmp_path / "existing.csv"
    ApplePasswordsCSVParser.write_file([entry()], csv)
    output.write_text("preserve")
    other = entry()
    other.title = "Other"
    other.url = "https://other.invalid"
    remote = provider([other])
    with pytest.raises(FileExistsError):
        await PasswordsSyncEngine(database()).sync(
            apple_csv_path=csv, provider=remote, output_apple_csv=output
        )
    remote.create_password.assert_not_awaited()
    assert output.read_text() == "preserve"


async def test_addition_failure_does_not_leak_exception(tmp_path, caplog):
    csv = tmp_path / "apple.csv"
    ApplePasswordsCSVParser.write_file([entry()], csv)
    remote = provider()
    remote.create_password.side_effect = RuntimeError("synthetic-secret-in-error")
    result = await PasswordsSyncEngine(database()).sync(apple_csv_path=csv, provider=remote)
    assert result["push"]["failed"] == 1
    assert result["push"]["created"] == 0
    assert "synthetic-secret-in-error" not in str(result) + caplog.text


async def test_vaultwarden_adapter_does_not_claim_failed_or_skipped_create():
    from icloudbridge.sources.passwords.providers.vaultwarden import VaultwardenProvider

    remote = object.__new__(VaultwardenProvider)
    remote.client = AsyncMock()
    for stats in ({"created": 0, "failed": 1}, {"created": 0, "skipped": 1}):
        remote.client.push_passwords.return_value = stats
        with pytest.raises(RuntimeError, match="not added"):
            await remote.create_password(entry())


async def test_concurrent_existing_login_is_not_duplicated():
    from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient

    client = object.__new__(VaultwardenAPIClient)
    client._ensure_authenticated = lambda: None
    client.pull_passwords = AsyncMock(return_value=[entry("changed-concurrently")])
    client._ensure_user_key = AsyncMock(return_value=b"synthetic")
    client._post_cipher_payload = AsyncMock()
    stats = await client.push_passwords([entry()])
    assert stats["created"] == 0
    assert stats["skipped"] == 1
    client._post_cipher_payload.assert_not_awaited()
