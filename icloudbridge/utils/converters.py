"""HTML and Markdown conversion utilities for Apple Notes."""

import re
from pathlib import Path

from html_to_markdown import convert_to_markdown
from markdown_it import MarkdownIt


def html_to_markdown(html: str) -> str:
    """
    Convert HTML from Apple Notes to Markdown.

    Apple Notes exports HTML with specific quirks:
    - First line is an <h1> with the note title (we strip this)
    - Images are embedded with <img> tags
    - Heavy use of <div> and <br> tags

    Args:
        html: HTML content from Apple Notes

    Returns:
        Clean Markdown representation

    Example:
        >>> html = '<h1>My Note</h1><p>Hello <b>world</b>!</p>'
        >>> html_to_markdown(html)
        'Hello **world**!'
    """
    if not html or not html.strip():
        return ""

    # Strip the first <h1> tag (note title) that Apple Notes adds
    html_cleaned = re.sub(r"^<h1>.*?</h1>\s*", "", html, count=1, flags=re.DOTALL)

    # Convert to Markdown using html-to-markdown
    markdown = convert_to_markdown(
        html_cleaned,
        heading_style="atx",  # Use # for headings (not underlined)
        newline_style="spaces",  # Use two spaces for line breaks
        code_language="",  # Default code language if not specified
        wrap_width=0,  # Don't wrap lines (preserve formatting)
        bullets="-*+",  # Prefer hyphen bullets so checklists look like - [ ]
        escape_misc=False,  # Keep literal [] so we can rewrite checklists cleanly
    )

    # Clean up excessive newlines (Apple Notes adds lots of <br>)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    return markdown.strip()


def markdown_to_html(markdown: str, note_title: str = "", attachment_paths: dict = None) -> str:
    """
    Convert Markdown to HTML for Apple Notes.

    Apple Notes expects HTML in a specific format:
    - First line should be <h1> with note title
    - Images need file:// URLs
    - Proper HTML structure with line breaks

    Args:
        markdown: Markdown content to convert
        note_title: Title of the note (added as <h1>)
        attachment_paths: Optional dict mapping markdown image refs to file paths
                         e.g., {'.attachments/uuid.png': '/full/path/to/image.png'}

    Returns:
        HTML formatted for Apple Notes

    Example:
        >>> markdown = 'Hello **world**!'
        >>> markdown_to_html(markdown, 'My Note')
        '<h1>My Note</h1><p>Hello <strong>world</strong>!</p>'
    """
    if not markdown or not markdown.strip():
        # Even empty notes need a title in Apple Notes
        return f"<h1>{note_title}</h1>" if note_title else ""

    # Initialize markdown-it parser
    md_parser = MarkdownIt()

    # Convert Markdown to HTML
    html = md_parser.render(markdown)

    # Strip the first <h1> tag (note title) - Apple Notes will display this in the body
    # AND use it as the note title, causing duplication. We strip it here, and the
    # note title will be set separately via the AppleScript note_title parameter.
    html = re.sub(r"^<h1>.*?</h1>\s*", "", html, count=1, flags=re.DOTALL)

    # Handle image attachments - convert to file:// URLs for Apple Notes
    if attachment_paths:
        for md_ref, file_path in attachment_paths.items():
            # Convert Path objects to strings
            if isinstance(file_path, Path):
                file_path = str(file_path)

            # Replace markdown image references with Apple Notes compatible file:// URLs
            # Match: <img src=".attachments/uuid.png" alt="...">
            pattern = re.compile(
                rf'<img\s+src="{re.escape(md_ref)}"[^>]*>',
                re.IGNORECASE,
            )

            replacement = (
                f'<div><img style="max-width: 100%; max-height: 100%;" '
                f'src="file://{file_path}"/><br></div>'
            )

            html = pattern.sub(replacement, html)

    # Replace empty lines with <br> tags (Apple Notes rendering)
    lines = html.split("\n")
    html_with_breaks = []
    for line in lines:
        if line.strip():
            html_with_breaks.append(line)
        else:
            html_with_breaks.append("<br>")

    return "\n".join(html_with_breaks)


def extract_attachment_references(markdown: str) -> list[str]:
    """
    Extract attachment file references from Markdown content.

    Finds all image references in the format: ![alt](.attachments/filename)

    Args:
        markdown: Markdown content to parse

    Returns:
        List of attachment file paths referenced in the markdown

    Example:
        >>> md = 'Check this ![image](.attachments/pic.png) out!'
        >>> extract_attachment_references(md)
        ['.attachments/pic.png']
    """
    if not markdown:
        return []

    # Match markdown image syntax: ![alt text](path)
    # Focus on .attachments/ folder references
    pattern = r"!\[.*?\]\(([^)]+)\)"
    matches = re.findall(pattern, markdown)

    # Filter to only attachment references (not URLs)
    attachments = [
        match for match in matches if not match.startswith(("http://", "https://", "file://"))
    ]

    return attachments


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize a filename for safe filesystem usage.

    Removes or replaces characters that are problematic in filenames:
    - Path separators (/, \\)
    - Special characters (:, *, ?, ", <, >, |)
    - Control characters

    Args:
        filename: Original filename
        max_length: Maximum length for filename (default: 255)

    Returns:
        Sanitized filename safe for filesystem use

    Example:
        >>> sanitize_filename('My Note: Draft #1')
        'My Note Draft 1'
    """
    if not filename:
        return "untitled"

    # Replace problematic characters with underscore or space
    sanitized = filename

    # Remove/replace path separators
    sanitized = sanitized.replace("/", "-").replace("\\", "-")

    # Remove special characters that are invalid in filenames
    sanitized = re.sub(r'[<>:"|?*]', "", sanitized)

    # Replace multiple spaces/underscores with single space
    sanitized = re.sub(r"[\s_]+", " ", sanitized)

    # Trim whitespace
    sanitized = sanitized.strip()

    # Ensure we have something left
    if not sanitized:
        return "untitled"

    # Truncate if too long (preserve extension if present)
    if len(sanitized) > max_length:
        name_parts = sanitized.rsplit(".", 1)
        if len(name_parts) == 2:
            # Has extension
            name, ext = name_parts
            available_length = max_length - len(ext) - 1
            sanitized = f"{name[:available_length]}.{ext}"
        else:
            # No extension
            sanitized = sanitized[:max_length]

    return sanitized
