"""HTML/Markdown conversion helpers plus checklist detection utilities."""

from __future__ import annotations

import logging
import re
from html import unescape
from pathlib import Path
from typing import List, Tuple

from html_to_markdown import convert_to_markdown
from markdown_it import MarkdownIt

logger = logging.getLogger(__name__)

CHECKLIST_LINE_RE = re.compile(r"\s*[-*]\s+\[[ xX]\]")


def normalize_checklists_html(html: str) -> str:
    """Ensure Apple checklist markup becomes - [ ] markdown-friendly bullets."""

    def replace(match: re.Match[str]) -> str:
        classes = match.group("classes") or ""
        inner = match.group("inner") or ""
        class_tokens = {token.strip() for token in classes.split() if token.strip()}
        marker = "[x] " if "checked" in class_tokens else "[ ] "
        text = re.sub(r"<[^>]+>", " ", inner)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return f"<li>{marker}{text}</li>"

    pattern = re.compile(
        r"<li\s+class=\"(?P<classes>[^\"]*)\"[^>]*>(?P<inner>.*?)</li>",
        re.DOTALL,
    )
    return pattern.sub(replace, html)


def html_to_markdown(html: str, note_title: str | None = None) -> str:
    if not html or not html.strip():
        return f"# {note_title}".strip() if note_title else ""

    html_cleaned = normalize_checklists_html(html)

    markdown = convert_to_markdown(
        html_cleaned,
        heading_style="atx",
        newline_style="spaces",
        code_language="",
        wrap_width=0,
        bullets="-*+",
        escape_misc=False,
    )

    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = markdown.strip()
    return _ensure_heading(markdown, note_title)


def markdown_to_html(markdown: str, note_title: str = "", attachment_paths: dict | None = None) -> str:
    if not markdown or not markdown.strip():
        return f"<h1>{note_title}</h1>" if note_title else ""

    md_parser = MarkdownIt()
    html = md_parser.render(markdown)

    if attachment_paths:
        for md_ref, file_path in attachment_paths.items():
            if isinstance(file_path, Path):
                file_path = str(file_path)
            pattern = re.compile(rf'<img\s+src="{re.escape(md_ref)}"[^>]*>', re.IGNORECASE)
            replacement = (
                f'<div><img style="max-width: 100%; max-height: 100%;" '
                f'src="file://{file_path}"/><br></div>'
            )
            html = pattern.sub(replacement, html)

    lines = html.split("\n")
    html_with_breaks: List[str] = []
    for line in lines:
        if line.strip():
            html_with_breaks.append(line)
        else:
            html_with_breaks.append("<br>")

    return "\n".join(html_with_breaks)


def contains_markdown_checklist(markdown: str) -> bool:
    return bool(CHECKLIST_LINE_RE.search(markdown or ""))


def split_markdown_segments(markdown: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    if not markdown:
        return segments

    current_type: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_type
        if buffer:
            segments.append((current_type or "content", "\n".join(buffer)))
            buffer = []

    for line in markdown.splitlines():
        is_check = bool(CHECKLIST_LINE_RE.match(line))
        target_type = "checklist" if is_check else "content"
        if current_type != target_type:
            flush()
            current_type = target_type
        buffer.append(line)

    flush()

    return [(kind, text) for kind, text in segments if text.strip()]


def extract_attachment_references(markdown: str) -> list[str]:
    if not markdown:
        return []

    pattern = r"!\[.*?\]\(([^)]+)\)"
    matches = re.findall(pattern, markdown)
    attachments = [
        match for match in matches if not match.startswith(("http://", "https://", "file://"))
    ]
    return attachments


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    if not filename:
        return "untitled"

    sanitized = filename
    sanitized = sanitized.replace("/", "-").replace("\\", "-")
    sanitized = re.sub(r'[<>:"|?*]', "", sanitized)
    sanitized = re.sub(r"[\s_]+", " ", sanitized)
    sanitized = sanitized.strip()

    if not sanitized:
        return "untitled"

    if len(sanitized) > max_length:
        name_parts = sanitized.rsplit(".", 1)
        if len(name_parts) == 2:
            name, ext = name_parts
            available_length = max_length - len(ext) - 1
            sanitized = f"{name[:available_length]}.{ext}"
        else:
            sanitized = sanitized[:max_length]

    return sanitized


def _ensure_heading(markdown: str, note_title: str | None) -> str:
    if not note_title:
        return markdown

    lines = markdown.splitlines()
    first_idx = next((idx for idx, line in enumerate(lines) if line.strip()), None)

    normalized_title = _normalize_heading_text(note_title)
    if first_idx is None:
        return f"# {note_title}"

    normalized_first = _normalize_heading_text(lines[first_idx])
    if normalized_first != normalized_title:
        return markdown

    if lines[first_idx].lstrip().startswith("#"):
        return markdown

    lines[first_idx] = f"# {note_title}"

    insert_idx = first_idx + 1
    if insert_idx == len(lines):
        lines.append("")
    elif lines[insert_idx].strip():
        lines.insert(insert_idx, "")

    return "\n".join(lines).strip()


def _normalize_heading_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^[#>*\s]+", "", cleaned)
    cleaned = re.sub(r"[*_`~]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.casefold()
