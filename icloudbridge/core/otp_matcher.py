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

_COMPACT = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class LoginRef:
    """The Apple login fields the preview is allowed to show."""

    title: str
    username: str


@dataclass(frozen=True)
class MatchedOtp:
    """A secret that belongs on exactly one Apple login.

    ``account_differs`` is set when Ente names an account that isn't the
    login's username, which happens when only the issuer matched.
    """

    secret: OtpSecret
    login: LoginRef
    account_differs: bool


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


def hostname_from_url(url: str | None) -> str | None:
    """Lowercase hostname, without ``www.``, from a URL or a bare hostname.

    Returns None for text that isn't a dotted hostname, such as an issuer
    name like ``GitHub`` or ``Rockstar Games``.
    """

    if not url:
        return None
    text = url.strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text}"
    try:
        host = urlparse(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host or " " in host:
        return None
    return host


def compact_name(value: str) -> str:
    """Lowercase letters and digits only, for issuer and title comparison."""

    return _COMPACT.sub("", value.lower())


def build_preview(parsed: EnteParseResult, apple_entries: list[PasswordEntry]) -> dict:
    """JSON-ready preview. Setup keys are included only for logins still missing one."""

    result = match(parsed.secrets, apple_entries)
    return {
        "matched": [
            {
                "issuer": item.secret.issuer,
                "account": item.secret.account,
                "title": item.login.title,
                "username": item.login.username,
                "setup_key": item.secret.secret,
                "otpauth_uri": item.secret.uri,
                "algorithm": item.secret.algorithm,
                "digits": item.secret.digits,
                "period": item.secret.period,
                "qr_only": not item.secret.uses_default_settings,
                "account_differs": item.account_differs,
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

    1. Issuer as a hostname (``google.com``) against the login's site or a
       subdomain of it, plus username.
    2. Issuer against a label of the login's hostname or its title, plus username.
    3. Issuer alone, when exactly one login fits. The match is flagged when
       Ente names a different account.
    """

    index = _LoginIndex(apple_entries)
    result = OtpMatchResult()
    pending: list[tuple[OtpSecret, PasswordEntry]] = []

    for secret in secrets:
        entries = index.lookup(secret)
        if not entries:
            result.unmatched.append(UnmatchedOtp(issuer=secret.issuer, account=secret.account))
            continue
        if len(entries) > 1:
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
            account = secret.account.strip().lower()
            result.matched.append(
                MatchedOtp(
                    secret=secret,
                    login=_login_ref(entry),
                    account_differs=bool(account) and account != entry.username.strip().lower(),
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
    """Hostname and name lookups over Apple logins."""

    def __init__(self, entries: list[PasswordEntry]) -> None:
        self._by_host_user: dict[tuple[str, str], list[PasswordEntry]] = defaultdict(list)
        self._by_name_user: dict[tuple[str, str], list[PasswordEntry]] = defaultdict(list)
        self._by_name: dict[str, list[PasswordEntry]] = defaultdict(list)
        for entry in entries:
            self._add(entry)

    def lookup(self, secret: OtpSecret) -> list[PasswordEntry]:
        username = secret.account.lower().strip()
        issuer_key = compact_name(secret.issuer)

        if username:
            issuer_host = hostname_from_url(secret.issuer)
            if issuer_host:
                by_host = self._by_host_user.get((issuer_host, username), [])
                if by_host:
                    return list(by_host)

            by_name = self._by_name_user.get((issuer_key, username), [])
            if by_name:
                return list(by_name)

        if len(issuer_key) < 3:
            return []
        return list(self._by_name.get(issuer_key, []))

    def _add(self, entry: PasswordEntry) -> None:
        username = entry.username.lower().strip()
        names: set[str] = set()
        title_key = compact_name(entry.title)
        if title_key:
            names.add(title_key)

        for url in entry.get_all_urls():
            host = hostname_from_url(url)
            if not host:
                continue
            labels = host.split(".")
            # accounts.google.com is filed under itself and google.com, so an
            # issuer of google.com finds it. The top-level label is left out.
            for start in range(len(labels) - 1):
                self._append(self._by_host_user, (".".join(labels[start:]), username), entry)
            # Every label but the top-level one can name the site, so a
            # self-hosted nextcloud.example.com is found by "Nextcloud".
            if not host.replace(".", "").isdigit():
                for label in labels[:-1]:
                    name = compact_name(label)
                    if name:
                        names.add(name)

        for name in names:
            self._append(self._by_name_user, (name, username), entry)
            self._append(self._by_name, name, entry)

    @staticmethod
    def _append(mapping: dict, key, entry: PasswordEntry) -> None:
        bucket = mapping[key]
        if all(existing is not entry for existing in bucket):
            bucket.append(entry)
