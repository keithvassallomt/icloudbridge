"""Matching Ente Auth secrets onto Apple Passwords logins."""

from icloudbridge.core.otp_matcher import domain_from_url, match
from icloudbridge.sources.passwords.ente_auth import OtpSecret
from icloudbridge.sources.passwords.models import PasswordEntry


def secret(
    issuer: str,
    account: str,
    key: str = "BPHY57NROF5SEVY7",
) -> OtpSecret:
    return OtpSecret(
        issuer=issuer,
        account=account,
        secret=key,
        algorithm="SHA1",
        digits=6,
        period=30,
        uri=f"otpauth://totp/{issuer}:{account}?secret={key}&issuer={issuer}",
    )


def login(
    title: str,
    username: str,
    url: str | None = None,
    otp_auth: str | None = None,
    extra_urls: list[str] | None = None,
) -> PasswordEntry:
    entry = PasswordEntry(
        title=title,
        username=username,
        password="secret-password",
        url=url,
        otp_auth=otp_auth,
    )
    for extra in extra_urls or []:
        entry.add_url(extra)
    return entry


def test_registrable_domain():
    assert domain_from_url("https://accounts.google.com/ServiceLogin") == "google.com"
    assert domain_from_url("https://www.example.co.uk/login") == "example.co.uk"
    assert domain_from_url("vault.bitwarden.com") == "bitwarden.com"


def test_tier1_domain_and_username():
    apple = [
        login("Work", "ada@example.com", "https://accounts.google.com/"),
        login("Personal", "grace@example.com", "https://accounts.google.com/"),
    ]

    result = match([secret("Google.com", "ada@example.com")], apple)

    assert len(result.matched) == 1
    assert result.matched[0].login.title == "Work"
    assert result.matched[0].setup_key == "BPHY57NROF5SEVY7"
    assert result.ambiguous == []
    assert result.unmatched == []


def test_tier2_issuer_matches_domain_label_and_username():
    apple = [
        login(
            "vault.bitwarden.com (ada@example.com)",
            "ada@example.com",
            "https://vault.bitwarden.com/",
        )
    ]

    result = match(
        [secret("Bitwarden", "ada@example.com", "HYCKEEXKYXUAUSV2LMHFN6XZAMBQN2AU")],
        apple,
    )

    assert [item.login.title for item in result.matched] == [
        "vault.bitwarden.com (ada@example.com)"
    ]


def test_tier2_issuer_matches_title_and_username():
    apple = [login("GitHub", "octocat", "https://github.com/login")]

    result = match([secret("GitHub", "octocat")], apple)

    assert [item.login.title for item in result.matched] == ["GitHub"]


def test_tier3_issuer_alone_when_one_login_fits():
    apple = [login("GitHub", "ada@example.com", "https://github.com/")]

    result = match([secret("GitHub", "octocat")], apple)

    assert [item.login.title for item in result.matched] == ["GitHub"]
    assert result.matched[0].account == "octocat"


def test_ambiguous_when_several_logins_fit():
    apple = [
        login("GitHub", "ada@example.com", "https://github.com/"),
        login("GitHub work", "grace@example.com", "https://github.com/"),
    ]

    result = match([secret("GitHub", "someone-else")], apple)

    assert result.matched == []
    assert len(result.ambiguous) == 1
    assert {item.title for item in result.ambiguous[0].candidates} == {"GitHub", "GitHub work"}


def test_domain_match_does_not_fall_through_when_ambiguous():
    apple = [
        login("Google", "ada@example.com", "https://accounts.google.com/"),
        login("Gmail", "ada@example.com", "https://mail.google.com/"),
    ]

    result = match([secret("Google.com", "ada@example.com")], apple)

    assert result.matched == []
    assert len(result.ambiguous) == 1
    assert len(result.ambiguous[0].candidates) == 2


def test_already_set_when_the_secret_matches():
    key = "BPHY57NROF5SEVY7"
    apple = [
        login(
            "GitHub",
            "octocat",
            "https://github.com/",
            otp_auth=f"otpauth://totp/GitHub:octocat?secret={key}&issuer=GitHub",
        )
    ]

    result = match([secret("GitHub", "octocat", key)], apple)

    assert result.matched == []
    assert result.already_set[0].login.title == "GitHub"
    assert result.conflict == []


def test_conflict_when_the_login_has_a_different_secret():
    apple = [
        login(
            "GitHub",
            "octocat",
            "https://github.com/",
            otp_auth="otpauth://totp/GitHub:octocat?secret=CKW5UEE2J5IWPYVJ&issuer=GitHub",
        )
    ]

    result = match([secret("GitHub", "octocat")], apple)

    assert result.matched == []
    assert result.conflict[0].login.title == "GitHub"


def test_unmatched():
    result = match([secret("Incogni", "ada@example.com")], [login("GitHub", "ada@example.com")])

    assert result.unmatched[0].issuer == "Incogni"
    assert result.matched == []


def test_two_ente_secrets_for_one_login_conflict():
    apple = [login("GitHub", "octocat", "https://github.com/")]

    result = match(
        [
            secret("GitHub", "octocat", "BPHY57NROF5SEVY7"),
            secret("GitHub", "octocat", "CKW5UEE2J5IWPYVJ"),
        ],
        apple,
    )

    assert result.matched == []
    assert len(result.conflict) == 2


def test_extra_urls_share_one_login():
    apple = [
        login(
            "US Mobile",
            "ada@example.com",
            "https://usmobile.com/",
            extra_urls=["https://app.usmobile.com/", "https://www.usmobile.com/"],
        )
    ]

    result = match([secret("US Mobile", "ada@example.com")], apple)

    assert len(result.matched) == 1
    assert result.ambiguous == []
