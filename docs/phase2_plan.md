# Phase 2: Notes Implementation - Detailed Plan

## Overview

This document outlines the simplified approach for implementing Notes sync in iCloudBridge, learning from TaskBridge but dramatically simplifying the architecture.

## Key Learnings from TaskBridge

### What TaskBridge Does (Complex Approach):

1. **Staged File System** ([notescript.py:6-43](../../TaskBridge/taskbridgeapp/notes/model/notescript.py#L6-L43))
   - AppleScript exports notes to temporary `.staged` files
   - Format: `UUID~~Name~~CreationDate~~ModifiedDate\n~~START_ATTACHMENTS~~\n...`
   - Python then parses these staged files
   - **Problem**: Extra complexity, temp file management, parsing errors

2. **Heavy Image Processing** ([note.py:328-465](../../TaskBridge/taskbridgeapp/notes/model/note.py#L328-L465))
   - Base64 encoding/decoding of images
   - Multiple file copies (staged → local → remote)
   - Complex attachment tracking
   - **Problem**: Slow, memory intensive, error-prone

3. **Dual HTML/Markdown Storage** ([note.py:22-57](../../TaskBridge/taskbridgeapp/notes/model/note.py#L22-L57))
   - Notes store BOTH `body_html` and `body_markdown`
   - Conversion happens multiple times
   - **Problem**: Data duplication, sync confusion

4. **Complex Sync Logic** ([controller.py:152-189](../../TaskBridge/taskbridgeapp/notes/model/controller.py#L152-L189))
   - Multiple phases: folder deletions → associations → note deletions → note sync
   - Lots of state tracking
   - **Problem**: Hard to debug, many failure points

### What We Can Simplify:

1. **Direct AppleScript** - Parse note data directly from AppleScript output, no staged files
2. **Lazy Image Handling** - Only copy images when needed, use file references
3. **Single Source of Truth** - Store notes in one format, convert on-the-fly
4. **Simplified Sync** - Single-pass bidirectional sync with timestamp comparison

---

## iCloudBridge Architecture

### Directory Structure

```
icloudbridge/
├── sources/
│   ├── notes/
│   │   ├── __init__.py
│   │   ├── applescript.py      # AppleScript adapter (NEW)
│   │   └── markdown.py          # Markdown folder adapter (NEW)
│   └── ...
├── utils/
│   ├── __init__.py
│   ├── converters.py            # HTML ↔ Markdown conversion (NEW)
│   └── db.py                    # SQLite helpers (NEW)
└── ...
```

### File Breakdown

#### 1. `icloudbridge/sources/notes/applescript.py`

**Purpose**: Interface with Apple Notes via AppleScript

**Key Classes**:
- `AppleScriptNote`: Represents a note from Apple Notes
- `AppleScriptFolder`: Represents a folder in Apple Notes
- `NotesAdapter`: Main adapter for all AppleScript operations

**Simplifications vs TaskBridge**:
- **No staged files** - Parse AppleScript output directly
- **Simpler metadata** - Just UUID, name, dates, body HTML
- **Attachment URLs only** - Don't extract base64 immediately

**AppleScript Snippets to Use**:

```applescript
# List Folders (use TaskBridge's load_folders_script)
tell application "Notes"
    set output to ""
    set n_folders to get every folder
    repeat with n_folder in n_folders
        set folder_id to id of n_folder
        set folder_name to name of n_folder
        set output to output & folder_id & "~~" & folder_name & "|"
    end repeat
    return output
end tell

# Get Notes from Folder (SIMPLIFIED - no staged files)
on run argv
    set folder_name to item 1 of argv
    tell application "Notes"
        set myFolder to first folder whose name = folder_name
        set myNotes to notes of myFolder
        set output to ""
        repeat with theNote in myNotes
            set nId to id of theNote
            set nName to name of theNote
            set nBody to body of theNote
            set nCreation to creation date of theNote
            set nModified to modification date of theNote

            # Simple format: ID|Name|Creation|Modified|Body
            # Use | as delimiter since ~~ appears in body
            set noteData to nId & "|||" & nName & "|||" & nCreation & "|||" & nModified & "|||" & nBody
            set output to output & noteData & "~~~NEXT_NOTE~~~"
        end repeat
        return output
    end tell
end run

# Create/Update/Delete - reuse TaskBridge scripts with minor tweaks
```

**Methods**:

```python
class NotesAdapter:
    async def list_folders() -> list[AppleScriptFolder]:
        """List all note folders"""

    async def get_notes(folder_name: str) -> list[AppleScriptNote]:
        """Get all notes from a folder (simplified - no staging)"""

    async def create_note(folder_name: str, note: Note) -> datetime:
        """Create a note, return its modification date"""

    async def update_note(folder_name: str, note: Note) -> datetime:
        """Update a note, return its modification date"""

    async def delete_note(folder_name: str, note_name: str) -> bool:
        """Delete a note"""
```

**Complexity**: ~150-200 lines (vs TaskBridge's ~500+ with staging)

---

#### 2. `icloudbridge/sources/notes/markdown.py`

**Purpose**: Read/write notes as Markdown files

**Key Classes**:
- `MarkdownNote`: Represents a note from markdown file
- `MarkdownAdapter`: Main adapter for markdown operations

**File Format** (same as TaskBridge):
```markdown
# Note Title

Content here...

![image](.attachments/uuid.png)
```

**Simplifications vs TaskBridge**:
- **No dual storage** - Only markdown on disk
- **Simpler attachments** - Just copy files, no base64 intermediate
- **Direct file operations** - Use aiofiles for async I/O

**Methods**:

```python
class MarkdownAdapter:
    def __init__(self, base_path: Path):
        self.base_path = base_path

    async def read_note(file_path: Path) -> MarkdownNote:
        """Read a markdown file into a Note"""

    async def write_note(note: Note) -> Path:
        """Write a Note to a markdown file"""

    async def delete_note(file_path: Path) -> bool:
        """Delete a markdown file"""

    async def list_notes(folder_path: Path) -> list[Path]:
        """List all .md files in folder"""

    async def copy_attachment(src: Path, note_name: str) -> Path:
        """Copy attachment to .attachments/ folder"""
```

**Complexity**: ~100-150 lines

---

#### 3. `icloudbridge/utils/converters.py`

**Purpose**: Convert between HTML and Markdown

**Libraries** (reuse TaskBridge's approach):
- `markdownify` - HTML → Markdown
- `markdown-it-py` - Markdown → HTML (TaskBridge uses markdown2, but markdown-it-py is already in deps)

**Functions**:

```python
def html_to_markdown(html: str) -> str:
    """Convert HTML from Apple Notes to Markdown"""
    # Use markdownify (same as TaskBridge)
    # Strip Apple's h1 title (first line)
    # Handle image tags

def markdown_to_html(markdown: str, attachments: list[Attachment] = None) -> str:
    """Convert Markdown to HTML for Apple Notes"""
    # Use markdown-it-py
    # Convert image references to file:// URLs
    # Add h1 title as first line (Apple Notes expects this)
```

**Simplifications**:
- No line-by-line processing (use library defaults)
- Handle images via regex replacement
- Let libraries do the heavy lifting

**Complexity**: ~50-75 lines

---

#### 4. `icloudbridge/utils/db.py`

**Purpose**: Minimal SQLite state tracking

**Schema** (already defined in implementation plan):

```sql
CREATE TABLE note_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_uuid TEXT NOT NULL,
    local_name TEXT NOT NULL,
    local_folder_uuid TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    last_sync_timestamp REAL NOT NULL,
    UNIQUE(local_uuid, remote_path)
);
```

**Functions**:

```python
class NotesDB:
    def __init__(self, db_path: Path):
        """Initialize database connection"""

    async def get_mapping(local_uuid: str) -> dict | None:
        """Get remote path for local UUID"""

    async def upsert_mapping(local_uuid: str, remote_path: Path, timestamp: float):
        """Create or update mapping"""

    async def delete_mapping(local_uuid: str):
        """Delete mapping"""

    async def get_all_mappings() -> list[dict]:
        """Get all mappings (for orphan detection)"""
```

**Complexity**: ~80-100 lines

---

#### 5. `icloudbridge/core/sync.py`

**Purpose**: Orchestrate notes synchronization

**Simplified Sync Algorithm**:

```python
class NotesSync:
    def __init__(self, config: AppConfig, db: NotesDB):
        self.config = config
        self.db = db
        self.apple_adapter = NotesAdapter()
        self.md_adapter = MarkdownAdapter(config.notes.remote_folder)

    async def sync_folder(folder_name: str) -> SyncResult:
        """
        Simplified bidirectional sync:

        1. Fetch all local notes (from AppleScript)
        2. Fetch all remote notes (from markdown folder)
        3. Load mappings from database
        4. For each note:
           a. If only in local → push to remote
           b. If only in remote → pull to local
           c. If in both:
              - Compare timestamps
              - Newer wins
              - Update loser
              - Update mapping
        5. Handle orphans (in mapping but deleted on both sides)

        No multi-phase complexity!
        """

        result = SyncResult(status=SyncStatus.SYNCING)

        # Get notes from both sides
        local_notes = await self.apple_adapter.get_notes(folder_name)
        remote_notes = await self.md_adapter.list_notes(folder_name)
        mappings = await self.db.get_all_mappings()

        # Build lookup dicts
        local_by_uuid = {n.uuid: n for n in local_notes}
        remote_by_path = {p: await self.md_adapter.read_note(p) for p in remote_notes}
        mapping_by_uuid = {m['local_uuid']: m for m in mappings}

        # Sync logic (timestamp-based)
        for local_note in local_notes:
            mapping = mapping_by_uuid.get(local_note.uuid)

            if not mapping:
                # New local note → push to remote
                await self._push_to_remote(local_note)
                result.items_created += 1
            else:
                remote_note = remote_by_path.get(Path(mapping['remote_path']))
                if not remote_note:
                    # Remote deleted → delete local or re-push
                    await self._handle_remote_deleted(local_note, mapping)
                else:
                    # Both exist → compare timestamps
                    if local_note.modified_date > remote_note.modified_date:
                        await self._push_to_remote(local_note)
                        result.items_updated += 1
                    elif remote_note.modified_date > local_note.modified_date:
                        await self._pull_from_remote(remote_note, folder_name)
                        result.items_updated += 1
                    # else: in sync, do nothing

        # Handle remote-only notes
        for remote_path, remote_note in remote_by_path.items():
            # Check if any mapping points to this remote
            if not any(m['remote_path'] == str(remote_path) for m in mappings):
                # New remote note → pull to local
                await self._pull_from_remote(remote_note, folder_name)
                result.items_created += 1

        result.status = SyncStatus.SUCCESS
        return result
```

**Complexity**: ~200-250 lines

---

## Implementation Order

### Step 1: Utilities (Foundation)
1. `utils/converters.py` - HTML/Markdown conversion
2. `utils/db.py` - Database helpers
3. **Test with**: Unit tests for conversion, db operations

### Step 2: Adapters (Data Sources)
1. `sources/notes/applescript.py` - AppleScript integration
2. `sources/notes/markdown.py` - Markdown file operations
3. **Test with**: Integration tests with real Notes app

### Step 3: Sync Logic (Core)
1. `core/sync.py` - Notes synchronization orchestration
2. **Test with**: End-to-end sync scenarios

### Step 4: CLI Integration
1. Update `cli/main.py` - Wire up notes commands
2. **Test with**: Manual CLI testing

---

## Key Simplifications Summary

| TaskBridge Approach | iCloudBridge Approach | Lines Saved |
|---------------------|----------------------|-------------|
| Staged files (export → parse) | Direct AppleScript parsing | ~200 lines |
| Base64 image intermediate | Direct file copy | ~150 lines |
| Dual HTML/Markdown storage | Single format, convert on-demand | ~100 lines |
| Multi-phase sync | Single-pass bidirectional | ~150 lines |
| Complex folder association | Simple folder matching | ~100 lines |
| **Total** | **Total** | **~700 lines** |

**Estimated Total Implementation**:
- TaskBridge notes: ~1500 lines
- iCloudBridge notes: ~800 lines (47% reduction)

---

## Testing Strategy

### Unit Tests
- [ ] HTML → Markdown conversion
- [ ] Markdown → HTML conversion
- [ ] Database CRUD operations
- [ ] AppleScript output parsing

### Integration Tests
- [ ] Fetch notes from Apple Notes
- [ ] Write notes to markdown folder
- [ ] Read notes from markdown folder
- [ ] Create notes in Apple Notes

### End-to-End Tests
- [ ] Sync new local note to remote
- [ ] Sync new remote note to local
- [ ] Sync updated local note (local newer)
- [ ] Sync updated remote note (remote newer)
- [ ] Handle deleted notes
- [ ] Handle attachments

---

## Error Handling

### AppleScript Errors
- Notes app not running → start it
- Permission denied → clear error message
- Note not found → treat as deleted

### File System Errors
- Remote folder doesn't exist → create it
- Permission denied → clear error message
- Disk full → abort with warning

### Sync Conflicts
- Both modified since last sync → newer wins (timestamp-based)
- Log conflicts for user review

---

## Performance Considerations

1. **Async Operations**: Use `asyncio.gather()` for parallel fetching
2. **Lazy Loading**: Don't load note bodies until needed
3. **Batch Operations**: Group AppleScript calls where possible
4. **Image Caching**: Only copy attachments if changed

---

## Next Steps

Ready to implement! Start with:

1. **Create `utils/converters.py`** - Simple, testable, no dependencies on adapters
2. **Create `utils/db.py`** - Simple, testable, no dependencies on adapters
3. **Create `sources/notes/applescript.py`** - Can test with real Notes app
4. **Create `sources/notes/markdown.py`** - Can test with temp directory
5. **Create `core/sync.py`** - Brings it all together
6. **Update CLI** - Make it usable

Target: Complete Phase 2 in 1 week of focused work.