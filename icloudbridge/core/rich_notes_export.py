"""Rich Notes export pipeline using the Ruby ripper."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
import re
from html import unescape

from icloudbridge.scripts.rich_notes import run_rich_ripper
from icloudbridge.utils.converters import html_to_markdown
from icloudbridge.utils.db import NotesDB

logger = logging.getLogger(__name__)


class RichNotesExporter:
    """Exports rich Apple Notes markup into a read-only RichNotes folder."""

    def __init__(self, notes_db_path: Path, remote_folder: Path) -> None:
        self.notes_db_path = notes_db_path
        self.remote_folder = remote_folder
        self.repo_root = Path(__file__).resolve().parents[2]

    async def _load_mappings(self) -> list[dict[str, Any]]:
        db = NotesDB(self.notes_db_path)
        await db.initialize()
        return await db.get_all_mappings()

    def _gather_mappings(self) -> list[dict[str, Any]]:
        return asyncio.run(self._load_mappings())

    def _copy_note_store(self, destination: Path) -> None:
        script = self.repo_root / "tools" / "note_db_copy" / "copy_note_db.py"
        cmd = [
            "/usr/bin/python3",
            str(script),
            "--dest",
            str(destination),
        ]
        logger.info("Copying Apple Notes database -> %s", destination)
        subprocess.run(cmd, check=True)

    def _run_ripper(self, db_path: Path, output_dir: Path) -> None:
        args = ["--file", str(db_path), "--output-dir", str(output_dir)]
        logger.info("Running rich notes ripper (output=%s)", output_dir)
        run_rich_ripper(args)

    def _find_json(self, output_dir: Path) -> Path:
        candidates = list(output_dir.rglob("json/all_notes_*.json"))
        if not candidates:
            raise FileNotFoundError(
                f"Could not find all_notes_*.json under ripper output {output_dir}"
            )
        return candidates[0]

    def _extract_note_content(self, note_entry: dict[str, Any]) -> str:
        html = note_entry.get("html") or ""
        marker = '<div class="note-content">'
        if marker in html:
            body = html.split(marker, 1)[1]
            # Remove closing </div> that wraps the content block
            if body.endswith("</div>"):
                body = body.rsplit("</div>", 1)[0]
        else:
            body = html
        return body

    def _convert_to_markdown(self, html: str, note_entry: dict[str, Any], original_path: Path) -> str:
        html = self._prepare_checklist_html(html)
        markdown = html_to_markdown(html)
        checkbox_items = self._extract_checklist_items(note_entry)

        if not checkbox_items or self._looks_truncated(checkbox_items):
            fallback = self._extract_checklists_from_markdown(original_path)
            if fallback:
                checkbox_items = fallback

        if checkbox_items:
            return self._merge_checklists(markdown, checkbox_items)

        return markdown

    @staticmethod
    def _prepare_checklist_html(html: str) -> str:
        """Rewrite checklist list items so markdown shows - [x]/[ ]."""

        def replace_li(match: re.Match[str]) -> str:
            classes = match.group("classes") or ""
            inner = match.group("inner") or ""
            class_tokens = {token.strip() for token in classes.split() if token.strip()}
            marker = "[x] " if "checked" in class_tokens else "[ ] "
            # Strip inner HTML tags to keep plaintext content inline
            text = re.sub(r"<[^>]+>", " ", inner)
            text = unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            logger.debug("Checklist item raw=%r cleaned=%r", inner[:80], text)
            return f"<li>{marker}{text}</li>"

        pattern = re.compile(
            r"<li\s+class=\"(?P<classes>[^\"]*)\"[^>]*>(?P<inner>.*?)</li>",
            re.DOTALL,
        )
        return pattern.sub(replace_li, html)

    def _merge_checklists(self, markdown: str, checkbox_items: list[tuple[str, str]]) -> str:
        if not checkbox_items:
            return markdown

        new_lines = markdown.splitlines()
        idx = 0

        checkbox_pattern = re.compile(r"^[*-]\s+\\?\[[ xX]\]")

        for i, line in enumerate(new_lines):
            stripped = line.strip()
            if checkbox_pattern.match(stripped) and idx < len(checkbox_items):
                state, label = checkbox_items[idx]
                new_lines[i] = f"- [{state}] {label}"
                idx += 1

        return "\n".join(new_lines)

    def _extract_checklist_items(self, note_entry: dict[str, Any]) -> list[tuple[str, str]]:
        html = note_entry.get("html") or ""
        if "class=\"checklist\"" not in html:
            return []

        items: list[tuple[str, str]] = []

        ul_pattern = re.compile(r"<ul[^>]*class=\"checklist\"[^>]*>(?P<body>.*?)</ul>", re.DOTALL)
        li_pattern = re.compile(r"<li\s+class=\"(?P<classes>[^\"]*)\"[^>]*>(?P<inner>.*?)</li>", re.DOTALL)

        for ul_match in ul_pattern.finditer(html):
            body = ul_match.group("body")
            for li_match in li_pattern.finditer(body):
                classes = li_match.group("classes") or ""
                inner = li_match.group("inner") or ""

                text = re.sub(r"<[^>]+>", " ", inner)
                text = unescape(re.sub(r"\s+", " ", text).strip())

                class_tokens = {token.strip() for token in classes.split() if token.strip()}
                state = "x" if "checked" in class_tokens else " "
                items.append((state, text))

        return items

    @staticmethod
    def _looks_truncated(checkbox_items: list[tuple[str, str]]) -> bool:
        if not checkbox_items:
            return True
        longest = max(len(label.strip()) for _, label in checkbox_items)
        return longest <= 2

    def _extract_checklists_from_markdown(self, original_path: Path) -> list[tuple[str, str]]:
        if not original_path.exists():
            return []

        try:
            lines = original_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

        items: list[tuple[str, str]] = []
        capture = False

        for line in lines:
            stripped = line.strip()
            if not capture and stripped.lower() == "checklist":
                capture = True
                continue

            if capture:
                if not stripped:
                    if items:
                        break
                    continue

                if stripped.startswith("- [") and len(stripped) >= 6:
                    state = "x" if stripped[3].lower() == "x" else " "
                    label = stripped[6:].strip()
                    items.append((state, label))

        return items

    def export(self, *, dry_run: bool = False) -> None:
        if not self.remote_folder:
            raise ValueError("Remote notes folder is not configured")

        mappings = self._gather_mappings()
        if not mappings:
            logger.warning("No note mappings exist; skipping rich notes export")
            return

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            local_store = tmp_dir / "NoteStore.sqlite"
            ripper_output = tmp_dir / "ripper_output"
            ripper_output.mkdir(parents=True, exist_ok=True)

            self._copy_note_store(local_store)
            self._run_ripper(local_store, ripper_output)

            json_file = self._find_json(ripper_output)
            data = json.loads(json_file.read_text(encoding="utf-8"))
            indexes = self._build_note_indexes(data.get("notes", {}))

        rich_root = self.remote_folder / "RichNotes"
        selected_notes: list[tuple[Path, dict[str, Any]]] = []

        for mapping in mappings:
            uuid = mapping["local_uuid"]
            note_entry = self._lookup_note_entry(uuid, indexes)
            if not note_entry:
                continue
            remote_path = Path(mapping["remote_path"])
            try:
                relative = remote_path.relative_to(self.remote_folder)
            except ValueError:
                continue
            selected_notes.append((relative, note_entry))

        if not selected_notes:
            logger.warning("No overlapping notes found between mappings and ripper output")
            return

        logger.info("Preparing RichNotes export (%d notes)", len(selected_notes))

        if dry_run:
            logger.info("Dry run: skipping filesystem changes for RichNotes export")
            return

        if rich_root.exists():
            shutil.rmtree(rich_root)
        rich_root.mkdir(parents=True, exist_ok=True)

        self._write_readme(rich_root)

        for relative_path, note_entry in selected_notes:
            folder = rich_root / relative_path.parent
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{relative_path.stem}_rich.md"
            html = self._extract_note_content(note_entry)
            logger.info("Exporting rich note: %s", relative_path)
            logger.debug("Relative path stem: %s", relative_path.stem)
            if relative_path.stem.lower() == "scratch":
                debug_dir = Path.home() / ".icloudbridge" / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                debug_path = debug_dir / f"{relative_path.stem}_rich_note.json"
                logger.info("Writing scratch debug JSON to %s", debug_path)
                debug_path.write_text(json.dumps(note_entry, indent=2), encoding="utf-8")
                logger.info("Scratch entry snapshot: %s", note_entry)
                logger.info("Scratch plaintext: %s", note_entry.get("plaintext"))
            original_path = self.remote_folder / relative_path
            markdown = self._convert_to_markdown(html, note_entry, original_path)
            target.write_text(markdown, encoding="utf-8")

        logger.info("RichNotes export complete: %s", rich_root)

    def _write_readme(self, rich_root: Path) -> None:
        content = """# Rich Notes Export\n\n"""
        content += (
            "This directory contains a read-only snapshot of your Apple Notes, exported using "
            "iCloudBridge's rich-notes mode. Every time you run `icloudbridge notes sync --rich-notes`, "
            "this folder is regenerated from scratch.\n\n"
            "- ✔️ Feel free to read or copy these Markdown files.\n"
            "- ⚠️ Changes made here will **NOT** sync back to Apple Notes.\n"
            "- ♻️ Any edits inside `RichNotes/` will be overwritten on the next export.\n"
        )
        (rich_root / "README.md").write_text(content, encoding="utf-8")

    def _build_note_indexes(self, notes_section: Any) -> dict[str, Any]:
        by_uuid: dict[str, dict[str, Any]] = {}
        by_primary: dict[int, dict[str, Any]] = {}
        by_note_id: dict[int, dict[str, Any]] = {}

        if isinstance(notes_section, dict):
            for key, entry in notes_section.items():
                if not isinstance(entry, dict):
                    continue
                uuid = entry.get("uuid") or key
                if uuid:
                    by_uuid[str(uuid)] = entry

                pk = entry.get("primary_key")
                if isinstance(pk, int):
                    by_primary[pk] = entry

                note_id = entry.get("note_id")
                if isinstance(note_id, int):
                    by_note_id[note_id] = entry

        return {
            "by_uuid": by_uuid,
            "by_primary": by_primary,
            "by_note_id": by_note_id,
        }

    def _lookup_note_entry(self, local_uuid: str, indexes: dict[str, Any]) -> dict[str, Any] | None:
        entry = indexes["by_uuid"].get(local_uuid)
        if entry:
            return entry

        pk = self._extract_primary_key(local_uuid)
        if pk is not None:
            entry = indexes["by_primary"].get(pk)
            if entry:
                return entry

        note_id = self._extract_note_id(local_uuid)
        if note_id is not None:
            entry = indexes["by_note_id"].get(note_id)
            if entry:
                return entry

        return None

    @staticmethod
    def _extract_primary_key(coredata_id: str) -> int | None:
        if "/p" not in coredata_id:
            return None
        suffix = coredata_id.rsplit("/p", 1)[-1]
        try:
            return int(suffix)
        except ValueError:
            return None

    @staticmethod
    def _extract_note_id(coredata_id: str) -> int | None:
        # Some IDs have the format .../ICNote/<number>
        suffix = coredata_id.rsplit("/", 1)[-1]
        if suffix.startswith("p"):
            suffix = suffix[1:]
        try:
            return int(suffix)
        except ValueError:
            return None
