#!/usr/bin/env python3
"""
inspect_epub.py
===============
Diagnostic tool — prints the heading, first 120 chars of text, and char count
for every XHTML file in your EPUB folder so you can see what structure
sync_audiobook.py needs to match against.

Usage:
    python3.11 inspect_epub.py ~/bobbiverse/heavensriver/OEBPS/Text/
"""

import sys
from pathlib import Path
from bs4 import BeautifulSoup


def inspect(epub_dir: Path):
    files = sorted(
        list(epub_dir.glob("*.xhtml")) + list(epub_dir.glob("*.html")),
        key=lambda p: p.name,
    )
    if not files:
        print(f"No .xhtml / .html files found in {epub_dir}")
        return

    print(f"{'FILE':<30}  {'HEADING':<40}  {'CHARS':>6}  FIRST 120 CHARS OF BODY TEXT")
    print("─" * 140)

    for f in files:
        try:
            raw  = f.read_text(encoding="utf-8", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")

            # Remove nav/script/style
            for tag in soup(["script", "style", "nav", "head"]):
                tag.decompose()

            # Find first heading
            heading = ""
            for htag in soup.find_all(["h1", "h2", "h3", "h4", "title"]):
                heading = htag.get_text(" ", strip=True)
                if heading:
                    break

            body_text = soup.get_text(" ", strip=True)
            # Remove excess whitespace
            import re
            body_text = re.sub(r"\s+", " ", body_text).strip()
            char_count = len(body_text)
            preview = body_text[:120].replace("\n", " ")

            h_display = (heading[:38] + "…") if len(heading) > 39 else heading
            print(f"{f.name:<30}  {h_display:<40}  {char_count:>6}  {preview}")

        except Exception as e:
            print(f"{f.name:<30}  ERROR: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_epub.py /path/to/epub/Text/")
        sys.exit(1)
    d = Path(sys.argv[1])
    if not d.is_dir():
        sys.exit(f"Not a directory: {d}")
    inspect(d)
