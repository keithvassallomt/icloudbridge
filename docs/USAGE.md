# iCloudBridge Usage Guide

Complete reference for all iCloudBridge CLI commands.

**Version**: 0.1.0
**Platform**: macOS only (requires AppleScript/EventKit)
**Python**: 3.11+

---

## Table of Contents

- [Global Options](#global-options)
- [Configuration](#configuration)
- [Notes Synchronization](#notes-synchronization)
- [Reminders Synchronization](#reminders-synchronization)
- [Passwords Synchronization](#passwords-synchronization)
- [Utility Commands](#utility-commands)

---

## Global Options

Available for all commands:

```bash
icloudbridge [OPTIONS] COMMAND [ARGS]
```

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--config` | `-c` | Path to configuration file | `~/.icloudbridge/config.toml` |
| `--log-level` | `-l` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | INFO |
| `--help` | | Show help message | |

**Example**:
```bash
icloudbridge --log-level DEBUG notes sync
icloudbridge --config ~/my-config.toml reminders sync
```

---

## Configuration

### `config --init`

Create a default configuration file.

```bash
icloudbridge config --init
```

**Creates**: `~/.icloudbridge/config.toml`

**Example config**:
```toml
[general]
log_level = "INFO"
data_dir = "~/.icloudbridge"

[notes]
enabled = true
remote_folder = "~/Nextcloud/Notes"

[reminders]
enabled = true
sync_mode = "auto"
caldav_url = "https://nextcloud.example.com/remote.php/dav"
caldav_username = "your_username"
# Password stored in system keyring - use: icloudbridge reminders set-password

[reminders.calendar_mappings]
"Reminders" = "tasks"
"Work" = "work-tasks"

[passwords]
enabled = true
provider = "vaultwarden"  # or "nextcloud"

# VaultWarden configuration
vaultwarden_url = "https://vault.example.com"
vaultwarden_email = "user@example.com"
# Credentials stored in system keyring - use: icloudbridge passwords set-bitwarden-credentials

# Nextcloud configuration (if using nextcloud provider)
nextcloud_url = "https://cloud.example.com"
nextcloud_username = "your_username"
# Credentials stored in system keyring - use: icloudbridge passwords set-nextcloud-credentials
```

---

## Notes Synchronization

Bidirectional sync between Apple Notes and Markdown files.

### `notes sync`

Synchronize notes between Apple Notes and markdown files.

```bash
icloudbridge notes sync [OPTIONS]
```

**Options**:

| Option | Short | Type | Description | Default |
|--------|-------|------|-------------|---------|
| `--folder` | `-f` | TEXT | Sync specific folder only | All folders |
| `--dry-run` | `-n` | flag | Preview changes without applying them | false |
| `--skip-deletions` | | flag | Skip all deletion operations | false |
| `--deletion-threshold` | | INT | Max deletions before confirmation (-1 to disable) | 5 |
| `--rich-notes / --no-rich-notes` | | flag | After sync, export read-only RichNotes snapshots | false |
| `--shortcut-push / --classic-push` | | flag | Choose Shortcut pipeline (default) or legacy AppleScript pipeline when pushing markdown to Apple Notes | shortcut-push |

> **Note**: The sync engine now **always** copies the Apple Notes database and feeds the Ruby-based
> rich-notes ripper to obtain HTML bodies. Grant `tools/note_db_copy/copy_note_db.py` Full Disk
> Access on macOS. The `--rich-notes` flag simply adds an extra read-only `RichNotes/` export and no
> longer toggles the sync source. The system "Recently Deleted" folder is ignored automatically and
> cannot be synced. If you install the `CheckListBuilder` Shortcut (override with
> `ICLOUDBRIDGE_NOTES__CHECKLIST_SHORTCUT`) and the companion `NoteContentBuilder` Shortcut
> (override with `ICLOUDBRIDGE_NOTES__CONTENT_SHORTCUT`), iCloudBridge routes Markdown checklists and
> non-checklist content through those shortcuts to produce real Apple Notes checklists. This requires
> the macOS `shortcuts` CLI; if the shortcuts are missing or fail, we gracefully fall back to plain
> bullets. Shortcut push mode is now the default for every note; pass `--classic-push` (or set
> `ICLOUDBRIDGE_NOTES__USE_SHORTCUTS_FOR_PUSH=false`) if you need the legacy AppleScript HTML path.

**Examples**:

```bash
# Sync all notes
icloudbridge notes sync

# Preview changes without syncing
icloudbridge notes sync --dry-run

# Export rich Markdown snapshots (read-only)
icloudbridge notes sync --rich-notes

# Sync specific folder only
icloudbridge notes sync --folder "Work Notes"

# Skip deletions (safer for first sync)
icloudbridge notes sync --skip-deletions

# Allow unlimited deletions
icloudbridge notes sync --deletion-threshold -1
```

**Configuration Required**:
```bash
export ICLOUDBRIDGE_NOTES__ENABLED=true
export ICLOUDBRIDGE_NOTES__REMOTE_FOLDER=~/Nextcloud/Notes
```

**How it works**:
- Last-write-wins conflict resolution
- Root-level notes auto-migrated to "Notes" folder (Apple Notes requires folders)
- SQLite database tracks sync state (UUID → path mappings)
- First `<h1>` tag is used as note title (to avoid duplication)
- Rich bodies and checklists come from the ripper snapshot (`icloudbridge/core/rich_notes_capture.py`)

### `notes list`

List all Apple Notes folders.

```bash
icloudbridge notes list
```

**Output**:
```
📂 Apple Notes Folders:
   - Personal (UUID: 550e8400-...)
   - Work Notes (UUID: 660e9500-...)
   - Shopping Lists (UUID: 770ea600-...)
```

### `notes status`

Show notes synchronization status.

```bash
icloudbridge notes status
```

**Output**:
```
Configuration:
  Remote folder: ~/Nextcloud/Notes
  Last sync: 2025-11-02 15:30:00 (2 hours ago)

Database:
  Total mappings: 152
  Database: ~/.icloudbridge/notes.db
```

### `notes reset`

Reset the sync database (clears all note mappings).

```bash
icloudbridge notes reset [--yes]
```

**Options**:
- `--yes` - Skip confirmation prompt

⚠️ **Warning**: This clears the sync state. Next sync will treat all notes as new.

---

### Rich Notes Export

The `--rich-notes` flag exports a read-only snapshot of your Apple Notes with rich formatting preserved.

**When to use**:
- Creating backups with full formatting
- Viewing notes outside of Apple Notes app
- Archiving notes with images and tables
- Sharing formatted notes (read-only)

**How it works**:
1. After sync completes, exports all notes to `RichNotes/` folder
2. Uses Ruby-based ripper to extract canonical HTML from NoteStore.sqlite
3. Converts to enhanced Markdown with formatting preserved
4. Organizes by folder structure matching Apple Notes

**Example**:
```bash
# Export rich notes after sync
icloudbridge notes sync --rich-notes

# Output structure:
# ~/Nextcloud/Notes/
# ├── Personal/              (bidirectional sync)
# │   ├── shopping-list.md
# │   └── ideas.md
# └── RichNotes/             (read-only export)
#     └── Personal/
#         ├── shopping-list.md   (rich formatting)
#         └── ideas.md           (with tables, images)
```

**What's included in Rich Notes**:
- Full HTML formatting (bold, italic, underline)
- Tables and lists
- Embedded images (as base64 or links)
- Checklists (native Apple Notes format)
- Links with preview URLs

**Note**: RichNotes are read-only. Changes should be made in the regular sync folders, not in RichNotes/.

---

### Attachment Handling

When notes contain attachments (images, PDFs, etc.), iCloudBridge organizes them with metadata:

**Structure**:
```
~/Nextcloud/Notes/
├── my-note.md                    # Main note content
├── .attachments.my-note/         # Hidden attachments folder
│   ├── image1.jpg
│   └── document.pdf
└── .my-note.md.meta.json        # Hidden metadata (sync state)
```

**Metadata file** (`.my-note.md.meta.json`):
```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "folder_uuid": "660e9500-e29b-41d4-a716-446655440001",
  "last_modified": "2025-11-08T15:30:00Z",
  "attachments": [
    {
      "filename": "image1.jpg",
      "type": "image/jpeg",
      "size": 102400
    }
  ]
}
```

**Why sidecar files?**
- Preserves sync mappings (UUID to path)
- Tracks modification times
- Maintains attachment relationships
- Hidden by default (`.` prefix)

**Note**: Don't manually edit or delete `.meta.json` files unless resetting sync state.

---

## Reminders Synchronization

Bidirectional sync between Apple Reminders and CalDAV (NextCloud, etc.).

### `reminders sync`

Synchronize reminders between Apple Reminders and CalDAV.

```bash
icloudbridge reminders sync [OPTIONS]
```

**Options**:

| Option | Short | Type | Description | Default |
|--------|-------|------|-------------|---------|
| `--apple-calendar` | `-a` | TEXT | Apple Reminders calendar/list (manual mode) | Auto mode |
| `--caldav-calendar` | `-c` | TEXT | CalDAV calendar to sync with (manual mode) | Auto mode |
| `--auto` / `--no-auto` | | flag | Auto-discover and sync all calendars | From config |
| `--dry-run` | `-n` | flag | Preview changes without applying | false |
| `--skip-deletions` | | flag | Skip all deletion operations | false |
| `--deletion-threshold` | | INT | Max deletions before confirmation (-1 to disable) | 5 |

**Auto Mode** (default):
```bash
# Syncs all calendars with default mappings
icloudbridge reminders sync

# "Reminders" → "tasks" (NextCloud default)
# Other calendars → auto-created CalDAV calendars with matching names
```

**Manual Mode** (specific calendar):
```bash
# Sync specific calendar pair
icloudbridge reminders sync --apple-calendar "Work" --caldav-calendar "work-tasks"

# Preview specific calendar sync
icloudbridge reminders sync -a "Personal" -c "personal" --dry-run
```

**Configuration Required**:
```bash
# Option 1: Environment variables
export ICLOUDBRIDGE_REMINDERS__ENABLED=true
export ICLOUDBRIDGE_REMINDERS__CALDAV_URL=https://nextcloud.example.com/remote.php/dav
export ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME=myuser

# Option 2: Set password in keyring (recommended)
icloudbridge reminders set-password
```

**Features**:
- Uses EventKit API (better than AppleScript)
- Full VTODO support: priority, due dates, completion, URLs
- Alarms and recurrence rules
- Last-write-wins conflict resolution

### `reminders set-password`

Store CalDAV password securely in system keyring.

```bash
icloudbridge reminders set-password [OPTIONS]
```

**Options**:
- `--username` - CalDAV username (or from config/env)

**Interactive prompt**:
```
Enter CalDAV password (input hidden):
Confirm password:

✓ Password stored securely for user: myuser
```

**Storage**: macOS Keychain, Windows Credential Manager, or Linux Secret Service

### `reminders delete-password`

Delete CalDAV password from system keyring.

```bash
icloudbridge reminders delete-password [OPTIONS]
```

**Options**:
- `--username` - CalDAV username
- `--yes` - Skip confirmation

### `reminders list`

List Apple Reminders calendars/lists.

```bash
icloudbridge reminders list
```

**Output**:
```
📅 Apple Reminders Calendars:
   - Reminders (UUID: 550e8400-...)
   - Work (UUID: 660e9500-...)
   - Personal (UUID: 770ea600-...)
```

### `reminders status`

Show reminders sync status.

```bash
icloudbridge reminders status
```

**Output**:
```
Configuration:
  CalDAV URL: https://nextcloud.example.com/remote.php/dav
  Username: myuser
  Password: ✓ Stored in keyring
  Sync mode: auto

Calendar Mappings:
  "Reminders" → "tasks"
  "Work" → "work-tasks"

Last Sync: 2025-11-02 15:30:00 (2 hours ago)
Database: ~/.icloudbridge/reminders.db
```

### `reminders reset`

Reset reminders sync database (clear all mappings).

```bash
icloudbridge reminders reset [--yes]
```

**Options**:
- `--yes` - Skip confirmation prompt

---

## Passwords Synchronization

Semi-automated sync between Apple Passwords and password managers (VaultWarden or Nextcloud Passwords) via API.

### Supported Providers

iCloudBridge supports two password sync providers:

1. **VaultWarden/Bitwarden** - Self-hosted Bitwarden-compatible server
2. **Nextcloud Passwords** - Built-in Nextcloud password manager

Choose your provider with the CLI (recommended):

```bash
# Show current selection
icloudbridge passwords provider

# Switch to Nextcloud Passwords and persist it in ~/.icloudbridge/config.toml
icloudbridge passwords provider nextcloud
```

You can still override via `ICLOUDBRIDGE_PASSWORDS__PROVIDER` environment variable (`bitwarden`/`vaultwarden` or `nextcloud`).

---

### Setup (One-Time)

Choose your provider and follow the corresponding setup:

#### Option A: VaultWarden Setup

**1. Store VaultWarden Credentials**

```bash
icloudbridge passwords set-bitwarden-credentials
```

**Interactive prompts**:
```
VaultWarden URL (e.g., https://vault.example.com): https://vault.yourdomain.com
VaultWarden Email: user@example.com

Enter VaultWarden password (input hidden):
Password: ********
Confirm password: ********

OAuth client ID and secret (optional, press Enter to skip):
Client ID:
Client Secret:

✅ VaultWarden credentials stored securely
   Email: user@example.com
   URL: https://vault.yourdomain.com

💡 Test connection with:
   icloudbridge passwords sync --apple-csv <path/to/passwords.csv>
```

**Storage**: macOS Keychain (secure, no plaintext in config)

#### Option B: Nextcloud Passwords Setup

**1. Generate Nextcloud App Password**

1. Log into your Nextcloud instance
2. Go to Settings → Security → Devices & sessions
3. Enter "iCloudBridge" as device name
4. Click "Create new app password"
5. Copy the generated password

**2. Store Nextcloud Credentials**

```bash
icloudbridge passwords set-nextcloud-credentials
```

**Interactive prompts**:
```
Nextcloud URL (e.g., https://cloud.example.com): https://cloud.yourdomain.com
Nextcloud Username: your_username

Enter Nextcloud App Password (not your regular password!):
Generate one at: Settings → Security → Devices & sessions

App Password: ********
Confirm app password: ********

✅ Nextcloud credentials stored securely
   Username: your_username
   URL: https://cloud.yourdomain.com

💡 Set provider in config:
   icloudbridge passwords provider nextcloud

💡 Test connection with:
   icloudbridge passwords sync --apple-csv <path/to/passwords.csv>
```

**Storage**: macOS Keychain (secure, no plaintext in config)

**Note**: Always use an app password, never your regular Nextcloud password!

---

### `passwords provider`

Display or change the active password sync provider. The selection is saved to `~/.icloudbridge/config.toml` so future CLI and API runs stay in sync.

```bash
# Show current provider
icloudbridge passwords provider

# Switch to Nextcloud Passwords
icloudbridge passwords provider nextcloud

# Switch back to Bitwarden/Vaultwarden
icloudbridge passwords provider bitwarden
```

**Tip**: Run this once during setup so you don't need to export `ICLOUDBRIDGE_PASSWORDS__PROVIDER` manually.

---

### `passwords sync`

Full auto-sync: Apple → Password Manager (push) and Password Manager → Apple (pull).

Works with both VaultWarden and Nextcloud Passwords providers.

```bash
icloudbridge passwords sync --apple-csv PASSWORDS.CSV [OPTIONS]
```

**Options**:

| Option | Short | Type | Description | Default |
|--------|-------|------|-------------|---------|
| `--apple-csv` | | PATH | Apple Passwords CSV export | **Required** |
| `--output` | `-o` | PATH | Output path for Apple CSV | `data_dir/apple-import.csv` |
| `--bulk` | | flag | Use bulk import (if supported by provider) | false |

**Complete Workflow**:

```bash
# 1. Set your provider (if not using VaultWarden)
icloudbridge passwords provider nextcloud  # or bitwarden

# 2. Export from Apple Passwords
# Settings → Passwords → ⚙️ → Export Passwords → passwords.csv

# 3. Run full auto-sync
icloudbridge passwords sync --apple-csv ~/Downloads/passwords.csv

# 4. Import generated CSV to Apple Passwords (if new entries found)
# Passwords app → File → Import Passwords → apple-import.csv

# 5. Delete CSV files (security)
rm ~/Downloads/passwords.csv
rm ~/Library/Application\ Support/iCloudBridge/apple-import.csv
```

**Output Example (Nextcloud)**:
```
🔐 Password Full Auto-Sync
Provider: nextcloud

Authenticating with nextcloud...

============================================================
📤 Apple → Nextcloud (Push)
============================================================
Created                    5
Updated                    0
Skipped (unchanged)        2135

============================================================
📥 Nextcloud → Apple (Pull)
============================================================
✅ Generated Apple CSV with 3 new entries
   File: ~/.icloudbridge/apple-import.csv

⚠️  Manual step required:
   1. Open Passwords app
   2. File → Import Passwords
   3. Select: ~/.icloudbridge/apple-import.csv
   4. Delete CSV file after import

============================================================
✅ Sync complete in 3.8s
============================================================

⚠️  SECURITY REMINDER
   Delete CSV files after import:
   → rm ~/Downloads/passwords.csv
   → rm ~/.icloudbridge/apple-import.csv
```

**What Gets Automated**:
- ✅ Apple → Password Manager: Direct API push (no manual import!)
- ✅ Change detection (only updates what changed)
- ✅ Deduplication
- ✅ Hash-based change tracking
- ✅ Folder/collection organization

**What Remains Manual** (Apple limitations):
- ❌ Apple Passwords export (must use Settings app)
- ❌ Apple Passwords import (must use Passwords app)

**Provider Differences**:
- **VaultWarden**: Supports bulk import with `--bulk` flag (faster for large imports)
- **Nextcloud**: Creates passwords individually (more reliable, slightly slower)

---

### Alternative: Manual CSV Workflow

For users without VaultWarden API access, use CSV-based workflow:

#### `passwords import-apple`

Import passwords from Apple Passwords CSV export.

```bash
icloudbridge passwords import-apple PASSWORDS.CSV
```

**Output**:
```
📥 Importing from Apple Passwords...
   Found: 2152 entries

   Processing:
   ├─ New entries: 5
   ├─ Updated entries: 12 (password changed)
   ├─ Duplicates skipped: 3
   └─ Unchanged: 2132

✅ Import complete
   Database: ~/.icloudbridge/passwords.db
   Last import: 2025-11-02 15:30:00

⚠️  SECURITY WARNING
   CSV file contains plaintext passwords!
   Delete immediately: ~/Downloads/passwords.csv

💡 Next step: Generate Bitwarden import file
   → icloudbridge passwords export-bitwarden -o bitwarden.csv --apple-csv ~/Downloads/passwords.csv
```

#### `passwords export-bitwarden`

Generate Bitwarden-formatted CSV for import.

```bash
icloudbridge passwords export-bitwarden -o OUTPUT.CSV --apple-csv APPLE_CSV
```

**Options**:
- `-o, --output` - Output CSV file path (required)
- `--apple-csv` - Original Apple Passwords CSV (for plaintext passwords) (required)

**Output**:
```
🔐 Bitwarden CSV Export

✅ Bitwarden CSV generated
   File: bitwarden.csv
   Entries: 2152
   Permissions: 0600 (owner read/write only)

⚠️  SECURITY WARNING
   Generated CSV contains plaintext passwords!
   1. Import to Bitwarden immediately
   2. Delete file: rm bitwarden.csv

💡 Import to Bitwarden:
   Settings → Import Data → Bitwarden (csv)
   Then delete both CSV files!
```

#### `passwords-import-bitwarden`

Import passwords from Bitwarden CSV export.

```bash
icloudbridge passwords-import-bitwarden BITWARDEN.CSV
```

**Output**: Similar to `passwords-import-apple`

#### `passwords-export-apple`

Generate Apple Passwords CSV for entries only in Bitwarden (not in Apple).

```bash
icloudbridge passwords-export-apple -o OUTPUT.CSV --bitwarden-csv BITWARDEN_CSV
```

**Options**:
- `-o, --output` - Output CSV file path (required)
- `--bitwarden-csv` - Original Bitwarden CSV export (required)

**Use case**: Sync new passwords from Bitwarden back to Apple Passwords.

---

### `passwords-status`

Show password sync status.

```bash
icloudbridge passwords-status
```

**Output**:
```
🔐 Password Sync Status

Database Statistics:
  Total entries          2152
    From apple           2152
    From bitwarden       0

Last Syncs:
  Apple import           2025-11-02 15:30:00 (2 hours ago)
  Bitwarden export       2025-11-02 15:31:00 (2 hours ago)
  Bitwarden import       Never
  Apple export           Never

Database: ~/.icloudbridge/passwords.db
```

### `passwords-set-nextcloud-credentials`

Store Nextcloud Passwords credentials securely in system keyring.

```bash
icloudbridge passwords-set-nextcloud-credentials
```

See [Option B: Nextcloud Passwords Setup](#option-b-nextcloud-passwords-setup) for interactive prompts and usage.

### `passwords-delete-nextcloud-credentials`

Delete Nextcloud Passwords credentials from system keyring.

```bash
icloudbridge passwords-delete-nextcloud-credentials [OPTIONS]
```

**Options**:
- `--username` - Nextcloud username (or from config)
- `--yes` - Skip confirmation

### `passwords-delete-vaultwarden-credentials`

Delete VaultWarden credentials from system keyring.

```bash
icloudbridge passwords-delete-vaultwarden-credentials [OPTIONS]
```

**Options**:
- `--email` - VaultWarden email (or from config)
- `--yes` - Skip confirmation

### `passwords-reset`

Clear all password entries from database.

```bash
icloudbridge passwords-reset [--yes]
```

**Options**:
- `--yes` - Skip confirmation prompt

⚠️ **Warning**: This clears the sync state. Passwords in VaultWarden/Apple are not affected.

---

## API Server

iCloudBridge includes a FastAPI-based REST API for programmatic access to all sync operations.

### `serve`

Start the FastAPI server.

```bash
icloudbridge serve [OPTIONS]
```

**Options**:

| Option | Type | Description | Default |
|--------|------|-------------|---------|
| `--host` | TEXT | Bind host address | 127.0.0.1 |
| `--port` | INT | Bind port | 8000 |
| `--reload` | flag | Enable auto-reload (development) | false |
| `--background` | flag | Run as daemon | false |

**Examples**:
```bash
# Start server (foreground)
icloudbridge serve

# Start on custom port
icloudbridge serve --port 9000

# Development mode with auto-reload
icloudbridge serve --reload

# Run as background daemon
icloudbridge serve --background
```

**Output**:
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

**API Documentation**: Visit `http://localhost:8000/docs` for interactive Swagger UI.

---

### Service Management

Install iCloudBridge as a macOS LaunchAgent for automatic startup.

#### `service install`

Install the API server as a LaunchAgent.

```bash
icloudbridge service install [OPTIONS]
```

**Options**:
- `--port` - Server port (default: 8000)
- `--start-on-boot` - Auto-start on login

**Example**:
```bash
# Install service
icloudbridge service install --start-on-boot

# Install on custom port
icloudbridge service install --port 9000 --start-on-boot
```

**Output**:
```
✅ Service installed successfully
   LaunchAgent: ~/Library/LaunchAgents/com.taskbridge.icloudbridge.plist
   Port: 8000
   Auto-start: Enabled

💡 Manage service:
   icloudbridge service start
   icloudbridge service stop
   icloudbridge service status
```

#### `service uninstall`

Remove the LaunchAgent service.

```bash
icloudbridge service uninstall
```

#### `service status`

Check if the service is running.

```bash
icloudbridge service status
```

**Output**:
```
🟢 Service is running
   PID: 12345
   Port: 8000
   Uptime: 2 hours
```

#### `service start`

Start the service.

```bash
icloudbridge service start
```

#### `service stop`

Stop the service.

```bash
icloudbridge service stop
```

#### `service restart`

Restart the service.

```bash
icloudbridge service restart
```

---

### API Usage Examples

The API provides programmatic access to all CLI functionality. All endpoints are documented at `/docs`.

#### Notes Sync via API

**Sync specific folder:**
```bash
curl -X POST http://localhost:8000/api/notes/sync \
  -H "Content-Type: application/json" \
  -d '{
    "folder": "Personal",
    "dry_run": false,
    "skip_deletions": false,
    "deletion_threshold": 5
  }'
```

**Response:**
```json
{
  "status": "success",
  "message": "Sync completed successfully",
  "created": 5,
  "updated": 12,
  "deleted": 2,
  "unchanged": 135,
  "duration": 3.2
}
```

**Sync all folders:**
```bash
curl -X POST http://localhost:8000/api/notes/sync \
  -H "Content-Type: application/json" \
  -d '{"folder": null}'
```

**List folders:**
```bash
curl http://localhost:8000/api/notes/folders
```

**Get sync status:**
```bash
curl http://localhost:8000/api/notes/status
```

**Get sync history:**
```bash
curl http://localhost:8000/api/notes/history?limit=10
```

#### Reminders Sync via API

**Auto-sync all calendars:**
```bash
curl -X POST http://localhost:8000/api/reminders/sync \
  -H "Content-Type: application/json" \
  -d '{
    "auto": true,
    "dry_run": false
  }'
```

**Sync specific calendar pair:**
```bash
curl -X POST http://localhost:8000/api/reminders/sync \
  -H "Content-Type: application/json" \
  -d '{
    "apple_calendar": "Work",
    "caldav_calendar": "work-tasks",
    "auto": false
  }'
```

**Set CalDAV password:**
```bash
curl -X POST http://localhost:8000/api/reminders/password \
  -H "Content-Type: application/json" \
  -d '{
    "username": "myuser",
    "password": "mypassword"
  }'
```

#### Passwords Sync via API

**Import Apple CSV:**
```bash
curl -X POST http://localhost:8000/api/passwords/import/apple \
  -F "file=@passwords.csv"
```

**Full auto-sync with VaultWarden:**
```bash
curl -X POST http://localhost:8000/api/passwords/sync \
  -F "apple_csv=@passwords.csv"
```

**Get sync status:**
```bash
curl http://localhost:8000/api/passwords/status
```

#### Scheduling

The API includes a scheduler for automated syncs.

**Create schedule:**
```bash
curl -X POST http://localhost:8000/api/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Daily Notes Sync",
    "service": "notes",
    "cron": "0 */6 * * *",
    "enabled": true,
    "config": {
      "skip_deletions": false,
      "deletion_threshold": 5
    }
  }'
```

**List schedules:**
```bash
curl http://localhost:8000/api/schedules
```

**Trigger schedule manually:**
```bash
curl -X POST http://localhost:8000/api/schedules/{id}/run
```

**Toggle schedule:**
```bash
curl -X PUT http://localhost:8000/api/schedules/{id}/toggle
```

#### Configuration Management

**Get current config:**
```bash
curl http://localhost:8000/api/config
```

**Update config:**
```bash
curl -X PUT http://localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "notes": {
      "enabled": true,
      "remote_folder": "~/Nextcloud/Notes"
    }
  }'
```

**Test CalDAV connection:**
```bash
curl http://localhost:8000/api/config/test-connection?service=reminders
```

#### WebSocket Real-Time Updates

Connect to WebSocket for real-time sync progress:

```javascript
const ws = new WebSocket('ws://localhost:8000/api/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Sync update:', data);
};
```

**Python example:**
```python
import requests
import websockets
import asyncio

# Trigger sync
response = requests.post('http://localhost:8000/api/notes/sync', json={
    'folder': 'Personal'
})

# Monitor progress via WebSocket
async with websockets.connect('ws://localhost:8000/api/ws') as ws:
    while True:
        message = await ws.recv()
        print(f'Update: {message}')
```

---

## Utility Commands

### `db-paths`

Show database file locations.

```bash
icloudbridge db-paths
```

**Output**:
```
📂 Database Locations:

Notes DB:
  Path: ~/.icloudbridge/notes.db
  Size: 1.2 MB
  Exists: ✓

Reminders DB:
  Path: ~/.icloudbridge/reminders.db
  Size: 456 KB
  Exists: ✓

Passwords DB:
  Path: ~/.icloudbridge/passwords.db
  Size: 2.1 MB
  Exists: ✓
```

### `version`

Show version information.

```bash
icloudbridge version
```

**Output**:
```
iCloudBridge v0.1.0
Python: 3.11.5
Platform: macOS 14.0
```

### `health`

Check application health and dependencies.

```bash
icloudbridge health
```

**Checks**:
- Python version
- Required dependencies
- System permissions (AppleScript, EventKit)
- Configuration validity
- Database connectivity

**Output**:
```
✅ Python 3.11.5
✅ Dependencies installed
✅ AppleScript available
✅ EventKit permissions granted
✅ Configuration valid
✅ Database accessible

All systems operational
```

---

## Configuration Files

### Default Locations

| File | Path |
|------|------|
| Config | `~/.icloudbridge/config.toml` |
| Notes DB | `~/.icloudbridge/notes.db` |
| Reminders DB | `~/.icloudbridge/reminders.db` |
| Passwords DB | `~/.icloudbridge/passwords.db` |
| Logs | `~/.icloudbridge/logs/` |

### Environment Variables

All config options can be overridden with environment variables:

```bash
# Format: ICLOUDBRIDGE_<SECTION>__<KEY>

# General
export ICLOUDBRIDGE_GENERAL__LOG_LEVEL=DEBUG

# Notes
export ICLOUDBRIDGE_NOTES__ENABLED=true
export ICLOUDBRIDGE_NOTES__REMOTE_FOLDER=~/Nextcloud/Notes
export ICLOUDBRIDGE_NOTES__USE_SHORTCUTS_FOR_PUSH=true
export ICLOUDBRIDGE_NOTES__CHECKLIST_SHORTCUT="CheckListBuilder"
export ICLOUDBRIDGE_NOTES__CONTENT_SHORTCUT="NoteContentBuilder"

# Reminders
export ICLOUDBRIDGE_REMINDERS__CALDAV_URL=https://nextcloud.example.com/remote.php/dav
export ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME=myuser

# Passwords
export ICLOUDBRIDGE_PASSWORDS__PROVIDER=vaultwarden  # or nextcloud
export ICLOUDBRIDGE_PASSWORDS__VAULTWARDEN_URL=https://vault.example.com
export ICLOUDBRIDGE_PASSWORDS__VAULTWARDEN_EMAIL=user@example.com
export ICLOUDBRIDGE_PASSWORDS__NEXTCLOUD_URL=https://cloud.example.com
export ICLOUDBRIDGE_PASSWORDS__NEXTCLOUD_USERNAME=your_username
```

**Priority**: Environment variables > config.toml > defaults

**Notes-specific variables**:
- `USE_SHORTCUTS_FOR_PUSH` - Enable/disable Shortcut pipeline (default: true)
- `CHECKLIST_SHORTCUT` - Name of checklist builder shortcut (default: "CheckListBuilder")
- `CONTENT_SHORTCUT` - Name of content builder shortcut (default: "NoteContentBuilder")

---

## Security Best Practices

### Credentials Storage

✅ **DO**:
- Use system keyring for passwords (macOS Keychain)
- Use `set-password` / `set-vaultwarden-credentials` commands
- Store only URLs and usernames in config files

❌ **DON'T**:
- Store passwords in config files
- Store passwords in environment variables
- Commit config files with credentials to git

### CSV Files

⚠️ **Apple Passwords CSV files contain plaintext passwords!**

**Always**:
1. Delete CSV files immediately after use
2. Check file permissions (should be 0600)
3. Don't commit CSV files to version control
4. Don't send CSV files via email/chat

```bash
# Secure deletion (macOS)
rm -P passwords.csv

# Or use secure delete tool
srm passwords.csv
```

---

## Troubleshooting

### Common Issues

**"AppleScript is not allowed to control Notes"**
```bash
# Grant permissions:
# System Settings → Privacy & Security → Automation
# Enable: Terminal (or your app) → Notes
```

**"EventKit access denied"**
```bash
# Grant permissions:
# System Settings → Privacy & Security → Reminders
# Enable: Terminal (or your app)
```

**"CalDAV authentication failed"**
```bash
# Verify credentials
icloudbridge reminders status

# Reset and re-enter password
icloudbridge reminders delete-password
icloudbridge reminders set-password
```

**"VaultWarden authentication failed"**
```bash
# Verify URL and credentials
icloudbridge passwords-status

# Reset and re-enter credentials
icloudbridge passwords-delete-vaultwarden-credentials
icloudbridge passwords-set-vaultwarden-credentials
```

### Debug Mode

Enable debug logging for troubleshooting:

```bash
icloudbridge --log-level DEBUG notes sync
```

Or set in config:
```toml
[general]
log_level = "DEBUG"
```

---

## Examples

### Complete Setup: Notes

```bash
# 1. Create config
icloudbridge config --init

# 2. Edit config or set env vars
export ICLOUDBRIDGE_NOTES__REMOTE_FOLDER=~/Nextcloud/Notes

# 3. Preview first sync
icloudbridge notes sync --dry-run

# 4. Run sync (skip deletions for safety)
icloudbridge notes sync --skip-deletions

# 5. Check status
icloudbridge notes status
```

### Complete Setup: Reminders

```bash
# 1. Set environment variables
export ICLOUDBRIDGE_REMINDERS__CALDAV_URL=https://nextcloud.example.com/remote.php/dav
export ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME=myuser

# 2. Store password securely
icloudbridge reminders set-password

# 3. List available calendars
icloudbridge reminders list

# 4. Preview sync
icloudbridge reminders sync --dry-run

# 5. Run auto-sync (all calendars)
icloudbridge reminders sync

# 6. Check status
icloudbridge reminders status
```

### Complete Setup: Passwords (VaultWarden)

```bash
# 1. Store VaultWarden credentials
icloudbridge passwords-set-vaultwarden-credentials
# Enter: URL, email, password

# 2. Export from Apple Passwords
# Settings → Passwords → Export → passwords.csv

# 3. Run sync
icloudbridge passwords-sync --apple-csv ~/Downloads/passwords.csv

# 4. Import generated CSV to Apple (if any new entries)
# Passwords app → File → Import Passwords

# 5. Delete CSV files
rm ~/Downloads/passwords.csv
rm ~/Library/Application\ Support/iCloudBridge/apple-import.csv

# 6. Check status
icloudbridge passwords-status
```

---

## Support

- **Documentation**: [DEVELOPMENT.md](../DEVELOPMENT.md)
- **Issues**: https://github.com/keithvassallomt/icloudbridge/issues
- **Website**: https://taskbridge.app/
- **Author**: Keith Vassallo <keith@vassallo.cloud>

---

**License**: GPL-3.0-or-later
**Platform**: macOS only (requires AppleScript/EventKit)
**Python**: 3.11+
