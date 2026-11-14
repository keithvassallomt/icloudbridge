"""HTML/Markdown conversion helpers plus checklist detection utilities."""

from __future__ import annotations

import hashlib
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

    md_parser = MarkdownIt("commonmark", {"breaks": True})
    html = md_parser.render(markdown)
    html = re.sub(r"</p>\s*<p>", "</p><br><p>", html)

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


# A Notes tag: "#" then a letter, then letters, digits, "_" or "-", starting a
# word. That leaves headings ("# Title"), issue numbers ("#18"), URL fragments
# ("page#top") and link anchors ("](#top)") alone.
HASHTAG_RE = re.compile(r"[ \t]?(?<!\S)#([^\W\d_][\w-]*)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def extract_hashtags(markdown: str) -> tuple[str, list[str]]:
    """
    Remove Notes-style hashtags from markdown and return them separately.

    Tags written back to Apple Notes as text are just text, so they are
    stripped here and applied as real tags instead. Code is left untouched.
    Returns the markdown without tags and the tag names (no "#"), each once.
    """
    tags: dict[str, str] = {}
    lines: list[str] = []
    in_fence = False

    def take(match: re.Match[str]) -> str:
        tags.setdefault(match.group(1).casefold(), match.group(1))
        return ""

    for line in (markdown or "").splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if in_fence or FENCE_RE.match(line):
            lines.append(line)
            continue
        # Only rewrite the text between inline code spans
        parts, last = [], 0
        for code in INLINE_CODE_RE.finditer(line):
            parts += [HASHTAG_RE.sub(take, line[last:code.start()]), code.group()]
            last = code.end()
        parts.append(HASHTAG_RE.sub(take, line[last:]))
        stripped = "".join(parts)
        if stripped != line:
            # Keep the line's own indentation, not the gap a leading tag left
            indent = line[: len(line) - len(line.lstrip())]
            stripped = indent + stripped.strip()
        # A line that held only tags goes away entirely
        if stripped.strip() or not line.strip():
            lines.append(stripped)

    return "\n".join(lines), list(tags.values())


def extract_attachment_references(markdown: str) -> list[str]:
    if not markdown:
        return []

    pattern = r"!\[.*?\]\(([^)]+)\)"
    matches = re.findall(pattern, markdown)
    attachments = [
        match for match in matches if not match.startswith(("http://", "https://", "file://"))
    ]
    return attachments


# Longest name, in UTF-8 bytes, that APFS, HFS+ and ext4 all accept (NAME_MAX).
FILENAME_MAX_BYTES = 255
# Note files are "<stem>.md", each with a ".<stem>.md.meta.json" sidecar
# (MarkdownAdapter). The stem budget leaves room for the longer sidecar name.
# Every place that maps a note title to a file name must use this budget, or
# long titles stop matching their files.
NOTE_FILENAME_SUFFIX = ".md"
NOTE_METADATA_SUFFIX = ".meta.json"
NOTE_STEM_MAX_BYTES = FILENAME_MAX_BYTES - len(f".{NOTE_FILENAME_SUFFIX}{NOTE_METADATA_SUFFIX}".encode("utf-8"))
_MAX_EXTENSION_BYTES = 16
_HASH_SUFFIX_CHARS = 8


def _utf8_prefix(text: str, max_bytes: int) -> str:
    """Longest prefix of text that fits in max_bytes of UTF-8."""
    return text.encode("utf-8")[: max(max_bytes, 0)].decode("utf-8", "ignore")


def sanitize_filename(filename: str, max_length: int = FILENAME_MAX_BYTES, *, keep_extension: bool = True) -> str:
    """Make a file name safe, at most max_length bytes of UTF-8.

    A name that is too long is cut on a character boundary and gets "~" plus
    a short hash of the full name, so long names sharing a prefix still map
    to different files. Names that fit are returned exactly as before.
    """
    if not filename:
        return "untitled"

    sanitized = filename
    sanitized = sanitized.replace("/", "-").replace("\\", "-")
    sanitized = re.sub(r'[<>:"|?*]', "", sanitized)
    sanitized = re.sub(r"[\s_]+", " ", sanitized)
    sanitized = sanitized.strip()

    if not sanitized:
        return "untitled"

    if len(sanitized.encode("utf-8")) <= max_length:
        return sanitized

    digest = hashlib.sha1(sanitized.encode("utf-8")).hexdigest()[:_HASH_SUFFIX_CHARS]
    name, ext = sanitized, ""
    if keep_extension:
        base, dot, candidate = sanitized.rpartition(".")
        # Only a short, space-free tail is an extension; a dotted title such
        # as "example.net/page..." must not keep its long tail.
        if dot and base and candidate and " " not in candidate and len(candidate.encode("utf-8")) <= _MAX_EXTENSION_BYTES:
            name, ext = base, f".{candidate}"
    suffix = f"~{digest}{ext}"
    stem = _utf8_prefix(name, max_length - len(suffix.encode("utf-8"))).rstrip(" .")
    return f"{stem}{suffix}"


def note_filename_stem(note_name: str) -> str:
    """File stem for a note title, leaving room for the .md suffix."""
    return sanitize_filename(note_name, NOTE_STEM_MAX_BYTES, keep_extension=False)


_VARIANT_RE = re.compile(r"(?s)(.*) \((\d+)\)")


def note_filename_variant(stem: str, n: int) -> str:
    """Stem for the n-th note whose title maps to the same file name.

    Notes can share a title ("TODO", "Temp"), so the second one is written
    as "TODO (2)", the third as "TODO (3)", and so on.
    """
    if n < 2:
        return stem
    tail = f" ({n})"
    return _utf8_prefix(stem, NOTE_STEM_MAX_BYTES - len(tail.encode("utf-8"))).rstrip(" .") + tail


def note_stem_matches_title(stem: str, title: str) -> bool:
    """Whether a file stem is the file name this title maps to.

    Accepts the numbered variants of note_filename_variant() and ignores case,
    because APFS treats "Todo.md" and "TODO.md" as the same file.
    """
    base = note_filename_stem(title)
    if stem.casefold() == base.casefold():
        return True
    match = _VARIANT_RE.fullmatch(stem)
    if not match or int(match.group(2)) < 2:
        return False
    return note_filename_variant(base, int(match.group(2))).casefold() == stem.casefold()


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


def strip_leading_heading(markdown: str, note_title: str | None) -> str:
    if not markdown or not note_title:
        return markdown

    lines = markdown.splitlines()
    first_idx = next((idx for idx, line in enumerate(lines) if line.strip()), None)
    if first_idx is None:
        return markdown

    line = lines[first_idx]
    if not line.lstrip().startswith("#"):
        return markdown

    normalized_line = _normalize_heading_text(line)
    normalized_title = _normalize_heading_text(note_title)
    if normalized_line != normalized_title:
        return markdown

    lines.pop(first_idx)
    if first_idx < len(lines) and not lines[first_idx].strip():
        lines.pop(first_idx)

    trimmed = "\n".join(lines)
    return trimmed.lstrip("\n")


def add_markdown_soft_breaks(markdown: str) -> str:
    """Return markdown unchanged (single newlines map to single line breaks)."""

    return markdown or ""


def insert_markdown_blank_line_markers(markdown: str) -> str:
    if not markdown:
        return ""

    return re.sub(
        r"\n\s*\n+",
        "\n\n<div class=\"icloudbridge-blank\"><br></div>\n\n",
        markdown,
    )


def markdown_block_to_inline_html(markdown: str) -> str:
    """Fallback helper retained for completeness (currently unused)."""

    if not markdown:
        return ""

    md_parser = MarkdownIt("commonmark", {"breaks": True})
    return md_parser.render(markdown).strip()
