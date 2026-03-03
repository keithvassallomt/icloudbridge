# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.1] - 2026-03-03 — "No Means No"

### Changed
- Preflight window now groups requirements into four categories: Essential, Notes Sync, Reminders Sync, and Photos Sync. Only essential requirements (Homebrew, Xcode CLT, Python, Ruby) block the daemon from starting. Service-specific permissions (Full Disk Access, Accessibility, Automation) are informational and no longer block startup.
- Preflight window persists permission states to `permissions.json` after each check cycle, allowing the backend and frontend to query which permissions have been granted.
- First-run wizard and Settings page now disable service toggles (Notes, Reminders, Photos) when required macOS permissions are missing, with a warning showing which permissions need to be granted.

### Fixed
- Recurring reminders (e.g. "Call Mum") were not syncing from CalDAV because the VALARM trigger parser crashed on relative durations (`-PT15M`). The code assumed `trigger.dt` was always a `datetime`, but for relative triggers it's a `timedelta`. This caused the entire TODO to fail parsing silently.
- Completed recurring reminders were incorrectly skipped during sync. The skip-completed filter now exempts reminders with recurrence rules, since future instances are still pending.
- Photo sync analysis performance drastically improved: replaced per-file AppleScript `whose` queries with a single bulk fetch of all Photos library filenames, parallelised file hashing/EXIF extraction with bounded concurrency, and added persistent DB connections during batch operations.
- Photo sync fast-path checks now verify `last_imported IS NOT NULL` before skipping files, fixing an issue where files recorded by migrations but never actually imported were permanently skipped.
- Preflight window version strings (Python, Ruby) no longer display trailing newlines from shell output.

---

*Entries below use an older format.*

## 2026-02-14 - Bidirectional Photo Sync
- **Added**: Bidirectional photo sync mode that imports photos from NextCloud/local folders into Apple Photos AND exports photos from Apple Photos back to the same folder.
  - Three sync modes available in Settings: `Import` (folder → Apple Photos), `Export` (Apple Photos → folder), and `Bidirectional` (both directions).
  - Unified Sync button behavior adapts based on configured mode - no separate Export section needed.
- **Added**: Apple Photos Library Reader for direct read-only access to Apple Photos SQLite database (`Photos.sqlite`).
  - Supports macOS Photos library structure with proper Core Data timestamp conversion.
  - Compatible with different macOS versions (handles schema variations like missing `ZFILESIZE` column).
  - Resolves file paths in the `originals` folder structure.
- **Added**: Photo Export Engine (`PhotoExportEngine`) for copying photos from Apple Photos to local folder.
  - Export modes: "Going Forward" (only new photos after enabling) or "Full Library" (export entire library).
  - Photos organized by date (e.g., `2026/02/photo.jpg`) or flat structure.
  - Hash-based deduplication prevents re-exporting photos that were originally imported from NextCloud.
  - Baseline date tracking ensures only new photos are exported in "Going Forward" mode.
  - Progress callbacks for real-time export status updates.
- **Improved**: Photos page UI shows mode-specific stats (import stats, export stats, or both for bidirectional).
- **Improved**: Settings page shows export configuration options (export mode, folder organization) when bidirectional mode is selected.
- **Fixed**: Simulation (dry run) now correctly shows "Would Export" count instead of "Exported: 0".
- **Fixed**: Scheduled photo syncs now respect sync_mode setting - runs import, export, or both based on configuration.

## 2026-02-14 - Photo Sync Performance & UI Improvements
- **Improved**: Photo sync now uses mtime-based fast-path deduplication to skip expensive SHA-256 hashing for unchanged files. Files are matched by path + size + mtime before falling back to hash comparison, dramatically speeding up subsequent syncs (20,853 of 20,854 files skipped in testing).
- **Fixed**: Initial scan files now correctly marked as imported. Previously, files discovered during the first-run wizard had `last_imported = NULL`, causing the "Library Items" count to show only files imported via manual sync rather than all tracked files.
- **Added**: One-time migration (`mark_all_imported_v1`) to fix existing databases where initial scan files weren't marked as imported.
- **Added**: One-time migration (`new_files_catchup_v1`) to add files present in source folders but missing from the database (assumes they're already in Apple Photos via iCloud sync).
- **Improved**: Photo sync UI now shows clearer metrics with tooltips:
  - "Library Items" - total photos/videos tracked in source folders
  - "Last Imported" - photos imported in the most recent sync
  - "Skipped" - photos skipped because they already exist in Apple Photos
- **Improved**: CLI photo sync output now shows consistent stats including "Skipped (fast-path)" for mtime matches and "Skipped (in Photos)" for existing duplicates.

## 2026-02-03 - Reminders Sync with Third-Party Apps & Upgrade Fixes
- **Fixed**: Reminders created in third-party apps (like Planify) that sync to NextCloud would repeatedly overwrite local changes in Apple Reminders. The issue occurred because these apps don't include a `LAST-MODIFIED` timestamp in their iCalendar data, causing the sync to incorrectly treat remote items as "just modified." Now falls back to `DTSTAMP` when `LAST-MODIFIED` is missing.
- **Fixed**: App upgrades now properly regenerate the Python environment. Previously, upgrading from one version to another (e.g., 0.1.7 → 0.1.8) could leave stale package metadata in the venv, causing the app to report the old version number. The venv fingerprint now includes pyproject.toml so any version bump triggers a fresh install.
- **Improved**: Password sync simulation now shows action badges (New/Update/Delete) for each password entry. Previously, the expanded details only showed item names without indicating what action would be taken. Updated and deleted entries are now included in the entry list for better visibility.
- **Fixed**: Password sync simulation no longer writes to the database. Previously, running a simulation would import the Apple CSV to the local database, causing subsequent simulations to show different results.
- **Fixed**: Passwords created in VaultWarden (not Apple) are no longer incorrectly marked for deletion during sync. The conflict resolution now detects provider-originated passwords and correctly marks them for creation in Apple instead of deletion from the provider.

## 2026-02-02 - Reminders Completion Sync Fix
- **Fixed**: Resolved an issue where marking a reminder as complete in Nextcloud would fail to sync the completion status to Apple Reminders. The sync would repeatedly fail with an error when the reminder had no alarms or recurrence rules.

## 2026-01-24 - Reminders Sync Deduplication & Completed Filter
- **Added**: Bootstrap reconciliation for reminders sync on fresh setup. When the database is empty (new install or after reset), reminders are matched by title + due date to avoid creating duplicates when the same reminder exists on both Apple and CalDAV.
- **Added**: Completed reminders filter. New completed reminders that aren't already tracked in the database are skipped during sync, preventing historical completed items from flooding the sync on fresh setup. Already-mapped reminders continue to sync completion status changes.
- **Fixed**: Timestamp comparison now uses a 2-second tolerance to account for CalDAV's lack of microsecond precision. Previously, reminders that were in sync would show as needing updates due to sub-second timestamp differences.
- **Fixed**: CLI bug where `icloudbridge reminders sync` in manual mode would fail with "cannot access local variable 'apple_calendar'" due to a Python closure issue in the nested async function.
- **Fixed**: Settings page now correctly displays the Nextcloud URL for reminders. The backend now derives `reminders_nextcloud_url` from the CalDAV URL by stripping `/remote.php/dav`.

## 2026-01-21 - Enable SSL verification against local keystore, or disabling SSL verification
- **Added**: Truststore integration for CalDAV and Passwords (Vaultwarden/Nextcloud) clients to honor macOS system keychain certificates when SSL verification is enabled.
- **Added**: Per-service SSL verification toggles (`reminders_caldav_ssl_verify_cert`, `passwords_ssl_verify_cert`) exposed via config/env, CLI, and UI (first-run wizard + Settings).
- **Added:** Frontend surfaces “Verify SSL certificates” switches with warnings; disabling bypasses verification for self-signed local servers.
- **Fixed:** Backend persists SSL verify choices to config and propagates to CalDAV/Vaultwarden/Nextcloud HTTP clients; HTTPX/CalDAV now pass custom verify flags and use truststore when available.


## 2026-01-16 - Bug Fixes and Improvements
- **Fixed**: Resolved an issue where new notes created remotely would not be created in Apple Notes. 
- **Fixed**: Addressed some npm package vulnerabilities.
- **Improved**: Upgraded Ruby flow to 4.x.
- **Improved**: Updated developer instructions to handle new Ruby version.

## 2025-12-18 - Support for CalDAV SSL Options
-- **Added**: CalDAV SSL options now support system keychain trust via `truststore`, and you can disable certificate verification via config, CLI, or GUI (Settings and first-run wizard) for self-signed setups.

## 2025-12-14 - Various Bug Fixes
- **Fixed**: Fix integer overflow in reminders by adding timestamp bounds checking and handling invalid years from NSDateComponents.
-- **Fixed**: Fixes issue where due dates without a time showed as midnight UTC.
-- **Fixed**: Fixes issue where deleting a note in Apple Notes syncs it back from markdown.
-- **Fixed**: Added support for Apple Notes dates in various languages.
-- **Improved**: **EXPERIMANTAL** support for sub-folders in Notes has been added. Please report any issues.

## 2025-12-07 - Password Sync Issues
- **Fixed**: Incorporated fix from @geneccx for recurring reminder sync - thanks!
- **Fixed**: Two-way password deletion sync between Apple Passwords and remote - deleting a password from Apple now correctly deletes it from remote (moved to trash), and vice versa.
- **Fixed**: Password updates now sync between Apple Passwords and remote - updating a password from Apple now correctly updates it from remote and vice versa.

## 2025-11-27 - More Bug fixes
- Major update to password sync. Bitwarden/Vaultwarden now support login via client id and client secret. This gets Bitwarden sync working again and future-proofs Vaultwarden. Updates made to backend, CLI, first-run wizard and settings page. 
- Updated text of reminder sync setting screen and first-run wizard to be less confusing. 

## 2025-11-26 - Bug fixes
- Added Xcode Development tools check to preflight window. 
- Centralised permission requests into AppBundle info.plist.
- Fixed problem with Apple Notes permission request. 
- Fixed problem with Accessibility permission request.
- Centralised version numbering. 
- Fixed missing login helper.

## 2025-11-15 – Nextcloud Passwords + UX polish
- Hardened the Nextcloud passwords provider so `folder/create`, `password/create`, and `password/update`
  gracefully fall back to legacy endpoints when instances return HTTP 405. This keeps bulk imports
  running even on servers that only expose the older `/password` route.
- Frontend now treats Bitwarden/Vaultwarden and Nextcloud as first-class options: the wizard copy,
  settings page, and dedicated Passwords UI switch wording/actions based on the selected provider, and
  expose a clear warning about Nextcloud's missing OTP/passkey support.
- Added an unsaved-changes guard + modal on Settings, an icon-only theme picker that honours system
  mode, and updated the first-run wizard welcome screen to highlight photo sync.
- Created a shared `ServiceDisabledNotice` card used by Notes/Reminders/Photos/Passwords so disabled
  states match copy and styling, and the dashboard no longer shows Notes setup warnings when the
  service is off.

## 2025-11-12 – Photo Sync Implementation
- Implemented **hash-based photo sync** for importing photos/videos from local folders to Apple Photos.
  Content-addressed deduplication uses SHA-256 hashes (not filenames) to prevent duplicate imports even
  when files are renamed or moved.
- Built complete pipeline: scanner (`icloudbridge/sources/photos/scanner.py`), AppleScript adapter
  (`icloudbridge/sources/photos/applescript.py`), SQLite tracking database (`icloudbridge/utils/photos_db.py`),
  and sync engine (`icloudbridge/core/photos_sync.py`).
- Enhanced metadata capture: extracts EXIF timestamps from images (falls back to mtime for videos), stores
  Apple Photos local identifiers for reconciliation, writes JSON sidecars with full import metadata.
- Integrated into scheduler, CLI, and API with full UI support. Photos can be synced via web UI with
  real-time progress tracking, dry-run simulation, and sync history.
- Frontend Photos page (`frontend/src/pages/Photos.tsx`) displays status (imported/pending counts), sync
  controls, and history with badge showing number of items imported per sync.
- Fixed empty album name handling, sync history clearing on reset, and pending count logic to only show
  photos not yet imported to Apple Photos.

## 2025-11-10 – Password Sync + Bulk Import UI/API Parity
- Finished the **Vaultwarden password sync** end-to-end: the shared `VaultwardenAPIClient` now mirrors
  Bitwarden’s encryption flow (master key derivation, user key unwrap, AES-CBC+HMAC cipher strings). CLI
  and API both authenticate directly against `/identity/connect/token`, decrypt `/api/sync`, and encrypt
  everything pushed back to `/api/ciphers`.
- Added optional **bulk import** support that packages encrypted ciphers + folder relationships for
  `/api/ciphers/import`. CLI exposes this behind `--bulk`, while the API uses bulk-mode by default. Folder
  tags (e.g., `#icb_ICE`) survive because we send encrypted folder metadata alongside each batch.
- Frontend passwords page now wires the Upload → Simulate/Sync, Export, and Import buttons to the new
  API endpoints with bulk mode enabled. Results surface the backend’s rich status block (created/skipped,
  pull downloads) and download links expire after five minutes per the API contract.
- Settings/API now store Vaultwarden credentials, and the API sync/history endpoints log bulk runs the
  same way as the CLI (ensuring “Simulate” does a read-only dry run, while “Sync” commits and records
  history entries). Passwords feature is considered **complete**.

## 2025-11-09 – Notes UI Folder Mapping Improvements
- **Enhanced folder mapping UX** in Notes page with collapsible panel and toggle control. Panel is now
  collapsed and disabled by default, preventing accidental activation of manual mapping mode.
- Added "Enable manual folder mapping" toggle that expands the panel and enables manual folder configuration.
  Toggle automatically enables if existing mappings are found in config, ensuring smooth migration.
- **Fixed "All folders are mapped" bug** where nested Apple Notes folders (e.g., "iCloud/Work") weren't
  displayed as unmapped. Root folder detection now uses actual tree roots instead of filtering by level,
  properly handling folders at any nesting level whose parents don't exist in the folder map.
- Migration warning now only displays when manual mapping is enabled but no mappings have been saved yet,
  reducing confusion during initial setup.
- Files modified: `frontend/src/pages/Notes.tsx`, `frontend/src/components/FolderMappingTable.tsx`

## 2025-11-08 – API Feature Parity & Documentation Overhaul
- **Achieved 100% API-CLI feature parity** for Notes sync functionality. The API now offers all capabilities
  available through the CLI, closing 4 critical gaps identified in the comprehensive gap analysis.
- Added **rich notes export support** to the API via new `rich_notes_export: bool` parameter in
  `NotesSyncRequest`. When enabled, the API exports a read-only snapshot to `RichNotes/` folder after sync,
  matching CLI behavior with `--rich-notes` flag.
- Implemented **shortcut pipeline control per-request** via `use_shortcuts: bool | None` parameter. API users
  can now override the pipeline preference: `None` uses config default, `True` forces Shortcut pipeline,
  `False` forces classic AppleScript pipeline. Response metadata includes `pipeline_used` field.
- Added **all-folders sync support** to API. Setting `folder: null` in the request triggers automatic discovery
  and sync of all Apple Notes folders (previously required separate API calls per folder). Returns aggregated
  statistics and per-folder results with error handling for partial failures.
- Created new **system utilities router** (`/api/system/`) with two endpoints:
  - `GET /api/system/db-paths` - Returns all database file locations with existence metadata
  - `GET /api/system/info` - Returns system and application information (version, platform, Python version)
- **Documentation updates**:
  - Created comprehensive `debug/instructions.md` (500+ lines) with detailed implementation guide, code
    examples, testing checklists, and migration path for existing API users.
  - Enhanced `docs/USAGE.md` with new sections: Rich Notes Export, Attachment Handling, complete API Server
    documentation with curl examples for all endpoints, WebSocket usage examples (JavaScript + Python),
    environment variables for Shortcut configuration, and `db-paths` command documentation.
- All API changes are **backwards-compatible** with sensible defaults. Existing API integrations continue
  working unchanged while gaining access to new optional parameters.
- Refactored `icloudbridge/api/routes/notes.py` sync endpoint to create engine per-request with pipeline
  override, handle all-folders logic with aggregation, and integrate rich notes export with proper error
  handling.
- Updated `icloudbridge/api/models.py` with enhanced `NotesSyncRequest` model including Field descriptions
  for auto-generated API documentation.
- Registered system router in `icloudbridge/api/app.py` for automatic inclusion in OpenAPI docs at `/api/docs`.

## 2025-11-07 – Title & Line-Break Preservation
- Restored first-level Markdown headings end-to-end so titles survive round-trips and filenames remain
  stable (`utils/converters.py`, `sources/notes/markdown.py`).
- Enabled Markdown-It soft breaks and post-processed paragraphs into `<br>` for the AppleScript path so
  single newlines/blank lines look the same inside Apple Notes.
- Added `add_markdown_soft_breaks()` plus `insert_markdown_blank_line_markers()` so Shortcut-driven notes
  keep their spacing; Shortcut payloads now include a blank line between metadata and content.
- Checklist Shortcut path now strips the top-level `# Title` before rebuilding the note so Apple Notes
  doesn’t duplicate the heading on recreation (`utils/converters.py:strip_leading_heading`,
  `core/sync.py`).
- Refined rich-note newline handling: Shortcut payloads now leave single `\n` untouched and turn blank lines
  into literal `<p>` markers, matching the exact spacing Apple Notes expects without extra heuristics.
- Shortcut pipeline is now the default for **all** markdown → Apple pushes (not just checklist notes). A new
  config flag (`notes.use_shortcuts_for_push`) and CLI override (`--shortcut-push/--classic-push`) let users
  fall back to the AppleScript HTML path when needed.
- The ripper cache now refreshes at the start of every folder sync, and we clear it whenever the Shortcut
  pipeline recreates a note. A short retry loop (with backoff) kicks in after each Shortcut upsert to give
  Apple Notes time to flush the new UUID before the ripper recaptures, eliminating the "rich note body
  missing" fallback. We also retry inside the capture path itself whenever a UUID is missing (logging when
  the fallback kicks in) so we copy the NoteStore again before conceding defeat.

## 2025-11-05 – Reminders Sync Telemetry & Retry Fixes
- Added verbose logging and per-sync log files under `~/.icloudbridge/log/reminder_sync/` to capture
  CalDAV failures with stack traces (`core/reminders_sync.py`, `sources/reminders/caldav_adapter.py`).
- Updated the DB strategy so failed CalDAV updates still bump `last_sync`, preventing infinite retries.
- Improved CLI messaging during remote reminder creation errors.

## 2025-11-08 – Attachment Sidecars & Metadata Cleanup
- Rich-notes capture now copies the entire Notes container and feeds the ripper via `--mac`, which lets us
  resolve attachment file paths reliably for every note (including previews/thumbnails).
- `NotesAdapter` surfaces attachment records (UUID + file path) with basic image detection so Apple →
  Markdown exports can rewrite `data:` URIs into local files and copy the binaries into `.attachments.<slug>/`.
- `MarkdownAdapter` writes and reads hidden JSON sidecar files (`.<note>.md.meta.json`) to store attachment
  slugs (and future metadata), removing the old `<!-- icloudbridge-metadata ... -->` comment block that
  Nextcloud rendered inline.
- Sync engine now rewrites Apple HTML to reference per-note attachment folders, converts inline base64
  images to files, keeps sidecar slugs stable across rename/create flows, and cleans up temp blobs after
  each push.
- Updated docs/instructions so manual testers know to look for hidden metadata files and ensure there are
  actual changes before requesting a sync.

## 2025-11-22 – Preflight & runtime hardening
- Menubar preflight now respects the "Don't show this next time" toggle correctly, keeps the app alive when closing the window, and only suppresses when all prerequisites are satisfied. Added a “Show Initial Setup” menu item to reopen the preflight window on demand.
- Accessibility check runs on the main thread and re-checks after prompting so it no longer sticks on “Checking…”. Preflight quit now cleans up any backend bound to port 27731.
- Backend launch guards against double-starts via health checks and no longer kills existing processes pre-emptively; we also preserve running backends when prerequisites are healthy.
- Python/Ruby runtime installer wiring: menubar sets `ICLOUDBRIDGE_VENV_PYTHON` and calls the managed venv interpreter for rich-notes copy (`copy_note_db.py`), with env scrubbed to avoid `/usr/bin/python3` bleed-through. Added explicit logging and fallbacks for venv path resolution.
- Bundling fixes: the macOS app now ships the full `tools/` directory (including `note_db_copy` and `notes_cloud_ripper`) inside `Contents/Resources/backend_src/` so the ripper helpers exist at runtime.
- Logging tightened: rich-notes capture/export now emit interpreter/command/env details and log exceptions at source; sync pipeline uses `logger.exception` for folder failures to surface stack traces in `~/Library/Logs/iCloudBridge/icloudbridge.log`.
