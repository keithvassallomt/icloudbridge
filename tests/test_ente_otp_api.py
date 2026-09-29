"""The Ente Auth preview endpoint on the Passwords API."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from icloudbridge.api.app import app

APPLE_CSV = (
    "Title,URL,Username,Password,Notes,OTPAuth\n"
    "github.com (octocat),https://github.com/,octocat,pw1,,\n"
    "bank.example.com (ada),https://bank.example.com/,ada,pw2,,\n"
)

ENTE_EXPORT = "\n".join(
    [
        "otpauth://totp/GitHub:octocat?secret=BPHY57NROF5SEVY7&issuer=GitHub",
        "otpauth://totp/Bank:someone-else?secret=CKW5UEE2J5IWPYVJ&issuer=Bank&digits=8",
    ]
)


def preview(ente_text: str):
    return TestClient(app).post(
        "/api/passwords/otp/ente/preview",
        files={
            "apple_file": ("Passwords.csv", APPLE_CSV, "text/csv"),
            "ente_file": ("ente.txt", ente_text, "text/plain"),
        },
    )


def test_preview_matches_and_flags():
    response = preview(ENTE_EXPORT)

    assert response.status_code == 200
    by_title = {item["title"]: item for item in response.json()["matched"]}
    assert by_title["github.com (octocat)"]["setup_key"] == "BPHY57NROF5SEVY7"
    assert by_title["github.com (octocat)"]["account_differs"] is False
    assert by_title["github.com (octocat)"]["qr_only"] is False
    assert by_title["bank.example.com (ada)"]["account_differs"] is True
    assert by_title["bank.example.com (ada)"]["qr_only"] is True


def test_encrypted_export_is_refused():
    response = preview(json.dumps({"version": 1, "encryptedData": "cipher"}))

    assert response.status_code == 400
    assert "ente auth decrypt" in response.json()["detail"]
