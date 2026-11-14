"""Actionable error categories and trace locations without exception secrets."""

import traceback

import httpx


class UserFacingError(Exception):
    """An error whose message is written by us, never contains secrets and is safe to show."""


class InvalidInputError(UserFacingError, ValueError):
    """A bad Apple CSV or an invalid combination of sync options."""


class ProviderAuthError(UserFacingError):
    """The password provider rejected the login or could not authenticate."""


def safe_failure(exc: Exception) -> str:
    if isinstance(exc, UserFacingError):
        return str(exc)
    if isinstance(exc, FileExistsError):
        return "Export already exists; choose a new output filename."
    if isinstance(exc, FileNotFoundError):
        return "Input file not found; select a fresh Apple Passwords export."
    if isinstance(exc, ValueError):
        return "Invalid input; check the Apple CSV format and sync options."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "Provider authentication or permission failed; check connection settings."
        return f"Provider request failed (HTTP {code}); refresh snapshots before retrying."
    if isinstance(exc, httpx.TransportError):
        return "Provider connection failed; check network and server availability."
    return f"Password sync failed ({type(exc).__name__}); refresh snapshots before retrying."


def log_failure(logger, exc: Exception) -> None:
    # format_exception would include exception text (possibly a password/body/URL).
    frames = traceback.extract_tb(exc.__traceback__)
    locations = "\n".join(f"{f.filename}:{f.lineno} in {f.name}" for f in frames)
    cause = exc.__cause__
    if cause is not None:
        # Type only: the cause's text is not ours and may contain secrets.
        locations += f"\ncaused by {type(cause).__name__}"
    logger.error("%s\n%s", safe_failure(exc), locations)
