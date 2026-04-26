#!/usr/bin/env python3
"""
convert_m4b.py
==============
Splits an .m4b audiobook file into per-chapter MP3 files using the chapter
markers embedded in the file.  Requires ffmpeg / ffprobe (brew install ffmpeg).

Usage:
    python convert_m4b.py input.m4b --out ./audio
    python convert_m4b.py input.m4b --out ./audio --author "Dennis E. Taylor"
    python convert_m4b.py input.m4b --out ./audio --start-track 3 --quality 2

Options:
    --out DIR           Output directory for MP3 files (required)
    --author NAME       Author name to embed in filenames
    --start-track N     Starting track number (default: 1)
    --quality 0-9       MP3 VBR quality: 0=best, 9=smallest (default: 2 ≈ 190 kbps)
    --list              Just list chapters, don't convert
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def check_ffmpeg():
    for cmd in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run([cmd, "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            sys.exit(
                f"ERROR: '{cmd}' not found.\n"
                "  Install with: brew install ffmpeg"
            )


def get_chapters(m4b_path: Path) -> list[dict]:
    """Extract chapter markers from an m4b/m4a/aac file using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_chapters",
            str(m4b_path),
        ],
        capture_output=True, text=True, check=True,
    )
    data     = json.loads(result.stdout)
    chapters = data.get("chapters", [])
    if not chapters:
        sys.exit(
            "ERROR: No chapter markers found in the file.\n"
            "  Verify with: ffprobe -show_chapters \"" + str(m4b_path) + "\""
        )
    return chapters


def sanitize(name: str, max_len: int = 80) -> str:
    """Remove filesystem-unsafe characters from a string."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len]


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ═════════════════════════════════════════════════════════════════════════════
# CONVERSION
# ═════════════════════════════════════════════════════════════════════════════

def convert(
    m4b_path: Path,
    out_dir: Path,
    author: str,
    start_track: int,
    quality: int,
    dry_run: bool,
):
    chapters = get_chapters(m4b_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Source  : {m4b_path}")
    print(f"Chapters: {len(chapters)}")
    print(f"Output  : {out_dir.resolve()}")
    if dry_run:
        print("(dry-run — no files will be written)\n")
    print()

    failed = []

    for i, ch in enumerate(chapters):
        track_num = i + start_track
        title     = ch.get("tags", {}).get("title", f"Chapter {i + 1}").strip()
        start     = float(ch["start_time"])
        end       = float(ch["end_time"])
        duration  = end - start

        # Build filename
        safe_title = sanitize(title)
        if author:
            filename = f"{track_num:02d} - {sanitize(author)} - {safe_title}.mp3"
        else:
            filename = f"{track_num:02d} - {safe_title}.mp3"

        out_path = out_dir / filename

        print(
            f"  [{track_num:02d}] {title}\n"
            f"       {format_duration(start)} → {format_duration(end)}"
            f"  ({format_duration(duration)})\n"
            f"       → {filename}"
        )

        if dry_run:
            print()
            continue

        if out_path.exists():
            print("       (already exists — skipping)\n")
            continue

        cmd = [
            "ffmpeg", "-y",
            "-i",         str(m4b_path),
            "-ss",        f"{start:.6f}",
            "-to",        f"{end:.6f}",
            "-vn",                          # strip video/cover art
            "-c:a",       "libmp3lame",
            "-q:a",       str(quality),     # VBR quality
            "-map_metadata", "-1",          # strip source metadata
            "-id3v2_version", "3",
            # Write minimal ID3 tags
            "-metadata",  f"title={title}",
            "-metadata",  f"track={track_num}",
            *((["-metadata", f"artist={author}"] if author else [])),
            "-loglevel",  "error",
            str(out_path),
        ]

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"       ERROR: ffmpeg returned {result.returncode}\n")
            failed.append(filename)
        else:
            size_kb = out_path.stat().st_size // 1024
            print(f"       ✓  {size_kb:,} KB\n")

    # Summary
    succeeded = len(chapters) - len(failed)
    print(f"{'─'*60}")
    print(f"Done.  {succeeded}/{len(chapters)} converted", end="")
    if failed:
        print(f",  {len(failed)} failed:")
        for f in failed:
            print(f"  ✗  {f}")
    else:
        print()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Split an .m4b audiobook into per-chapter MP3 files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", metavar="FILE",
                        help=".m4b (or .m4a / .aac) audiobook file")
    parser.add_argument("--out", required=True, metavar="DIR",
                        help="Output directory for MP3 files")
    parser.add_argument("--author", default="", metavar="NAME",
                        help="Author name to embed in filenames")
    parser.add_argument("--start-track", type=int, default=1, metavar="N",
                        help="Starting track number (default: 1)")
    parser.add_argument("--quality", type=int, default=2, metavar="0-9",
                        help="MP3 VBR quality 0=best 9=smallest (default: 2)")
    parser.add_argument("--list", action="store_true",
                        help="List chapters only, do not convert")
    args = parser.parse_args()

    m4b = Path(args.input)
    if not m4b.exists():
        sys.exit(f"ERROR: File not found: {m4b}")
    if m4b.suffix.lower() not in (".m4b", ".m4a", ".aac", ".mp4"):
        print(f"WARNING: Unexpected extension '{m4b.suffix}' — attempting anyway.")

    check_ffmpeg()

    convert(
        m4b_path    = m4b,
        out_dir     = Path(args.out),
        author      = args.author,
        start_track = args.start_track,
        quality     = args.quality,
        dry_run     = args.list,
    )


if __name__ == "__main__":
    main()
