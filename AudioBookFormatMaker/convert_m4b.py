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
import math
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


def estimate_size_mb(duration_seconds: float, bitrate_kbps: int) -> float:
    """Estimate output size in MB from duration and bitrate (mono/stereo agnostic)."""
    if duration_seconds <= 0 or bitrate_kbps <= 0:
        return 0.0
    # kbit/s -> MB (binary MB)
    return (duration_seconds * bitrate_kbps / 8.0) / 1024.0


def quality_to_estimated_kbps(quality: int) -> int:
    """Rough MP3 VBR bitrate estimate for size projection."""
    table = {
        0: 245,
        1: 225,
        2: 190,
        3: 175,
        4: 165,
        5: 130,
        6: 115,
        7: 100,
        8: 85,
        9: 65,
    }
    return table.get(max(0, min(9, quality)), 190)


def bitrate_for_target_mb(total_seconds: float, target_mb: float, safety_margin: float = 0.97) -> int:
    """Compute CBR kbps to fit a target size with a small safety margin."""
    if total_seconds <= 0 or target_mb <= 0:
        return 64
    raw_kbps = (target_mb * 1024.0 * 8.0 / total_seconds) * safety_margin
    # Clamp to sane MP3 CBR range.
    return max(32, min(320, int(math.floor(raw_kbps))))


def detect_hwaccels() -> list[str]:
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-hwaccels"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return []
    lines = [ln.strip() for ln in probe.stdout.splitlines()]
    out = []
    seen = False
    for ln in lines:
        if "Hardware acceleration methods" in ln:
            seen = True
            continue
        if seen and ln:
            out.append(ln)
    return out


def choose_hwaccel(hwaccel_mode: str) -> str | None:
    """Choose one hwaccel backend once per run."""
    if hwaccel_mode == "off":
        return None

    available = detect_hwaccels()
    if not available:
        return None

    preferred_order = ["cuda", "vaapi", "videotoolbox", "qsv", "d3d11va", "dxva2", "vulkan"]
    for accel in preferred_order:
        if accel in available:
            return accel
    return available[0]


def run_ffmpeg_with_fallback(cmd: list[str], selected_accel: str | None) -> tuple[subprocess.CompletedProcess, bool]:
    """
    Run ffmpeg with a preselected hwaccel backend and CPU fallback.
    Returns (result, keep_using_hwaccel).
    """
    if not selected_accel:
        return subprocess.run(cmd), False

    hw_cmd = cmd[:1] + ["-hwaccel", selected_accel] + cmd[1:]
    result = subprocess.run(hw_cmd)
    if result.returncode == 0:
        return result, True

    print(f"       NOTE: hwaccel '{selected_accel}' failed; retrying on CPU and disabling hwaccel for remaining tracks...")
    cpu_result = subprocess.run(cmd)
    return cpu_result, False


# ═════════════════════════════════════════════════════════════════════════════
# CONVERSION
# ═════════════════════════════════════════════════════════════════════════════

def convert(
    m4b_path: Path,
    out_dir: Path,
    author: str,
    start_track: int,
    quality: int,
    bitrate_kbps: int | None,
    target_size_mb: float | None,
    hwaccel_mode: str,
    dry_run: bool,
):
    chapters = get_chapters(m4b_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_duration = 0.0
    for ch in chapters:
        total_duration += float(ch["end_time"]) - float(ch["start_time"])

    effective_bitrate = bitrate_kbps
    if target_size_mb is not None:
        effective_bitrate = bitrate_for_target_mb(total_duration, target_size_mb)

    if effective_bitrate is None:
        estimated_kbps = quality_to_estimated_kbps(quality)
    else:
        estimated_kbps = effective_bitrate

    estimated_total_mb = estimate_size_mb(total_duration, estimated_kbps)
    selected_accel = choose_hwaccel(hwaccel_mode)

    print(f"Source  : {m4b_path}")
    print(f"Chapters: {len(chapters)}")
    print(f"Output  : {out_dir.resolve()}")
    print(f"Mode    : mono MP3")
    if effective_bitrate is None:
        print(f"Bitrate : VBR quality {quality} (~{estimated_kbps} kbps estimated)")
    else:
        print(f"Bitrate : CBR {effective_bitrate} kbps")
    if target_size_mb is not None:
        fits = "yes" if estimated_total_mb <= target_size_mb else "no"
        print(f"Target  : {target_size_mb:.1f} MB (CD-R fit: {fits})")
    print(f"Estimate: {estimated_total_mb:.1f} MB total")
    if selected_accel:
        print(f"Accel   : {hwaccel_mode} (selected backend: {selected_accel}, auto fallback CPU)")
    else:
        print(f"Accel   : {hwaccel_mode} (CPU)")
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
            "-ac",        "1",             # audiobook output should be mono
            "-map_metadata", "-1",          # strip source metadata
            "-id3v2_version", "3",
            # Write minimal ID3 tags
            "-metadata",  f"title={title}",
            "-metadata",  f"track={track_num}",
            *((["-metadata", f"artist={author}"] if author else [])),
            "-loglevel",  "error",
            str(out_path),
        ]

        if effective_bitrate is None:
            cmd[cmd.index("-map_metadata"):cmd.index("-map_metadata")] = ["-q:a", str(quality)]
        else:
            cmd[cmd.index("-map_metadata"):cmd.index("-map_metadata")] = ["-b:a", f"{effective_bitrate}k"]

        result, keep_accel = run_ffmpeg_with_fallback(cmd, selected_accel)
        if selected_accel and not keep_accel:
            selected_accel = None
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
    parser.add_argument("--bitrate", type=int, default=None, metavar="KBIT",
                        help="Use CBR bitrate in kbps (overrides --quality, useful for size control)")
    parser.add_argument("--target-size-mb", type=float, default=None, metavar="MB",
                        help="Auto-pick bitrate to target total output size (e.g. 700 for CD-R)")
    parser.add_argument("--hwaccel", choices=["auto", "off"], default="auto",
                        help="Hardware acceleration mode for decode: auto or off (default: auto)")
    parser.add_argument("--list", action="store_true",
                        help="List chapters only, do not convert")
    args = parser.parse_args()

    m4b = Path(args.input)
    if not m4b.exists():
        sys.exit(f"ERROR: File not found: {m4b}")
    if m4b.suffix.lower() not in (".m4b", ".m4a", ".aac", ".mp4"):
        print(f"WARNING: Unexpected extension '{m4b.suffix}' — attempting anyway.")

    if args.bitrate is not None and args.bitrate <= 0:
        sys.exit("ERROR: --bitrate must be > 0")
    if args.target_size_mb is not None and args.target_size_mb <= 0:
        sys.exit("ERROR: --target-size-mb must be > 0")
    if args.quality < 0 or args.quality > 9:
        sys.exit("ERROR: --quality must be between 0 and 9")

    check_ffmpeg()

    convert(
        m4b_path    = m4b,
        out_dir     = Path(args.out),
        author      = args.author,
        start_track = args.start_track,
        quality     = args.quality,
        bitrate_kbps= args.bitrate,
        target_size_mb=args.target_size_mb,
        hwaccel_mode=args.hwaccel,
        dry_run     = args.list,
    )


if __name__ == "__main__":
    main()
