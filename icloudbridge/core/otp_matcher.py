"""Match Ente Auth TOTP secrets onto existing Apple Passwords logins.

Lookups are built once, so matching a batch is linear in the number of
secrets plus the number of logins. A secret is never applied when more than
one login fits, and an existing verification code is never replaced.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

from icloudbridge.sources.passwords.ente_auth import (
    EnteParseResult,
    OtpSecret,
    secret_from_otp_value,
)
from icloudbridge.sources.passwords.models import PasswordEntry

# Second-level suffixes where the registrable name is three labels deep.
_MULTI_PART_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.jp",
    "co.nz",
    "com.br",
    "com.mx",
    "co.za",
}

_COMPACT = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class LoginRef:
    """The Apple login fields the preview is allowed to show."""

    title: str
    username: str


@dataclass(frozen=True)
class MatchedOtp:
    """A secret that belongs on exactly one Apple login."""

    issuer: str
    account: str
    setup_key: str
    otpauth_uri: str
    login: LoginRef


@dataclass(frozen=True)
class AmbiguousOtp:
    """A secret that fits more than one Apple login."""

    issuer: str
    account: str
    candidates: list[LoginRef]


@dataclass(frozen=True)
class UnmatchedOtp:
    """A secret with no Apple login."""

    issuer: str
    account: str


@dataclass(frozen=True)
class ExistingOtp:
    """A login that already has this secret, or a different one."""

    issuer: str
    account: str
    login: LoginRef


@dataclass
class OtpMatchResult:
    """Grouped match results. ``conflict`` entries must not be overwritten."""

    matched: list[MatchedOtp] = field(default_factory=list)
    ambiguous: list[AmbiguousOtp] = field(default_factory=list)
    unmatched: list[UnmatchedOtp] = field(default_factory=list)
    already_set: list[ExistingOtp] = field(default_factory=list)
    conflict: list[ExistingOtp] = field(default_factory=list)


def registrable_domain(hostname: str) -> str | None:
    """Return the registrable domain for a hostname, without a public-suffix list.

    ``accounts.google.com`` becomes ``google.com``. ``www.example.co.uk``
    becomes ``example.co.uk``.
    """

    host = hostname.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or " " in host:
        return None
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return None
    last_two = ".".join(parts[-2:])
    if last_two in _MULTI_PART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def domain_from_url(url: str | None) -> str | None:
    """Registrable domain from a URL or a bare hostname."""

    if not url:
        return None
    text = url.strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text}"
    host = urlparse(text).hostname
    if not host:
        return None
    return registrable_domain(host)


def compact_name(value: str) -> str:
    """Lowercase letters and digits only, for issuer and title comparison."""

    return _COMPACT.sub("", value.lower())


def build_preview(parsed: EnteParseResult, apple_entries: list[PasswordEntry]) -> dict:
    """JSON-ready preview. Setup keys are included only for logins still missing one."""

    result = match(parsed.secrets, apple_entries)
    return {
        "matched": [
            {
                "issuer": item.issuer,
                "account": item.account,
                "title": item.login.title,
                "username": item.login.username,
                "setup_key": item.setup_key,
                "otpauth_uri": item.otpauth_uri,
            }
            for item in result.matched
        ],
        "ambiguous": [
            {
                "issuer": item.issuer,
                "account": item.account,
                "candidates": [
                    {"title": candidate.title, "username": candidate.username}
                    for candidate in item.candidates
                ],
            }
            for item in result.ambiguous
        ],
        "unmatched": [
            {"issuer": item.issuer, "account": item.account} for item in result.unmatched
        ],
        "already_set": [
            {
                "issuer": item.issuer,
                "account": item.account,
                "title": item.login.title,
                "username": item.login.username,
            }
            for item in result.already_set
        ],
        "conflict": [
            {
                "issuer": item.issuer,
                "account": item.account,
                "title": item.login.title,
                "username": item.login.username,
            }
            for item in result.conflict
        ],
        "skipped": [{"label": item.label, "reason": item.reason} for item in parsed.skipped],
    }


def match(secrets: list[OtpSecret], apple_entries: list[PasswordEntry]) -> OtpMatchResult:
    """Match Ente secrets onto Apple logins.

    Tiers, in order. The first tier that finds any login wins, so a broader
    tier cannot override a closer one:

    1. Registrable domain plus username.
    2. Issuer against the domain label or the title, plus username.
    3. Issuer alone, when exactly one login fits.
    """

    index = _LoginIndex(apple_entries)
    result = OtpMatchResult()
    pending: list[tuple[OtpSecret, PasswordEntry]] = []

    for secret in secrets:
        status, entries = index.lookup(secret)
        if status == "none":
            result.unmatched.append(UnmatchedOtp(issuer=secret.issuer, account=secret.account))
            continue
        if status == "many":
            result.ambiguous.append(
                AmbiguousOtp(
                    issuer=secret.issuer,
                    account=secret.account,
                    candidates=[_login_ref(entry) for entry in entries],
                )
            )
            continue
        pending.append((secret, entries[0]))

    _classify_unique_matches(pending, result)
    return result


def _classify_unique_matches(
    pending: list[tuple[OtpSecret, PasswordEntry]],
    result: OtpMatchResult,
) -> None:
    """Turn unique login hits into matched, already-set, or conflict rows.

    Two Ente secrets that land on the same login stay matched only when they
    are the same key. Different keys are a conflict and are not written.
    """

    grouped: dict[int, list[OtpSecret]] = defaultdict(list)
    entries_by_id: dict[int, PasswordEntry] = {}
    for secret, entry in pending:
        grouped[id(entry)].append(secret)
        entries_by_id[id(entry)] = entry

    for entry_id, secrets in grouped.items():
        entry = entries_by_id[entry_id]
        unique_secrets = _unique_secrets(secrets)
        existing = secret_from_otp_value(entry.otp_auth)
        if len(unique_secrets) > 1:
            for secret in unique_secrets:
                result.conflict.append(_existing(secret, entry))
            continue

        secret = unique_secrets[0]
        if existing and existing == secret.secret:
            result.already_set.append(_existing(secret, entry))
        elif existing:
            result.conflict.append(_existing(secret, entry))
        else:
            result.matched.append(
                MatchedOtp(
                    issuer=secret.issuer,
                    account=secret.account,
                    setup_key=secret.secret,
                    otpauth_uri=secret.uri,
                    login=_login_ref(entry),
                )
            )


def _unique_secrets(secrets: list[OtpSecret]) -> list[OtpSecret]:
    seen: set[str] = set()
    unique: list[OtpSecret] = []
    for secret in secrets:
        if secret.secret in seen:
            continue
        seen.add(secret.secret)
        unique.append(secret)
    return unique


def _login_ref(entry: PasswordEntry) -> LoginRef:
    return LoginRef(title=entry.title, username=entry.username)


def _existing(secret: OtpSecret, entry: PasswordEntry) -> ExistingOtp:
    return ExistingOtp(issuer=secret.issuer, account=secret.account, login=_login_ref(entry))


class _LoginIndex:
    """Domain and name lookups over Apple logins."""

    def __init__(self, entries: list[PasswordEntry]) -> None:
        self._by_domain_user: dict[tuple[str, str], list[PasswordEntry]] = defaultdict(list)
        self._by_name_user: dict[tuple[str, str], list[PasswordEntry]] = defaultdict(list)
        self._by_name: dict[str, list[PasswordEntry]] = defaultdict(list)
        for entry in entries:
            self._add(entry)

    def lookup(self, secret: OtpSecret) -> tuple[str, list[PasswordEntry]]:
        username = secret.account.lower().strip()
        issuer_key = compact_name(secret.issuer)

        if username:
            by_domain = self._collect_domain(secret, username)
            if by_domain:
                return _status(by_domain), by_domain

            by_name = self._dedupe(self._by_name_user.get((issuer_key, username), []))
            if by_name:
                return _status(by_name), by_name

        if len(issuer_key) < 3:
            return "none", []
        by_issuer = self._dedupe(self._by_name.get(issuer_key, []))
        if not by_issuer:
            return "none", []
        return _status(by_issuer), by_issuer

    def _collect_domain(self, secret: OtpSecret, username: str) -> list[PasswordEntry]:
        found: list[PasswordEntry] = []
        for domain in _domains_for_secret(secret):
            found.extend(self._by_domain_user.get((domain, username), []))
        return self._dedupe(found)

    def _add(self, entry: PasswordEntry) -> None:
        username = entry.username.lower().strip()
        names: set[str] = set()
        title_key = compact_name(entry.title)
        if title_key:
            names.add(title_key)

        for url in entry.get_all_urls():
            domain = domain_from_url(url)
            if not domain:
                continue
            self._append(self._by_domain_user, (domain, username), entry)
            label = compact_name(domain.split(".", 1)[0])
            if label:
                names.add(label)

        for name in names:
            self._append(self._by_name_user, (name, username), entry)
            self._append(self._by_name, name, entry)

    @staticmethod
    def _append(mapping: dict, key, entry: PasswordEntry) -> None:
        bucket = mapping[key]
        if all(existing is not entry for existing in bucket):
            bucket.append(entry)

    @staticmethod
    def _dedupe(entries: list[PasswordEntry]) -> list[PasswordEntry]:
        unique: list[PasswordEntry] = []
        for entry in entries:
            if all(existing is not entry for existing in unique):
                unique.append(entry)
        return unique


def _domains_for_secret(secret: OtpSecret) -> list[str]:
    domains: list[str] = []
    for raw in (secret.issuer,):
        domain = domain_from_url(raw)
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _status(entries: list[PasswordEntry]) -> str:
    if not entries:
        return "none"
    if len(entries) == 1:
        return "one"
    return "many"
