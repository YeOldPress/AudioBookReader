#!/usr/bin/env python3
"""
sync_audiobook.py
=================
Aligns audiobook MP3 files to their corresponding EPUB XHTML text using
OpenAI Whisper for transcription and fuzzy string matching for paragraph
alignment. Produces a JSON sync file per chapter.

Requirements:
    pip install openai-whisper rapidfuzz beautifulsoup4 tqdm lxml

Usage:
    python sync_audiobook.py --audio /path/to/mp3s --epub /path/to/xhtmls --out ./sync_output

    # Only process specific tracks (useful for testing):
    python sync_audiobook.py --audio ... --epub ... --out ... --tracks 3 4 5

    # Use a larger/more accurate Whisper model:
    python sync_audiobook.py --audio ... --epub ... --out ... --model small

Model size guide:
    tiny   — fastest (~4× real-time on CPU), lower accuracy
    base   — good balance (default, ~10× real-time on CPU)
    small  — better accuracy (~20× real-time on CPU)
    medium — very accurate (~40× real-time on CPU)
    large  — best accuracy (needs a GPU or lots of patience)
"""

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

# ── third-party (installed via pip) ──────────────────────────────────────────

# Prefer mlx-whisper (Apple Silicon — much faster), fall back to openai-whisper
try:
    import mlx_whisper
    MLX = True
except ImportError:
    MLX = False
    try:
        import whisper
    except ImportError:
        sys.exit(
            "ERROR: No Whisper backend found.\n"
            "  Apple Silicon (fastest): pip install mlx-whisper\n"
            "  Any machine:             pip install openai-whisper"
        )

try:
    from rapidfuzz import fuzz
except ImportError:
    sys.exit("ERROR: rapidfuzz not installed.\n  pip install rapidfuzz")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("ERROR: beautifulsoup4 not installed.\n  pip install beautifulsoup4")

try:
    from tqdm import tqdm
except ImportError:
    # tqdm is optional — fall back to a no-op wrapper
    class tqdm:  # noqa: F811
        def __init__(self, iterable, **_): self._it = iterable
        def __iter__(self): return iter(self._it)


# ═════════════════════════════════════════════════════════════════════════════
# EPUB ZIP ABSTRACTION
# ═════════════════════════════════════════════════════════════════════════════

class VirtualPath:
    """
    A file reference that lives inside a zip archive, duck-typing the Path
    methods used by this script (.name, .stem, .suffix, .read_bytes(),
    .read_text(), .exists()).
    """
    __slots__ = ("_zf", "_key", "name", "stem", "suffix")

    def __init__(self, zf: zipfile.ZipFile, key: str):
        self._zf    = zf
        self._key   = key
        _p          = Path(key)
        self.name   = _p.name
        self.stem   = _p.stem
        self.suffix = _p.suffix

    def read_bytes(self) -> bytes:
        return self._zf.read(self._key)

    def read_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.read_bytes().decode(encoding, errors)

    def exists(self) -> bool:
        try:
            self._zf.getinfo(self._key)
            return True
        except KeyError:
            return False

    def __str__(self)  -> str:  return self._key
    def __repr__(self) -> str:  return f"VirtualPath({self._key!r})"
    def __lt__(self, other)     -> bool:
        return self.name < (other.name if hasattr(other, "name") else str(other))


class EpubSource:
    """
    Uniform interface for accessing EPUB content whether the source is:
      - An extracted directory  (e.g. /path/to/OEBPS/Text  or  /path/to/OEBPS)
      - A raw .epub zip file    (e.g. /path/to/book.epub)
    """

    def __init__(self, epub_arg: Path):
        self._zf:  zipfile.ZipFile | None = None
        self._dir: Path | None            = None

        if epub_arg.is_file() and epub_arg.suffix.lower() in (".epub", ".zip"):
            self._zf    = zipfile.ZipFile(epub_arg, "r")
            self._names = set(self._zf.namelist())
        elif epub_arg.is_dir():
            # If the directory contains a single .epub file, use it directly
            epub_files = list(epub_arg.glob("*.epub"))
            if epub_files:
                chosen = epub_files[0]
                if len(epub_files) > 1:
                    print(f"  NOTE: multiple .epub files found — using {chosen.name}")
                else:
                    print(f"  Found epub: {chosen.name}")
                self._zf    = zipfile.ZipFile(chosen, "r")
                self._names = set(self._zf.namelist())
            else:
                self._dir = epub_arg
        else:
            sys.exit(f"ERROR: --epub must be a .epub file or a directory: {epub_arg}")

    # ── TOC discovery ─────────────────────────────────────────────────────────

    def find_toc(self) -> tuple[str, bytes, str] | None:
        """
        Returns (toc_id, content_bytes, toc_dir_id) or None.
        For zip mode:  toc_id / toc_dir_id are internal zip paths.
        For dir mode:  toc_id / toc_dir_id are filesystem path strings.
        """
        if self._zf:
            # Prefer .ncx (always has full chapter list)
            for name in sorted(self._names):
                if name.endswith(".ncx"):
                    toc_dir = name.rsplit("/", 1)[0] if "/" in name else ""
                    return name, self._zf.read(name), toc_dir
            # NAV fallback
            for name in sorted(self._names):
                if re.search(r"(nav|toc)\.(xhtml|html)$", name, re.I):
                    toc_dir = name.rsplit("/", 1)[0] if "/" in name else ""
                    return name, self._zf.read(name), toc_dir
            return None
        else:
            toc_path = find_toc_file(self._dir)
            if toc_path:
                return str(toc_path), toc_path.read_bytes(), str(toc_path.parent)
            return None

    # ── XHTML resolution ──────────────────────────────────────────────────────

    def resolve_xhtml(self, href: str, toc_dir: str):
        """
        Resolve a TOC href relative to toc_dir.
        Returns a VirtualPath (zip mode) or Path (dir mode), or None.
        """
        href = href.split("#")[0]
        if not href:
            return None

        if self._zf:
            # Normalize: toc_dir + "/" + href  →  remove . and .. segments
            raw_parts = (f"{toc_dir}/{href}" if toc_dir else href).split("/")
            resolved: list[str] = []
            for part in raw_parts:
                if part == "..":
                    if resolved:
                        resolved.pop()
                elif part and part != ".":
                    resolved.append(part)
            key = "/".join(resolved)
            if key in self._names:
                return VirtualPath(self._zf, key)
            # Fallback: match by filename alone
            fname = Path(href).name
            for name in self._names:
                if name.endswith("/" + fname) or name == fname:
                    return VirtualPath(self._zf, name)
            return None
        else:
            return _resolve_href(href, Path(toc_dir), self._dir)

    def close(self):
        if self._zf:
            self._zf.close()
            self._zf = None


def _parse_toc_from_source(source: EpubSource) -> list[dict]:
    """
    Parse the TOC from an EpubSource (works for both zip and directory).
    Returns an ordered list of dicts: {toc_title, toc_label, xhtml_path}
    """
    toc = source.find_toc()
    if not toc:
        return []
    toc_id, raw, toc_dir = toc

    if toc_id.endswith(".ncx"):
        soup = BeautifulSoup(raw, "xml")
        entries = []
        for nav_point in soup.find_all("navPoint"):
            label_el   = nav_point.find("navLabel")
            content_el = nav_point.find("content")
            if not label_el or not content_el:
                continue
            title = label_el.get_text(" ", strip=True)
            href  = content_el.get("src", "")
            path  = source.resolve_xhtml(href, toc_dir)
            if path and title:
                label = re.sub(r"^\d+\.\s*", "", title).strip()
                entries.append({"toc_title": title, "toc_label": label, "xhtml_path": path})
        return entries
    else:
        soup = BeautifulSoup(raw, "html.parser")
        nav  = soup.find("nav") or soup.find("ol") or soup.body
        entries = []
        if not nav:
            return entries
        for a in nav.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            path  = source.resolve_xhtml(a["href"], toc_dir)
            if path and title:
                label = re.sub(r"^\d+\.\s*", "", title).strip()
                entries.append({"toc_title": title, "toc_label": label, "xhtml_path": path})
        return entries


# ═════════════════════════════════════════════════════════════════════════════
# CHAPTER / FILENAME PARSING
# ═════════════════════════════════════════════════════════════════════════════

WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-one": 21, "twenty-two": 22, "twenty-three": 23, "twenty-four": 24,
    "twenty-five": 25, "twenty-six": 26, "twenty-seven": 27,
    "twenty-eight": 28, "twenty-nine": 29, "thirty": 30,
    "thirty-one": 31, "thirty-two": 32, "thirty-three": 33, "thirty-four": 34,
    "thirty-five": 35,
}


def word_to_num(s: str):
    s = s.lower().strip()
    if s.isdigit():
        return int(s)
    return WORD_TO_NUM.get(s)


def parse_audio_filename(name: str) -> dict:
    """
    Parse an audio filename like:
        '03 - Dennis E. Taylor - Chapter One Frenemies.mp3'

    Returns a dict with keys: track, kind, title, part, ch
    kind ∈ {'cred', 'part', 'ch', 'other'}
    """
    base = Path(name).stem
    parts = base.split(" - ", 2)
    try:
        track = int(parts[0])
    except (ValueError, IndexError):
        track = 0
    desc = parts[2].strip() if len(parts) > 2 else base

    if re.search(r"opening credits", desc, re.I):
        return {"track": track, "kind": "cred", "title": "Opening Credits", "part": 0, "ch": None}
    if re.search(r"end credits", desc, re.I):
        return {"track": track, "kind": "cred", "title": "End Credits", "part": 0, "ch": None}

    pm = re.search(r"\bpart\s+([\w-]+)", desc, re.I)
    if pm and not re.search(r"\bchapter\b", desc, re.I):
        pn = word_to_num(pm.group(1)) or (2 if pm.group(1).lower() == "two" else 1)
        return {"track": track, "kind": "part", "title": desc, "part": pn, "ch": None}

    cm = re.search(r"\bchapter\s+([\w-]+)", desc, re.I)
    if cm:
        ch_num = word_to_num(cm.group(1))
        # Tracks 37+ are Part 2 (Part Two header is track 37, so chapters start at 38)
        part = 2 if track >= 38 else 1
        return {"track": track, "kind": "ch", "title": desc, "part": part, "ch": ch_num}

    return {"track": track, "kind": "other", "title": desc, "part": 0, "ch": None}


# ═════════════════════════════════════════════════════════════════════════════
# EPUB PARSING
# ═════════════════════════════════════════════════════════════════════════════

def extract_paragraphs(xhtml_path) -> list[dict]:
    """
    Extract text blocks from an XHTML file (accepts Path or VirtualPath).
    Returns a list of dicts: {epub_file, tag, text}
    """
    try:
        raw = xhtml_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  WARNING: could not read {xhtml_path.name}: {e}")
        return []

    soup = BeautifulSoup(raw, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "aside", "head"]):
        tag.decompose()

    paras = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "p"]):
        text = tag.get_text(separator=" ", strip=True)
        # Skip very short/empty blocks and likely page numbers
        if not text or len(text) < 4:
            continue
        if re.match(r"^\d+$", text):  # bare page number
            continue
        paras.append({
            "epub_file": xhtml_path.name,
            "tag": tag.name,
            "text": text,
        })

    return paras


# ═════════════════════════════════════════════════════════════════════════════
# TEXT NORMALIZATION
# ═════════════════════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    """
    Normalize text for fuzzy comparison:
    - Unicode NFKD → ASCII where possible
    - Lowercase
    - Strip punctuation
    - Collapse whitespace
    """
    # Unicode normalization
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    # Remove punctuation
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ═════════════════════════════════════════════════════════════════════════════
# ALIGNMENT  (word-level — much more accurate than segment-level)
# ═════════════════════════════════════════════════════════════════════════════

def extract_words(result: dict) -> list[dict]:
    """
    Flatten per-segment word timestamps into a single list.
    Each entry: {word, start, end}
    Requires word_timestamps=True in the Whisper transcribe call.
    Falls back to segment-level if word timestamps are absent.
    """
    words = []
    for seg in result.get("segments", []):
        seg_words = seg.get("words", [])
        if seg_words:
            for w in seg_words:
                text = w.get("word", "").strip()
                if text:
                    words.append({
                        "word":  text,
                        "start": round(float(w.get("start", seg["start"])), 3),
                        "end":   round(float(w.get("end",   seg["end"])),   3),
                    })
        else:
            # No word timestamps — treat whole segment as one token
            text = seg.get("text", "").strip()
            if text:
                words.append({
                    "word":  text,
                    "start": round(float(seg["start"]), 3),
                    "end":   round(float(seg["end"]),   3),
                })
    return words


def align_by_words(words: list[dict], paragraphs: list[dict]) -> list[dict]:
    """
    Align epub paragraphs to audio timestamps using Whisper word timestamps.

    For each paragraph we match its first N normalized words against the
    rolling word stream (monotonic cursor).  Word-level timestamps give
    ±0.2 s accuracy vs. ±3–5 s from segment matching.

    Returns a list of dicts: paragraph fields + start, end, match_score
    """
    if not words or not paragraphs:
        return []

    norm_words = [normalize(w["word"]) for w in words]
    results    = []
    cursor     = 0        # monotonic: search starts here
    LOOKAHEAD  = 500      # search at most this many words ahead of cursor
    MATCH_N    = 8        # use first N words of each paragraph for matching
    # When a heading para fails to match, don't stall the cursor — use a wider
    # look-ahead for the *next* para to get back on track.
    low_conf_streak = 0

    for para in paragraphs:
        para_norm  = normalize(para["text"])
        para_split = para_norm.split()

        # Very short paragraph — inherit cursor timestamp
        if len(para_split) < 3:
            ts = words[min(cursor, len(words) - 1)]["start"]
            results.append({**para, "start": ts, "end": ts, "match_score": 0.0})
            continue

        # Build the search key: first MATCH_N words of paragraph
        n_key      = min(MATCH_N, len(para_split))
        search_key = " ".join(para_split[:n_key])

        # After consecutive low-conf matches, widen the search to escape a stuck cursor
        lookahead = LOOKAHEAD + low_conf_streak * 200

        search_start = cursor                          # strictly forward — no look-back
        search_end   = min(len(words) - n_key, cursor + lookahead)

        best_score = -1
        best_pos   = cursor

        for i in range(search_start, search_end):
            window = " ".join(norm_words[i : i + n_key])
            score  = fuzz.ratio(search_key, window)
            if score > best_score:
                best_score = score
                best_pos   = i
            if score >= 97:          # near-perfect → stop early
                break

        # Advance cursor monotonically
        if best_pos >= cursor:
            cursor = best_pos

        if best_score >= 50:
            low_conf_streak = 0
        else:
            low_conf_streak += 1

        start_ts = words[best_pos]["start"]
        # Estimate end by advancing word_count words into the stream
        end_pos  = min(best_pos + len(para_split), len(words) - 1)
        end_ts   = words[end_pos]["end"]

        results.append({
            **para,
            "start":       round(start_ts, 3),
            "end":         round(end_ts,   3),
            "match_score": round(best_score / 100, 3),
        })

    return interpolate_stuck_timestamps(results, words[-1]["end"] if words else 0)


def interpolate_stuck_timestamps(sync_map: list[dict], duration: float) -> list[dict]:
    """
    Post-process sync_map: paragraphs whose timestamps are stuck (same as
    predecessor) or have match_score < 0.45 get their timestamps linearly
    interpolated between the nearest high-confidence anchors on either side.
    """
    n = len(sync_map)
    if n < 2:
        return sync_map

    ANCHOR_THRESHOLD = 0.70  # min score to be used as an interpolation anchor

    # Collect anchor indices and times
    anchors = [(i, sync_map[i]["start"]) for i in range(n)
               if sync_map[i]["match_score"] >= ANCHOR_THRESHOLD]

    if not anchors:
        return sync_map  # nothing to work with

    # Ensure boundary anchors
    if anchors[0][0] != 0:
        anchors.insert(0, (0, max(0.0, sync_map[anchors[0][0]]["start"] - 5.0)))
    if anchors[-1][0] != n - 1:
        anchors.append((n - 1, duration))

    result = [dict(p) for p in sync_map]

    # For each gap between consecutive anchors, interpolate low-conf paras
    for (i1, t1), (i2, t2) in zip(anchors, anchors[1:]):
        if i2 - i1 <= 1:
            continue
        for j in range(i1 + 1, i2):
            p = result[j]
            # Interpolate paragraphs that scored below the anchor threshold —
            # these are either unmatched or only coincidentally matched and
            # their raw timestamps are unreliable.
            stuck = (p["start"] == result[j - 1]["start"])
            if p["match_score"] < 0.70 or stuck:
                frac      = (j - i1) / (i2 - i1)
                new_start = round(t1 + frac * (t2 - t1), 3)
                p["start"] = new_start
                p["end"]   = new_start

    # Safety pass: ensure timestamps are strictly non-decreasing so the
    # JS backward scan in findActivePara always gives the correct result.
    running_max = result[0]["start"]
    for p in result[1:]:
        if p["start"] < running_max:
            p["start"] = running_max
            p["end"]   = max(p["end"], running_max)
        else:
            running_max = p["start"]

    return result



# ═════════════════════════════════════════════════════════════════════════════
# EPUB TOC PARSING  (toc.ncx or nav.xhtml)
# ═════════════════════════════════════════════════════════════════════════════

def find_toc_file(epub_text_dir: Path) -> Path | None:
    """
    Search for toc.ncx or a nav document in the OEBPS directory
    (the parent of the Text/ folder, or its parent).
    """
    for search_dir in [epub_text_dir.parent, epub_text_dir.parent.parent, epub_text_dir]:
        if not search_dir.is_dir():
            continue
        # Prefer NCX (simpler to parse, always has chapter list)
        for ncx in sorted(search_dir.glob("*.ncx")):
            return ncx
        # NAV fallback
        for nav in sorted(search_dir.glob("*.xhtml")) + sorted(search_dir.glob("*.html")):
            if "nav" in nav.stem.lower() or "toc" in nav.stem.lower():
                return nav
    return None


def parse_toc(toc_path: Path, epub_text_dir: Path) -> list[dict]:
    """
    Parse toc.ncx or nav.xhtml.
    Returns an ordered list of dicts: {toc_title, toc_label, xhtml_path}
    Only entries that point to an existing xhtml file in epub_text_dir are included.
    """
    raw = toc_path.read_bytes()

    if toc_path.suffix.lower() == ".ncx":
        return _parse_ncx(raw, toc_path.parent, epub_text_dir)
    else:
        return _parse_nav(raw, toc_path.parent, epub_text_dir)


def _resolve_href(href: str, base_dir: Path, epub_text_dir: Path) -> Path | None:
    """Resolve a relative href from the TOC to an absolute Path."""
    if not href:
        return None
    href = href.split("#")[0]          # strip anchor
    candidate = (base_dir / href).resolve()
    if candidate.exists():
        return candidate
    # Try just the filename against the Text/ dir
    candidate2 = epub_text_dir / Path(href).name
    if candidate2.exists():
        return candidate2
    return None


def _parse_ncx(raw: bytes, base_dir: Path, epub_text_dir: Path) -> list[dict]:
    soup = BeautifulSoup(raw, "xml")
    entries = []
    for nav_point in soup.find_all("navPoint"):
        label_el   = nav_point.find("navLabel")
        content_el = nav_point.find("content")
        if not label_el or not content_el:
            continue
        title = label_el.get_text(" ", strip=True)
        href  = content_el.get("src", "")
        path  = _resolve_href(href, base_dir, epub_text_dir)
        if path and title:
            # Strip leading "N. " or "N." from the label to get the bare title
            label = re.sub(r"^\d+\.\s*", "", title).strip()
            entries.append({"toc_title": title, "toc_label": label, "xhtml_path": path})
    return entries


def _parse_nav(raw: bytes, base_dir: Path, epub_text_dir: Path) -> list[dict]:
    soup = BeautifulSoup(raw, "html.parser")
    nav  = soup.find("nav") or soup.find("ol") or soup.body
    entries = []
    if not nav:
        return entries
    for a in nav.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        path  = _resolve_href(a["href"], base_dir, epub_text_dir)
        if path and title:
            label = re.sub(r"^\d+\.\s*", "", title).strip()
            entries.append({"toc_title": title, "toc_label": label, "xhtml_path": path})
    return entries


# ═════════════════════════════════════════════════════════════════════════════
# CHAPTER-TO-EPUB MAPPING
# ═════════════════════════════════════════════════════════════════════════════

def _audio_subtitle(meta: dict) -> str:
    """
    Extract the bare chapter subtitle from a parsed audio filename.
    e.g. "Chapter One Frenemies" → "Frenemies"
         "Chapter Twenty-Three Dancing with Dragons" → "Dancing with Dragons"
    """
    title = meta.get("title", "")
    # Strip "Chapter <word-number> " prefix
    cleaned = re.sub(r"^chapter\s+[\w-]+\s*", "", title, flags=re.I).strip()
    # Also strip "Part N " prefix for part tracks
    cleaned = re.sub(r"^part\s+[\w-]+\s*", "", cleaned, flags=re.I).strip()
    return cleaned


def build_chapter_map(audio_files: list[Path], epub_arg: Path) -> list[dict]:
    """
    Map audio tracks to their epub XHTML files using sequential position matching.

    epub_arg may be:
      - A directory containing extracted XHTML files (e.g. OEBPS/Text/)
      - A raw .epub zip file

    Strategy:
      1. Find toc.ncx (or nav.xhtml) inside the epub source.
      2. Extract all TOC entries that point to real XHTML files, in TOC order.
         Filter out obvious non-chapter entries (Part headers, copyright, etc.)
      3. Sort chapter audio tracks by track number.
      4. Zip them 1:1 positionally — no fuzzy matching.

    Returns a list of dicts: {audio, meta, epub_paths}
    """
    # ── Open EPUB source (zip or directory) ───────────────────────────────────
    source = EpubSource(epub_arg)

    # ── Find and parse the TOC ────────────────────────────────────────────────
    toc_info = source.find_toc()
    if not toc_info:
        print("  WARNING: No toc.ncx / nav file found. Will attempt body-text fallback.")
        toc_entries = []
    else:
        print(f"  Using TOC: {toc_info[0]}")
        toc_entries = _parse_toc_from_source(source)
        print(f"  TOC has {len(toc_entries)} entries")

    # Only keep TOC entries that point to actual xhtml files
    valid_toc = [e for e in toc_entries if e["xhtml_path"].exists()]

    # ── Build ordered list of chapter audio tracks ────────────────────────────
    all_sorted = sorted(audio_files, key=lambda p: p.name)
    chapter_audio = [
        af for af in all_sorted
        if parse_audio_filename(af.name)["kind"] == "ch"
    ]

    # ── Build ordered list of TOC chapter entries ─────────────────────────────
    # Sort by the xhtml filename (part0009, part0010, …) — this is the natural
    # reading order and matches the audio track order.
    # Filter to entries whose toc_title starts with a number (e.g. "1. Frenemies")
    # which selects real chapters and skips front matter and part headers.
    chapter_toc = sorted(
        [e for e in valid_toc if re.match(r"^\d+\.\s", e["toc_title"])],
        key=lambda e: e["xhtml_path"].name,
    )
    # If the TOC has no numbered entries, fall back to all valid entries
    if not chapter_toc:
        chapter_toc = sorted(valid_toc, key=lambda e: e["xhtml_path"].name)

    if chapter_toc:
        print(f"  Sequential mapping: {len(chapter_audio)} audio chapters → "
              f"{len(chapter_toc)} TOC entries")
        if len(chapter_audio) != len(chapter_toc):
            print(f"  WARNING: count mismatch — "
                  f"{len(chapter_audio)} audio vs {len(chapter_toc)} TOC entries")
    else:
        print("  WARNING: no TOC entries found — epub files will be empty")

    # ── Zip chapter audio ↔ TOC entries positionally ─────────────────────────
    toc_by_position = {chapter_audio[i]: chapter_toc[i]
                       for i in range(min(len(chapter_audio), len(chapter_toc)))}

    chapter_map = []
    for af in all_sorted:
        meta = parse_audio_filename(af.name)
        epub_paths = []

        if meta["kind"] == "ch":
            entry = toc_by_position.get(af)
            if entry:
                epub_paths = [entry["xhtml_path"]]
            else:
                print(f"  WARNING: no TOC entry for '{meta['title']}'")

        chapter_map.append({"audio": af, "meta": meta, "epub_paths": epub_paths})

    matched = sum(1 for c in chapter_map if c["epub_paths"])
    print(f"  Matched {matched} / {len(chapter_map)} audio tracks to epub files")
    return chapter_map, source


# ═════════════════════════════════════════════════════════════════════════════
# PER-CHAPTER PROCESSING
# ═════════════════════════════════════════════════════════════════════════════

def process_chapter(item: dict, transcribe_fn, out_dir: Path) -> dict | None:
    """
    Transcribe one audio file and align it to its epub paragraphs.
    Saves a JSON file to out_dir and returns a summary dict.
    transcribe_fn: callable(audio_path_str) → Whisper result dict
    """
    audio_path = item["audio"]
    epub_paths = item["epub_paths"]
    meta       = item["meta"]

    print(f"\n{'─'*60}")
    print(f"  Track {meta['track']:02d}: {meta['title']}")
    print(f"  EPUB: {[p.name for p in epub_paths] if epub_paths else '(none)'}")

    # Collect paragraphs from all epub files for this chapter
    all_paragraphs = []
    for ep in sorted(epub_paths, key=lambda p: p.name):
        all_paragraphs.extend(extract_paragraphs(ep))

    if not all_paragraphs:
        print("  SKIP — no epub paragraphs found")
        return None

    print(f"  Transcribing with word timestamps… ({len(all_paragraphs)} paragraphs to align)")
    result = transcribe_fn(str(audio_path))

    # Flatten word timestamps
    words    = extract_words(result)
    segments = [
        {"start": round(s["start"], 3), "end": round(s["end"], 3),
         "text": s["text"].strip(), "words": s.get("words", [])}
        for s in result.get("segments", []) if s.get("text", "").strip()
    ]
    duration = round(result.get("duration") or (words[-1]["end"] if words else 0), 3)

    if words:
        print(f"  Whisper: {len(words)} words, {len(segments)} segments — word-aligning…")
        sync_map = align_by_words(words, all_paragraphs)
    else:
        # Fallback: no word timestamps available (shouldn't happen with current backends)
        print(f"  WARNING: no word timestamps — falling back to segment alignment")
        sync_map = align_by_words(
            [{"word": s["text"], "start": s["start"], "end": s["end"]} for s in segments],
            all_paragraphs,
        )

    # Score summary
    if sync_map:
        avg_score = sum(p["match_score"] for p in sync_map) / len(sync_map)
        low_count = sum(1 for p in sync_map if p["match_score"] < 0.50)
        print(f"  Avg match score: {avg_score:.2f}  |  Low-confidence (<0.50): {low_count}")

    output = {
        "audio_file":   audio_path.name,
        "epub_files":   [p.name for p in epub_paths],
        "chapter_info": meta,
        "duration":     duration,
        "sync_map":     sync_map,   # paragraph → timestamp (what the reader uses)
        # raw Whisper kept for debugging — you can remove these to save disk space
        "whisper_segments": segments,
    }

    out_path = out_dir / f"{audio_path.stem}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ Saved → {out_path.name}")

    return {
        "audio_file":       audio_path.name,
        "epub_files":       [p.name for p in epub_paths],
        "duration":         duration,
        "segment_count":    len(segments),
        "paragraph_count":  len(sync_map),
        "avg_match_score":  round(avg_score if sync_map else 0, 3),
        "sync_file":        out_path.name,
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Sync audiobook MP3s to EPUB XHTML text using Whisper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--audio",   required=True, metavar="DIR",
                        help="Directory containing the MP3 audio files")
    parser.add_argument("--epub",    required=True, metavar="DIR",
                        help="Directory containing the XHTML epub files")
    parser.add_argument("--out",     required=True, metavar="DIR",
                        help="Output directory for sync JSON files")
    parser.add_argument("--model",   default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model (default: base)")
    parser.add_argument("--tracks",  nargs="*", type=int, metavar="N",
                        help="Only process these track numbers, e.g. --tracks 3 4 5")
    parser.add_argument("--resume",  action="store_true",
                        help="Skip tracks that already have a sync JSON in --out")
    parser.add_argument("--device",  default=None,
                        help="Device for Whisper: cpu | mps | cuda (default: auto-detect)")
    args = parser.parse_args()

    audio_dir = Path(args.audio)
    epub_arg  = Path(args.epub)
    out_dir   = Path(args.out)

    if not audio_dir.is_dir():
        sys.exit(f"ERROR: --audio directory not found: {audio_dir}")
    if not epub_arg.exists():
        sys.exit(f"ERROR: --epub not found: {epub_arg}")
    if epub_arg.is_file() and epub_arg.suffix.lower() not in (".epub", ".zip"):
        sys.exit(f"ERROR: --epub must be a directory or a .epub file: {epub_arg}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Find audio files ──────────────────────────────────────────────────────
    audio_exts   = ("*.mp3", "*.m4a", "*.m4b", "*.flac", "*.ogg", "*.wav")
    audio_files  = sorted(
        [p for ext in audio_exts for p in audio_dir.glob(ext)],
        key=lambda p: p.name,
    )
    if not audio_files:
        sys.exit(f"ERROR: No audio files found in {audio_dir}")

    print(f"Found {len(audio_files)} audio file(s) in {audio_dir}")

    # ── Build chapter map ─────────────────────────────────────────────────────
    print("Building chapter-to-EPUB mapping…")
    chapter_map, _epub_source = build_chapter_map(audio_files, epub_arg)  # keep _epub_source alive

    # Filter to only chapters that have epub text
    with_epub    = [c for c in chapter_map if c["epub_paths"]]
    without_epub = [c for c in chapter_map if not c["epub_paths"]]

    print(f"  Chapters with EPUB text : {len(with_epub)}")
    print(f"  Chapters without (skip) : {len(without_epub)}")
    if without_epub:
        names = [c["meta"]["title"] for c in without_epub]
        print(f"  Skipped: {names}")

    # Apply --tracks filter
    if args.tracks:
        with_epub = [c for c in with_epub if c["meta"]["track"] in args.tracks]
        print(f"  After --tracks filter   : {len(with_epub)} chapter(s)")

    # Apply --resume filter
    if args.resume:
        before = len(with_epub)
        with_epub = [
            c for c in with_epub
            if not (out_dir / f"{c['audio'].stem}.json").exists()
        ]
        print(f"  After --resume filter   : {len(with_epub)} remaining (skipped {before - len(with_epub)})")

    if not with_epub:
        print("Nothing to process. Exiting.")
        return

    # ── Build transcription backend ───────────────────────────────────────────
    MLX_MODELS = {
        "tiny":   "mlx-community/whisper-tiny-mlx",
        "base":   "mlx-community/whisper-base-mlx",
        "small":  "mlx-community/whisper-small-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "large":  "mlx-community/whisper-large-v3-mlx",
    }

    if MLX:
        repo = MLX_MODELS.get(args.model, f"mlx-community/whisper-{args.model}-mlx")
        print(f"\nUsing mlx-whisper  [{repo}]  (Apple Silicon — fastest)")
        print("First run will download model weights (~150 MB for base).")
        transcribe_fn = lambda p: mlx_whisper.transcribe(
            p, path_or_hf_repo=repo, language="en", verbose=False,
            word_timestamps=True,
        )
    else:
        import torch

        def print_torch_gpu_status() -> None:
            """Print useful PyTorch GPU/ROCm diagnostics."""
            print(f"PyTorch version: {torch.__version__}")
            print(f"PyTorch ROCm/HIP: {getattr(torch.version, 'hip', None)}")
            print(f"PyTorch CUDA: {getattr(torch.version, 'cuda', None)}")
            print(f"torch.cuda.is_available(): {torch.cuda.is_available()}")
            if torch.cuda.is_available():
                print(f"GPU: {torch.cuda.get_device_name(0)}")

        def detect_whisper_device() -> str:
            """
            Pick the best Whisper device.

            Notes:
              - AMD GPUs using ROCm appear to PyTorch as device "cuda".
              - NVIDIA CUDA also appears as "cuda".
              - Apple Silicon uses "mps" when available.
            """
            print_torch_gpu_status()

            if args.device:
                requested = args.device.lower()
                if requested == "cuda" and not torch.cuda.is_available():
                    print(
                        "WARNING: --device cuda was requested, but PyTorch does not see a GPU.\n"
                        "         This usually means you installed a CPU-only PyTorch build,\n"
                        "         or ROCm is not working for this Python environment.\n"
                        "         Falling back to CPU so the script does not crash."
                    )
                    return "cpu"
                return requested

            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                hip_version = getattr(torch.version, "hip", None)
                cuda_version = getattr(torch.version, "cuda", None)

                if hip_version:
                    print(f"AMD ROCm/HIP detected: {gpu_name}")
                    print(f"ROCm/HIP version: {hip_version}")
                elif cuda_version:
                    print(f"NVIDIA CUDA detected: {gpu_name}")
                    print(f"CUDA version: {cuda_version}")
                else:
                    print(f"GPU detected through torch.cuda: {gpu_name}")

                return "cuda"

            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                print("Apple Silicon MPS detected.")
                return "mps"

            return "cpu"

        device = detect_whisper_device()

        if device == "cpu":
            print("WARNING: No GPU detected by PyTorch. Whisper will run on CPU.")

        print(f"\nLoading openai-whisper '{args.model}' on {device}…")
        _model = whisper.load_model(args.model, device=device)

        transcribe_fn = lambda p: _model.transcribe(
            p,
            language="en",
            verbose=False,
            word_timestamps=True,
            fp16=(device == "cuda"),
        )
    print("Model ready.\n")

    # ── Process chapters ──────────────────────────────────────────────────────
    index = []
    failed = []

    for item in tqdm(with_epub, desc="Chapters", unit="ch"):
        try:
            summary = process_chapter(item, transcribe_fn, out_dir)
            if summary:
                index.append(summary)
        except Exception as e:
            print(f"\n  ERROR processing {item['audio'].name}: {e}")
            failed.append({"audio": item["audio"].name, "error": str(e)})

    # ── Save master index ─────────────────────────────────────────────────────
    index_data = {
        "whisper_model": args.model,
        "chapters":      index,
        "failed":        failed,
    }
    index_path = out_dir / "_index.json"
    index_path.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  Done!  {len(index)} chapter(s) synced,  {len(failed)} failed.")
    if index:
        overall_avg = sum(c["avg_match_score"] for c in index) / len(index)
        print(f"  Overall avg match score : {overall_avg:.2f}")
    print(f"  Output directory        : {out_dir.resolve()}")
    print(f"  Master index            : {index_path.name}")
    if failed:
        print(f"\n  Failed chapters:")
        for f in failed:
            print(f"    {f['audio']}: {f['error']}")
    print()


if __name__ == "__main__":
    main()
