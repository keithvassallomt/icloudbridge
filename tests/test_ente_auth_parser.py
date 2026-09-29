"""Ente Auth plain-text export parsing."""

import json
from urllib.parse import parse_qs, quote, urlparse

import pytest

from icloudbridge.sources.passwords.ente_auth import EnteAuthParser, normalize_secret


def _uri(
    issuer: str,
    account: str,
    secret: str,
    *,
    otp_type: str = "totp",
    algorithm: str = "SHA1",
    digits: int = 6,
    period: int = 30,
    code_display: dict | None = None,
    extra: str = "",
) -> str:
    label = quote(f"{issuer}:{account}" if account else issuer, safe="")
    query = (
        f"algorithm={algorithm}&digits={digits}&issuer={quote(issuer)}"
        f"&period={period}&secret={secret}"
    )
    if code_display is not None:
        query += "&codeDisplay=" + quote(json.dumps(code_display), safe="")
    return f"otpauth://{otp_type}/{label}?{query}{extra}"


def test_newline_and_comma_separated_uris():
    first = _uri("GitHub", "octocat", "BPHY57NROF5SEVY7")
    second = _uri("PayPal", "ada@example.com", "CKW5UEE2J5IWPYVJ")
    text = (
        "\n".join([first, second])
        + ","
        + _uri(
            "Stripe",
            "ada@example.com",
            "XLND7E3WNYH7BADTCUPQASZX",
        )
    )

    result = EnteAuthParser.parse_text(text)

    assert [(item.issuer, item.account) for item in result.secrets] == [
        ("GitHub", "octocat"),
        ("PayPal", "ada@example.com"),
        ("Stripe", "ada@example.com"),
    ]
    assert result.skipped == []


def test_plus_and_spaces_are_removed_from_secrets():
    uri = _uri("Example", "ada@example.com", "BPHY+57NR OF5S EVY7")
    result = EnteAuthParser.parse_text(uri)

    assert len(result.secrets) == 1
    assert result.secrets[0].secret == "BPHY57NROF5SEVY7"
    assert normalize_secret("bphy 57nro+f5sevy7") == "BPHY57NROF5SEVY7"


def test_trashed_entries_are_skipped():
    live = _uri("Hytale", "ada@example.com", "UXAQTR6KICGVWPUMYNXAQ4YHUIYWZAFD")
    trashed = _uri(
        "Hytale",
        "ada@example.com",
        "UXAQTR6KICGVWPUMYNXAQ4YHUIYWZAFD",
        code_display={"trashed": True, "note": "old"},
    )

    result = EnteAuthParser.parse_text(live + "\n" + trashed)

    assert len(result.secrets) == 1
    assert result.skipped[0].label == "Hytale"
    assert result.skipped[0].reason == "trashed in Ente Auth"


def test_hotp_and_steam_are_skipped():
    text = "\n".join(
        [
            _uri("Bank", "ada", "BPHY57NROF5SEVY7", otp_type="hotp"),
            _uri("Steam", "ada", "BPHY57NROF5SEVY7", otp_type="steam"),
            "otpauth://totp/Steam:ada?secret=BPHY57NROF5SEVY7&issuer=Steam",
            _uri("GitHub", "ada", "BPHY57NROF5SEVY7"),
        ]
    )

    result = EnteAuthParser.parse_text(text)

    assert [item.issuer for item in result.secrets] == ["GitHub"]
    assert [item.reason for item in result.skipped] == [
        "HOTP is not supported by Apple Passwords",
        "Steam codes are not supported by Apple Passwords",
        "Steam codes are not supported by Apple Passwords",
    ]


def test_code_display_is_stripped_from_the_rebuilt_uri():
    uri = _uri(
        "Facebook",
        "Ada Lovelace",
        "AOTVPRIUT7P37KESRPQ5LRDQN24FQ7HX",
        code_display={"trashed": False, "note": "personal", "tags": ["social"]},
    )

    result = EnteAuthParser.parse_text(uri)

    rebuilt = result.secrets[0].uri
    assert "codeDisplay" not in rebuilt
    assert "personal" not in rebuilt
    query = parse_qs(urlparse(rebuilt).query)
    assert query["secret"] == ["AOTVPRIUT7P37KESRPQ5LRDQN24FQ7HX"]
    assert query["issuer"] == ["Facebook"]
    assert query["algorithm"] == ["SHA1"]
    assert query["digits"] == ["6"]
    assert query["period"] == ["30"]


def test_lowercase_algorithm_and_secret_are_normalised():
    uri = (
        "otpauth://totp/Discord:ada@example.com?algorithm=sha1&digits=6"
        "&issuer=Discord&period=30&secret=sa3i5efh5x76gwim2gb7pbz2gt3xrqlb"
    )

    result = EnteAuthParser.parse_text(uri)

    assert result.secrets[0].secret == "SA3I5EFH5X76GWIM2GB7PBZ2GT3XRQLB"
    assert result.secrets[0].algorithm == "SHA1"
    assert result.secrets[0].account == "ada@example.com"


def test_encoded_colon_in_the_label():
    uri = (
        "otpauth://totp/Rockstar+Games%3Aada%40example.com?secret=SBAYRKBS3FZKHEUWM4I4PXRVDQ"
        "&issuer=Rockstar+Games&algorithm=SHA1&digits=6&period=30"
    )

    result = EnteAuthParser.parse_text(uri)

    assert result.secrets[0].issuer == "Rockstar Games"
    assert result.secrets[0].account == "ada@example.com"


def test_invalid_secret_is_skipped():
    uri = "otpauth://totp/Example:ada?secret=not!valid&issuer=Example"

    result = EnteAuthParser.parse_text(uri)

    assert result.secrets == []
    assert result.skipped[0].reason == "missing or invalid base32 secret"


def test_duplicate_lines_collapse():
    uri = _uri("GitHub", "octocat", "BPHY57NROF5SEVY7")

    result = EnteAuthParser.parse_text(uri + "\n" + uri)

    assert len(result.secrets) == 1


def test_encrypted_export_is_rejected():
    payload = json.dumps(
        {
            "version": 1,
            "kdfParams": {"memLimit": 4096, "opsLimit": 3, "salt": "example"},
            "encryptedData": "cipher",
            "encryptionNonce": "nonce",
        }
    )

    with pytest.raises(ValueError, match="ente auth decrypt"):
        EnteAuthParser.parse_text(payload)
