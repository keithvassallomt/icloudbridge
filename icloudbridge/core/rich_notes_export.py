"""Rich Notes export pipeline using the Ruby ripper."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from icloudbridge.core.rich_notes_capture import (
    RichNotesCapture,
    extract_note_content,
    lookup_note_entry,
)
from icloudbridge.utils.converters import html_to_markdown, normalize_checklists_html
from icloudbridge.utils.db import NotesDB

logger = logging.getLogger(__name__)


class RichNotesExporter:
    """Exports rich Apple Notes markup into a read-only RichNotes folder."""

    def __init__(self, notes_db_path: Path, remote_folder: Path) -> None:
        self.notes_db_path = notes_db_path
        self.remote_folder = remote_folder
        self.repo_root = Path(__file__).resolve().parents[2]
        self._capture = RichNotesCapture(repo_root=self.repo_root)

    async def _load_mappings(self) -> list[dict[str, Any]]:
        db = NotesDB(self.notes_db_path)
        await db.initialize()
        return await db.get_all_mappings()

    def _gather_mappings(self) -> list[dict[str, Any]]:
        return asyncio.run(self._load_mappings())

    def _copy_note_store(self, destination: Path) -> None:
        script = self.repo_root / "tools" / "note_db_copy" / "copy_note_db.py"
        python = self._preferred_python()
        cmd = [
            python,
            str(script),
            "--dest",
            str(destination),
        ]
        env = os.environ.copy()
        env["PYTHONHOME"] = ""
        env["PYTHONPATH"] = ""
        env["VIRTUAL_ENV"] = ""
        env["ICLOUDBRIDGE_VENV_PYTHON"] = python
        logger.error(
            "copy_note_db (export): python=%s cmd=%s env_pythonhome=%s env_pythonpath=%s env_virtual_env=%s",
            str(python),
            " ".join(cmd),
            env.get("PYTHONHOME", ""),
            env.get("PYTHONPATH", ""),
            env.get("VIRTUAL_ENV", ""),
        )
        try:
            subprocess.run(cmd, check=True, env=env)
        except Exception as exc:  # pragma: no cover - runtime logging
            logger.exception("copy_note_db export failed: %s", exc)
            raise

    @staticmethod
    def _preferred_python() -> str:
        env_python = os.environ.get("ICLOUDBRIDGE_VENV_PYTHON")
        if env_python and Path(env_python).is_file():
            return env_python

        app_support = Path.home() / "Library" / "Application Support" / "iCloudBridge" / "venv" / "bin" / "python3"
        if app_support.is_file():
            return str(app_support)

        logger.error(
            "No managed python found. Checked ICLOUDBRIDGE_VENV_PYTHON=%s and %s; falling back to %s",
            env_python,
            app_support,
            sys.executable,
        )
        return sys.executable

    def _run_ripper(self, db_path: Path, output_dir: Path) -> None:
        args = ["--file", str(db_path), "--output-dir", str(output_dir)]
        logger.info("Running rich notes ripper (output=%s)", output_dir)
        run_rich_ripper(args, log_stream=logger, log_category="notes_ripper", log_level="DEBUG")

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
        html = normalize_checklists_html(html)
        markdown = html_to_markdown(html)
        checkbox_items = self._extract_checklist_items(note_entry)

        if not checkbox_items or self._looks_truncated(checkbox_items):
            fallback = self._extract_checklists_from_markdown(original_path)
            if fallback:
                checkbox_items = fallback

        if checkbox_items:
            return self._merge_checklists(markdown, checkbox_items)

        return markdown

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

        indexes = self._capture.capture_indexes()

        rich_root = self.remote_folder / "RichNotes"
        selected_notes: list[tuple[Path, dict[str, Any]]] = []

        for mapping in mappings:
            uuid = mapping["local_uuid"]
            note_entry = lookup_note_entry(uuid, indexes)
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
            html = extract_note_content(note_entry)
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
