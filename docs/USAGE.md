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
| `--config` | `-c` | Path to configuration file | `~/Library/Application Support/iCloudBridge/config.toml` |
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

**Creates**: `~/Library/Application Support/iCloudBridge/config.toml`

**Example config**:
```toml
[general]
log_level = "INFO"
data_dir = "~/Library/Application Support/iCloudBridge"

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
vaultwarden_url = "https://vault.example.com"
vaultwarden_email = "user@example.com"
# Credentials stored in system keyring - use: icloudbridge passwords-set-vaultwarden-credentials
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

**Examples**:

```bash
# Sync all notes
icloudbridge notes sync

# Preview changes without syncing
icloudbridge notes sync --dry-run

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
  Database: ~/Library/Application Support/iCloudBridge/notes.db
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
Database: ~/Library/Application Support/iCloudBridge/reminders.db
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

Semi-automated sync between Apple Passwords and VaultWarden via API.

### Setup (One-Time)

#### 1. Store VaultWarden Credentials

```bash
icloudbridge passwords-set-vaultwarden-credentials
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
   icloudbridge passwords-sync --apple-csv <path/to/passwords.csv>
```

**Storage**: macOS Keychain (secure, no plaintext in config)

---

### `passwords-sync`

Full auto-sync: Apple → VaultWarden (push) and VaultWarden → Apple (pull).

```bash
icloudbridge passwords-sync --apple-csv PASSWORDS.CSV [OPTIONS]
```

**Options**:

| Option | Short | Type | Description | Default |
|--------|-------|------|-------------|---------|
| `--apple-csv` | | PATH | Apple Passwords CSV export | **Required** |
| `--output` | `-o` | PATH | Output path for Apple CSV | `data_dir/apple-import.csv` |

**Complete Workflow**:

```bash
# 1. Export from Apple Passwords
# Settings → Passwords → ⚙️ → Export Passwords → passwords.csv

# 2. Run full auto-sync
icloudbridge passwords-sync --apple-csv ~/Downloads/passwords.csv

# 3. Import generated CSV to Apple Passwords (if new entries found)
# Passwords app → File → Import Passwords → apple-import.csv

# 4. Delete CSV files (security)
rm ~/Downloads/passwords.csv
rm ~/Library/Application\ Support/iCloudBridge/apple-import.csv
```

**Output**:
```
🔐 Password Full Auto-Sync

Authenticating with VaultWarden...

============================================================
📤 Apple → VaultWarden (Push)
============================================================
Created                    5
Updated                    12
Skipped (unchanged)        2135

============================================================
📥 VaultWarden → Apple (Pull)
============================================================
✅ Generated Apple CSV with 3 new entries
   File: ~/Library/Application Support/iCloudBridge/apple-import.csv

⚠️  Manual step required:
   1. Open Passwords app
   2. File → Import Passwords
   3. Select: ~/Library/Application Support/iCloudBridge/apple-import.csv
   4. Delete CSV file after import

============================================================
✅ Sync complete in 5.2s
============================================================

⚠️  SECURITY REMINDER
   Delete CSV files after import:
   → rm ~/Downloads/passwords.csv
   → rm ~/Library/Application Support/iCloudBridge/apple-import.csv
```

**What Gets Automated**:
- ✅ Apple → VaultWarden: Direct API push (no manual import!)
- ✅ Change detection (only updates what changed)
- ✅ Deduplication
- ✅ Hash-based change tracking

**What Remains Manual** (Apple limitations):
- ❌ Apple Passwords export (must use Settings app)
- ❌ Apple Passwords import (must use Passwords app)

---

### Alternative: Manual CSV Workflow

For users without VaultWarden API access, use CSV-based workflow:

#### `passwords-import-apple`

Import passwords from Apple Passwords CSV export.

```bash
icloudbridge passwords-import-apple PASSWORDS.CSV
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
   → icloudbridge passwords-export-bitwarden -o bitwarden.csv --apple-csv ~/Downloads/passwords.csv
```

#### `passwords-export-bitwarden`

Generate Bitwarden-formatted CSV for import.

```bash
icloudbridge passwords-export-bitwarden -o OUTPUT.CSV --apple-csv APPLE_CSV
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

Database: ~/Library/Application Support/iCloudBridge/passwords.db
```

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

## Utility Commands

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
| Config | `~/Library/Application Support/iCloudBridge/config.toml` |
| Notes DB | `~/Library/Application Support/iCloudBridge/notes.db` |
| Reminders DB | `~/Library/Application Support/iCloudBridge/reminders.db` |
| Passwords DB | `~/Library/Application Support/iCloudBridge/passwords.db` |
| Logs | `~/Library/Application Support/iCloudBridge/logs/` |

### Environment Variables

All config options can be overridden with environment variables:

```bash
# Format: ICLOUDBRIDGE_<SECTION>__<KEY>
export ICLOUDBRIDGE_GENERAL__LOG_LEVEL=DEBUG
export ICLOUDBRIDGE_NOTES__ENABLED=true
export ICLOUDBRIDGE_NOTES__REMOTE_FOLDER=~/Nextcloud/Notes
export ICLOUDBRIDGE_REMINDERS__CALDAV_URL=https://nextcloud.example.com/remote.php/dav
export ICLOUDBRIDGE_REMINDERS__CALDAV_USERNAME=myuser
export ICLOUDBRIDGE_PASSWORDS__VAULTWARDEN_URL=https://vault.example.com
export ICLOUDBRIDGE_PASSWORDS__VAULTWARDEN_EMAIL=user@example.com
```

**Priority**: Environment variables > config.toml > defaults

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
