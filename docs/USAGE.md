# iCloudBridge CLI Usage Guide

Authoritative reference for every `icloudbridge` command. Use this when you need to
quickly recall which switches exist, what they do, and how the commands interact.

- **CLI entry point:** `icloudbridge [OPTIONS] COMMAND [ARGS]...`
- **Supported OS:** macOS 13+ (Apple Notes/Reminders APIs require EventKit + AppleScript)
- **Python runtime:** 3.11+ (Poetry handles dependencies for you)
- **Config:** defaults to `~/.icloudbridge/config.toml` unless overridden with `--config`

> Tip: `icloudbridge --help`, `icloudbridge notes --help`, etc. show contextual help at the
> terminal. This document mirrors that information and adds extra context.

---

## Global Options

These flags are accepted by every command:

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--config PATH` | `-c` | Use a custom configuration file (must exist). The path you pass is also persisted so future runs can reuse it. | last-used config or `~/.icloudbridge/config.toml` |
| `--log-level LEVEL` | `-l` | Override logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). When omitted we honor the `general.log_level` value from the config file. | `INFO` |
| `--help` | — | Show contextual help for the current command. | — |

All examples in this guide assume you are running from the project root or that `icloudbridge`
is installed globally (via `poetry run icloudbridge`, `pipx`, etc.).

---

## Top-Level Commands

### `version`
Shows the CLI, Python, and macOS build information.

```
icloudbridge version
```

### `config`
Inspect or scaffold configuration.

| Option | Description |
|--------|-------------|
| `--show`, `-s` | Render the current effective configuration in a table. |
| `--init`, `-i` | Create (or overwrite) the default config file with sample values. |

Examples:
```
# Create ~/.icloudbridge/config.toml with sample sections
icloudbridge config --init

# Inspect settings currently in effect
icloudbridge config --show
```

### `photos`
Import media from configured watch folders into Apple Photos.

| Option | Description |
|--------|-------------|
| `--dry-run` | Scan and print statistics without importing anything. |
| `--source KEY`, `-s KEY` | Limit the run to specific `photos.sources` keys. Repeat to select multiple sources. |

```
icloudbridge photos --dry-run
icloudbridge photos --source nextcloud --source screenshots
```

### `db-paths`
Print the file paths for the Notes, Reminders, and Passwords SQLite databases so you can
back them up or inspect them with a SQL browser.

```
icloudbridge db-paths
```

### `health`
Performs a quick readiness check (data directory, database presence, configured folders,
CalDAV URL, etc.).

```
icloudbridge health
```

### `serve`
Launch the bundled FastAPI backend + WebUI without using the menubar app. Helpful during
development or when running on a server/VM.

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--host` | `-h` | Interface to bind. Use `0.0.0.0` to listen on all interfaces. | `127.0.0.1` |
| `--port` | `-p` | TCP port for HTTP/WebSocket traffic. | `8000` |
| `--reload` | — | Enable code auto-reload (development only). |
| `--background` | `-d` | Detach and keep the server running in the background (launches a new process). |

Examples:
```
icloudbridge serve --reload
icloudbridge serve --host 0.0.0.0 --port 9000
icloudbridge serve --background
```

---

## Notes Commands

All Notes commands live under `icloudbridge notes ...`. Ensure you configured
`notes.enabled=true` and `notes.remote_folder` first.

### `notes sync`
Bidirectional Notes ⇄ Markdown sync. Every option is listed below:

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--folder NAME` | `-f` | Restrict the run to a single Apple Notes folder / Markdown subfolder. | all folders |
| `--mode MODE` | `-m` | Direction: `bidirectional`, `import` (Markdown → Apple Notes), or `export` (Apple Notes → Markdown). | `bidirectional` |
| `--dry-run` | `-n` | Preview changes without writing anything. |
| `--skip-deletions` | — | Never delete notes on either side. |
| `--deletion-threshold N` | — | Maximum deletions allowed before aborting (`-1` disables the guard). | `5` |
| `--rich-notes / --no-rich-notes` | — | When enabled, export read-only Rich Notes HTML snapshots into `RichNotes/` after sync. | `false` |
| `--shortcut-push / --classic-push` | — | Force the Shortcut pipeline (default) or fall back to the legacy AppleScript HTML push. | Shortcut pipeline |

Important behavior:
- The CLI always copies Apple’s `NoteStore.sqlite` and runs the rich-notes ripper so you need to
  grant Full Disk Access to `/usr/bin/python3`.
- Checklist handling requires the Shortcuts (`ICLOUDBRIDGE_NOTES__CHECKLIST_SHORTCUT` and
  `ICLOUDBRIDGE_NOTES__CONTENT_SHORTCUT`). Use `--classic-push` only if Shortcuts are broken.

Examples:
```
icloudbridge notes sync                       # full sync
icloudbridge notes sync --dry-run             # preview
icloudbridge notes sync --mode export         # Apple → Markdown only
icloudbridge notes sync --folder "Work" --skip-deletions
icloudbridge notes sync --rich-notes --classic-push
```

### `notes list`
Shows detected Apple Notes folders (name + UUID). No options.

```
icloudbridge notes list
```

### `notes status`
Displays total synced entries, configured Markdown path, and database location.

```
icloudbridge notes status
```

### `notes reset`
Clears the Notes sync tracking database. Does **not** delete actual notes.

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip the confirmation prompt. |

```
icloudbridge notes reset --yes
```

---

## Reminders Commands

Reminders commands require `reminders.enabled=true`, a CalDAV URL, username, and a password
stored via `reminders set-password`.

### `reminders sync`
Synchronize Apple Reminders with CalDAV calendars.

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--apple-calendar NAME` | `-a` | Manually select an Apple Reminders list when not using auto-discovery. |
| `--caldav-calendar NAME` | `-c` | CalDAV calendar to pair with `--apple-calendar`. |
| `--auto / --no-auto` | — | Force auto-discovery on/off. When omitted we read `reminders.sync_mode`. |
| `--dry-run` | `-n` | Preview changes without modifying either side. |
| `--skip-deletions` | — | Never delete reminders. |
| `--deletion-threshold N` | — | Abort if more than `N` deletions would occur (`-1` disables). | `5` |

Auto mode syncs every configured mapping (e.g., Apple “Reminders” → CalDAV “tasks”). Manual mode
requires both `--apple-calendar` and `--caldav-calendar`.

### `reminders list`
Prints every Apple Reminders list with its UUID. No options.

### `reminders status`
Summarizes current configuration (CalDAV URL/user, whether the password is stored in the keyring,
manual calendar overrides, etc.). No options.

### `reminders reset`
Clears the Reminders sync database.

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation. |

### `reminders set-password`
Securely store the CalDAV password in the macOS keychain.

| Option | Description |
|--------|-------------|
| `--username`, `-u` | Override the username (defaults to the one in config). |

### `reminders delete-password`
Remove the stored CalDAV password.

| Option | Description |
|--------|-------------|
| `--username`, `-u` | Target username (defaults to config value). |
| `--yes`, `-y` | Skip confirmation. |

---

## Password Commands

All password-related automation is grouped under `icloudbridge passwords ...`. The database
lives alongside other sync metadata and every CSV generated contains plaintext secrets—delete
those files immediately after importing them.

### `passwords provider [SERVICE]`
Show or set the active downstream service.

- `SERVICE` accepts `bitwarden`/`vaultwarden` or `nextcloud`. When omitted we simply display the
  current choice.

### `passwords import-apple CSV`
Seed the database using Apple’s “Export Passwords…” CSV (argument is required).

### `passwords export-bitwarden`
Build a Bitwarden/Vaultwarden-compatible CSV.

| Option | Description |
|--------|-------------|
| `--output PATH`, `-o PATH` | Destination file (will be created with `0600` permissions). |
| `--apple-csv PATH` | The original Apple export used to map UUIDs ↔ vault entries. |

### `passwords import-bitwarden CSV`
Pull entries from a Bitwarden/Vaultwarden CSV export into the local database.

### `passwords export-apple`
Generate a CSV that Apple Passwords can import (only entries missing from Apple’s side).

| Option | Description |
|--------|-------------|
| `--output PATH`, `-o PATH` | Destination file. |
| `--bitwarden-csv PATH` | Original Bitwarden export to diff against. |

### `passwords status`
Inspect database totals and timestamps for the last Apple/Bitwarden push-pull operations.

### `passwords set-bitwarden-credentials`
Interactive helper that saves Vaultwarden URL/email/password (and optional OAuth client ID/secret)
into the macOS keychain.

### `passwords delete-bitwarden-credentials`
Remove stored Vaultwarden credentials.

| Option | Description |
|--------|-------------|
| `--email ADDRESS` | Email to delete (defaults to config value). |
| `--yes` | Skip confirmation. |

### `passwords set-nextcloud-credentials`
Store your Nextcloud Passwords username + app password in the keychain. Prompts for URL and user
if the config is missing those fields.

### `passwords delete-nextcloud-credentials`
Delete stored Nextcloud credentials.

| Option | Description |
|--------|-------------|
| `--username NAME` | Username to delete (defaults to config value). |
| `--yes` | Skip confirmation. |

### `passwords sync`
End-to-end synchronization: push Apple passwords into the configured provider and pull changes
back into Apple via generated CSVs.

| Option | Description |
|--------|-------------|
| `--apple-csv PATH` | Required. Fresh Apple “Export Passwords…” CSV so we can reconcile updates. |
| `--output PATH`, `-o PATH` | Where to store the Apple-import CSV produced during the pull phase. |
| `--bulk` | Enable provider-specific bulk APIs when available (faster on Vaultwarden). |

Workflow: authenticate against the chosen provider (credentials must already be in the keychain),
compare entries, push additions/updates, optionally download new records from the provider, and
tell you how to import them back into Apple.

### `passwords reset`
Delete everything from the password sync database.

| Option | Description |
|--------|-------------|
| `--yes` | Skip confirmation. |

---

## Service Commands

`icloudbridge service ...` manages a macOS LaunchAgent wrapper around `icloudbridge serve` so you
can keep the API server running without the menubar app.

### `service install`

| Option | Description |
|--------|-------------|
| `--port N` | API port exposed by the service (passed to `serve --port`). |
| `--start-on-boot / --no-start-on-boot` | Auto-start when you log in. Defaults to `true`. |

Creates `~/Library/LaunchAgents/com.icloudbridge.server.plist` plus log files in
`~/Library/Logs/iCloudBridge/`.

### `service uninstall`
Removes the LaunchAgent plist and unloads it from `launchctl`.

### `service status`
Reports whether the LaunchAgent is loaded/running and shows the PID if available.

### `service start`
Loads the plist via `launchctl load ...`.

### `service stop`
Unloads the plist via `launchctl unload ...`.

### `service restart`
Stops then starts the LaunchAgent (handy after changing the config).

---

## Additional Tips

- `icloudbridge db-paths` and `icloudbridge notes status` are safe read-only commands you can use
  before any sync to ensure the databases exist where you expect them.
- The CLI respects environment variables defined in `docs/config.md` (e.g.,
  `ICLOUDBRIDGE_NOTES__REMOTE_FOLDER`). CLI flags override environment variables, which override
  the config file.
- All password-related commands touch plaintext CSV files—treat them with the same care as any
  exported secrets. Delete them after importing.
- Commands that interact with Apple Notes/Reminders will launch permission prompts the first time
  they run. Accept them in System Settings → Privacy & Security.

You now have a single reference for every available CLI option. Happy syncing!🌥️
