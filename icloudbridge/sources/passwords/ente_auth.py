"""Parser for Ente Auth plain-text exports.

Ente writes one ``otpauth://`` URI per line, and also accepts commas between
URIs. Each URI may include a ``codeDisplay`` parameter: URL-encoded JSON with
Ente-only metadata (trashed, tags, notes). That parameter is never copied
into the URI this module returns.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

logger = logging.getLogger(__name__)

_URI_SPLIT = re.compile(r"[\r\n,]+")
_SECRET_NOISE = re.compile(r"[\s+]+")
_BASE32_ALPHABET = re.compile(r"[A-Z2-7]+")
_SUPPORTED_ALGORITHMS = {"SHA1", "SHA256", "SHA512"}


@dataclass(frozen=True)
class OtpSecret:
    """One TOTP secret taken from an Ente Auth export."""

    issuer: str
    account: str
    secret: str
    algorithm: str
    digits: int
    period: int
    uri: str


@dataclass(frozen=True)
class SkippedOtp:
    """An Ente line that was not turned into a setup key, and why."""

    label: str
    reason: str


@dataclass(frozen=True)
class EnteParseResult:
    """Secrets that can be matched, plus lines that were left out."""

    secrets: list[OtpSecret]
    skipped: list[SkippedOtp]


def normalize_secret(raw: str) -> str | None:
    """Strip Ente's ``+`` and spaces from a secret and require base32.

    Ente sometimes inserts ``+`` or spaces into a base32 secret. Those
    characters are not part of the key.
    """

    cleaned = _SECRET_NOISE.sub("", raw).upper()
    if not cleaned or not _BASE32_ALPHABET.fullmatch(cleaned):
        return None

    padding = (-len(cleaned)) % 8
    try:
        base64.b32decode(cleaned + ("=" * padding), casefold=True)
    except Exception:
        return None
    return cleaned


def secret_from_otp_value(value: str | None) -> str | None:
    """Read a base32 secret from an otpauth URI or a bare secret."""

    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.lower().startswith("otpauth://"):
        query = parse_qs(urlparse(text).query)
        secrets = query.get("secret") or []
        text = secrets[0] if secrets else ""
    return normalize_secret(text)


class EnteAuthParser:
    """Parse an Ente Auth plain-text export into TOTP secrets."""

    @staticmethod
    def parse_text(text: str) -> EnteParseResult:
        """Parse ``otpauth://`` lines from an Ente plain-text export.

        Raises:
            ValueError: The text is an encrypted Ente export, which this
                parser does not decrypt.
        """

        stripped = text.lstrip("\ufeff").lstrip()
        if stripped.startswith("{") and "encryptedData" in stripped[:800]:
            raise ValueError(
                "This is an encrypted Ente Auth export. Decrypt it with "
                "`ente auth decrypt <export_file> <output_file>` and upload "
                "the plain-text file."
            )

        secrets: list[OtpSecret] = []
        skipped: list[SkippedOtp] = []
        seen: set[tuple[str, str, str]] = set()

        for chunk in _URI_SPLIT.split(text):
            uri = chunk.strip()
            if not uri:
                continue
            if not uri.lower().startswith("otpauth://"):
                skipped.append(SkippedOtp(label=uri[:80], reason="not an otpauth URI"))
                continue

            secret, skip = _parse_uri(uri)
            if skip is not None:
                skipped.append(skip)
                continue
            assert secret is not None
            key = (secret.issuer.lower(), secret.account.lower(), secret.secret)
            if key in seen:
                continue
            seen.add(key)
            secrets.append(secret)

        logger.info(
            "Parsed Ente Auth export: %s secrets, %s skipped",
            len(secrets),
            len(skipped),
        )
        return EnteParseResult(secrets=secrets, skipped=skipped)


def _parse_uri(uri: str) -> tuple[OtpSecret | None, SkippedOtp | None]:
    parsed = urlparse(uri)
    otp_type = (parsed.netloc or "").lower()
    label = unquote(parsed.path.lstrip("/"))
    query = parse_qs(parsed.query, keep_blank_values=True)

    issuer = (query.get("issuer") or [""])[0].strip()
    account = ""
    if ":" in label:
        label_issuer, account = label.split(":", 1)
        label_issuer = label_issuer.strip()
        account = account.strip()
        if not issuer:
            issuer = label_issuer
    else:
        account = label.strip()
        if not issuer:
            issuer = label.strip()
            account = ""

    display_label = issuer or account or label or "unknown code"
    code_display = _code_display(query.get("codeDisplay", [""])[0])
    if code_display.get("trashed") is True:
        return None, SkippedOtp(label=display_label, reason="trashed in Ente Auth")

    if otp_type == "hotp":
        return None, SkippedOtp(
            label=display_label,
            reason="HOTP is not supported by Apple Passwords",
        )
    if otp_type == "steam" or issuer.lower() == "steam":
        return None, SkippedOtp(
            label=display_label,
            reason="Steam codes are not supported by Apple Passwords",
        )
    if otp_type != "totp":
        return None, SkippedOtp(
            label=display_label,
            reason=f"unsupported type '{otp_type or 'unknown'}'",
        )

    raw_secret = (query.get("secret") or [""])[0]
    secret = normalize_secret(raw_secret)
    if not secret:
        return None, SkippedOtp(label=display_label, reason="missing or invalid base32 secret")
    if not issuer:
        return None, SkippedOtp(label=display_label, reason="missing issuer")

    algorithm = ((query.get("algorithm") or ["SHA1"])[0] or "SHA1").upper()
    if algorithm not in _SUPPORTED_ALGORITHMS:
        algorithm = "SHA1"
    digits = _positive_int((query.get("digits") or ["6"])[0], default=6)
    period = _positive_int((query.get("period") or ["30"])[0], default=30)

    return (
        OtpSecret(
            issuer=issuer,
            account=account,
            secret=secret,
            algorithm=algorithm,
            digits=digits,
            period=period,
            uri=_clean_uri(issuer, account, secret, algorithm, digits, period),
        ),
        None,
    )


def _code_display(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_int(raw: str, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _clean_uri(
    issuer: str,
    account: str,
    secret: str,
    algorithm: str,
    digits: int,
    period: int,
) -> str:
    """Build an otpauth URI without Ente's codeDisplay metadata."""

    label = f"{issuer}:{account}" if account else issuer
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": algorithm,
            "digits": str(digits),
            "period": str(period),
        }
    )
    return f"otpauth://totp/{quote(label, safe='')}?{query}"
