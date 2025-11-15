# iCloudBridge

A modern application for synchronizing Apple Notes, Reminders, Photos and Passwords to cross-platform services.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## Overview

iCloudBridge is a complete rewrite of [TaskBridge](https://github.com/keithvassallomt/taskbridge), designed to be simpler, more maintainable, and built with modern Python practices. It synchronizes your Apple Notes and Reminders with services like NextCloud, CalDAV servers, and local folders.

### Key Features

- **Notes Synchronization**: Export Apple Notes to Markdown format
- **Reminders Synchronization**: Export Apple Reminders to CalDAV (VTODO)
- **Bidirectional Sync**: Keep changes in sync between Apple and remote services
- **Modern Architecture**: API-first design with FastAPI
- **Clean CLI**: Beautiful command-line interface built with Typer
- **Type Safe**: Full type hints and Pydantic validation
- **Async-First**: Concurrent operations for better performance

### What's Different from TaskBridge?

- **60-70% less code** - Simpler architecture and fewer moving parts
- **EventKit for Reminders** - Native API access instead of AppleScript parsing
- **FastAPI** - Clean separation between API and consumers (CLI now, GUI later)
- **Modern Python** - 3.11+ with async/await, type hints, and Pydantic
- **Simpler sync** - Bidirectional only (for now)

## Installation

### Requirements

- **macOS** (required - uses Apple Notes.app and Reminders.app)
- **Python 3.11+**
- **Poetry** for dependency management

### From Source

```bash
# Clone the repository
git clone https://github.com/keithvassallomt/icloudbridge.git
cd icloudbridge

# Install with Poetry
poetry install

# Activate the virtual environment
poetry shell

# Run iCloudBridge
icloudbridge --help
```

### From PyPI (Coming Soon)

```bash
pip install icloudbridge
```

## Quick Start

### 1. Check Version and Health

```bash
# Show version information
icloudbridge version

# Check application health
icloudbridge health
```

### 2. Configure

Create a configuration file at `~/.icloudbridge/config.toml`:

```toml
[general]
log_level = "INFO"

[notes]
enabled = true
remote_folder = "~/NextCloud/Notes"

[notes.folders]
"Personal" = { enabled = true }
"Work" = { enabled = true }

[reminders]
enabled = true
caldav_url = "https://nextcloud.example.com/remote.php/dav"
caldav_username = "user@example.com"

[reminders.lists]
"Reminders" = { enabled = true, calendar = "Tasks" }
```

Or view your current configuration:

```bash
icloudbridge config --show
```

### 3. Synchronize

```bash
# Sync notes (rich export is now the default source)
icloudbridge notes sync

# Sync notes and also export a read-only RichNotes/ snapshot
icloudbridge notes sync --rich-notes

# Sync notes (dry run)
icloudbridge notes sync --dry-run

# Sync reminders
icloudbridge reminders sync

# List note folders
icloudbridge notes list

# List reminder lists
icloudbridge reminders list
```

Every notes sync now stages content through the Ruby-based rich-notes ripper, which copies
`NoteStore.sqlite` via `tools/note_db_copy/copy_note_db.py`. Make sure that helper script has Full
Disk Access on macOS; the legacy plain AppleScript extraction flow has been removed. Apple Notes'
"Recently Deleted" system folder is automatically ignored and cannot be synced. If you want
Markdown checklists (`- [ ] Task`) to stay as true Apple Notes checklists when syncing back, install
the `CheckListBuilder` Shortcut (or point `ICLOUDBRIDGE_NOTES__CHECKLIST_SHORTCUT` to your own) plus
the companion `NoteContentBuilder` Shortcut (override via
`ICLOUDBRIDGE_NOTES__CONTENT_SHORTCUT`). iCloudBridge will route checklist blocks through
CheckListBuilder, feed the rest of the note through NoteContentBuilder, splice the RTF output
together, and hand the combined RTF to Apple Notes. The macOS `shortcuts` CLI must be available for
both shortcuts.

## Architecture

iCloudBridge follows a clean, modular architecture:

```
icloudbridge/
├── api/          # FastAPI application (for future GUI)
├── core/         # Business logic and domain models
├── sources/      # Data source adapters (Apple, CalDAV, Markdown)
├── cli/          # Command-line interface
└── utils/        # Shared utilities
```

### Key Design Decisions

1. **Hybrid Apple Integration**
   - **EventKit** for Reminders (native API, no parsing)
   - **AppleScript** for Notes (only option available)

2. **API-First Design**
   - CLI calls API layer directly (no HTTP initially)
   - Future GUI can call same API over HTTP
   - Clean separation of concerns

3. **Simplified Sync**
   - Bidirectional only (newest wins)
   - Minimal state tracking (SQLite)
   - Timestamp-based conflict resolution

## Configuration

### Environment Variables

All configuration can be overridden with environment variables:

```bash
export ICLOUDBRIDGE_GENERAL__LOG_LEVEL=DEBUG
export ICLOUDBRIDGE_NOTES__ENABLED=true
export ICLOUDBRIDGE_NOTES__REMOTE_FOLDER=~/Notes
export ICLOUDBRIDGE_REMINDERS__CALDAV_URL=https://example.com/dav
```

### Passwords

CalDAV passwords are stored securely in the macOS Keychain via the `keyring` library.

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/keithvassallomt/icloudbridge.git
cd icloudbridge

# Install dependencies (including dev dependencies)
poetry install

# Activate virtual environment
poetry shell

# Run tests
pytest

# Run linter
ruff check .

# Format code
ruff format .
```

### Project Structure

See [docs/implementation_plan.md](docs/implementation_plan.md) for detailed architecture and implementation plan.

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=icloudbridge --cov-report=html

# Run specific test file
pytest tests/test_models.py
```

### Code Quality

We use `ruff` for linting and formatting:

```bash
# Check code
ruff check .

# Format code
ruff format .

# Fix auto-fixable issues
ruff check --fix .
```

## Roadmap

### Phase 1: Core Foundation ✅ (Current)
- [x] Project setup
- [x] Core models
- [x] Configuration management
- [x] Basic CLI

### Phase 2: Notes Implementation (In Progress)
- [ ] AppleScript notes adapter
- [ ] Markdown folder adapter
- [ ] Notes sync logic
- [ ] CLI commands

### Phase 3: Reminders Implementation
- [ ] EventKit reminders adapter
- [ ] CalDAV adapter
- [ ] Reminders sync logic
- [ ] CLI commands

### Phase 4: API Layer
- [ ] FastAPI application
- [ ] Server mode (uvicorn)
- [ ] API documentation

### Phase 5: Polish & Testing
- [ ] Comprehensive tests
- [ ] Complete documentation
- [ ] PyPI package

### Future Enhancements
- [ ] GUI application (PyQt6)
- [ ] Scheduled sync (cron-like)
- [ ] Advanced conflict resolution
- [ ] Multiple remote destinations

## Known Limitations

### Notes
- Only image and URL attachments supported (Apple Notes limitation)
- **Checkable todo items convert to bullets** - Apple Notes does not expose checklist/TODO status via AppleScript or safe database access. For TODO tracking, use Apple Reminders instead (Phase 3). See [DEVELOPMENT.md](DEVELOPMENT.md#4-apple-notes-checkliststodo-items-cannot-be-synced) for detailed investigation.
- Modification dates reset on local updates

### Reminders
- No timezone support for alarms
- Reminder attachments not synchronized

## Support

- **Issues**: [GitHub Issues](https://github.com/keithvassallomt/icloudbridge/issues)
- **TaskBridge Documentation**: [docs.taskbridge.app](https://docs.taskbridge.app)

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run linter and tests
6. Submit a pull request

## License

GNU General Public License v3.0 or later (GPL-3.0-or-later)

See [LICENSE](LICENSE) for details.

## Acknowledgments

- Built as a simplified successor to [TaskBridge](https://github.com/keithvassallomt/taskbridge)
- Uses [FastAPI](https://fastapi.tiangolo.com/) for API layer
- Uses [Typer](https://typer.tiangolo.com/) for CLI
- Uses [PyObjC](https://pyobjc.readthedocs.io/) for native macOS integration

## Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

> iCloudBridge is in no way affiliated with or endorsed by Apple, Inc.
