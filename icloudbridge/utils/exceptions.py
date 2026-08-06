"""Shared exception types."""


class SourceUnavailableError(RuntimeError):
    """
    Raised when a sync source cannot be read at all.

    This is distinct from a source that is legitimately empty: it means we could
    not talk to the source, so its true contents are unknown. Treating an
    unreadable source as empty is dangerous, because the sync planner would
    conclude that every mapped item was deleted and propagate that to the other
    side.

    Known causes: macOS revoking TCC access from the running process (which
    happens silently when the interpreter we exec'd from is replaced by a
    package upgrade), an unmounted volume, or a dead EventKit store.
    """
