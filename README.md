# AudioBook Reader

A synchronized audiobook + ebook reader that highlights text in real time as the audio plays. Built around a Whisper-powered alignment pipeline that maps audio timestamps to EPUB paragraphs, with a dark-themed web UI that works both as a standalone browser app and an Electron desktop app.

Originally built for *Heaven's River* (Bobiverse #4) by Dennis E. Taylor, but the tooling is general-purpose.

## Apps

- **Player app**: the web/Electron reader (`reader.html`, `main.js`, `launch.py`)
- **AudioBookFormat Maker app**: standalone formatter in `AudioBookFormatMaker/` for conversion + sync JSON generation

---

## Features

- **Synchronized reading** — paragraphs highlight automatically as the narrator speaks
- **Chapter sidebar** — click any chapter to jump instantly
- **Audio player** — play/pause, seek, skip ±30 s, playback speed control
- **Dark UI** — comfortable for long reading sessions
- **Two launch modes** — browser (`launch.py`) or Electron desktop app
- **Multi-platform Whisper backends** — Apple Silicon (`mlx-whisper`), AMD/ROCm, or CPU (`openai-whisper`)

---

## Project Structure

```
AudioBookReader/
├── AudioBookFormatMaker/   # Standalone sync/format app
│   ├── audiobook_format_maker.py
│   ├── convert_m4b.py
│   ├── sync_audiobook.py
│   ├── sync_audiobook_amd_rocm.py
│   ├── sync_audiobook_amd_rocm_v2.py
│   ├── inspect_epub.py
│   └── requirements.txt
│
├── reader.html              # Main reader UI
├── audiobook-reader.html    # Alternate reader UI variant
├── main.js                  # Electron main process
├── preload.js               # Electron preload script
├── launch.py                # Browser-mode launcher (simple HTTP server)
├── package.json             # Electron app manifest
│
├── audio/                   # Per-chapter MP3 files (output of convert_m4b.py)
├── ebook/                   # Source EPUB file(s)
├── sync/                    # Per-chapter JSON sync files (output of sync_audiobook.py)
└── requirements.txt         # Python dependencies
```

---

## Setup

### Prerequisites

- Python 3.10+
- Node.js + npm (only needed for the Electron app)
- `ffmpeg` / `ffprobe` (needed for `convert_m4b.py`)
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

### Python dependencies

```bash
pip install -r AudioBookFormatMaker/requirements.txt
```

The default `requirements.txt` targets Apple Silicon via `mlx-whisper`. For other platforms, replace it with `openai-whisper`:

```bash
pip install openai-whisper rapidfuzz beautifulsoup4 lxml tqdm
```

### Electron (optional)

```bash
npm install
```

---

## Usage

### Step 1 — Convert M4B to MP3 chapters

You can now run conversion and sync from the GUI:

```bash
npm run format-maker:gui
```

The GUI includes:
- Whisper model selector (`tiny` → `large`)
- Hardware backend selector (Apple GPU, AMD GPU, NVIDIA GPU, CPU, Auto)
- Live progress bar and full log output
- Convert mode always outputs **mono MP3** (audiobook-friendly)
- Optional GPU decode acceleration for faster convert, with automatic CPU fallback
- CD-R fit toggle (700 MB target) with live size estimate while selecting the source file

Node.js launcher is also available:

```bash
node AudioBookFormatMaker/audiobook_format_maker.js --help
npm run format-maker -- --help
```

Example (Node.js sync):

```bash
npm run format-maker -- sync --audio ./audio --epub ./ebook/book.epub --out ./sync --model medium
```

CLI usage is still available:

```bash
python AudioBookFormatMaker/audiobook_format_maker.py convert input.m4b --out ./audio

# With author name embedded in filenames
python AudioBookFormatMaker/audiobook_format_maker.py convert input.m4b --out ./audio --author "Dennis E. Taylor"

# Just list chapters without converting
python AudioBookFormatMaker/audiobook_format_maker.py convert input.m4b --list
```

| Option | Default | Description |
|---|---|---|
| `--out DIR` | *(required)* | Output directory for MP3 files |
| `--author NAME` | — | Author name embedded in filenames |
| `--start-track N` | `1` | Starting track number |
| `--quality 0–9` | `2` | MP3 VBR quality (0 = best, 9 = smallest) |
| `--list` | — | List chapters only, no conversion |

### Step 2 — Generate sync files

```bash
python AudioBookFormatMaker/audiobook_format_maker.py sync --audio ./audio --epub ./ebook/book.epub --out ./sync

# Process specific tracks only (useful for testing)
python AudioBookFormatMaker/audiobook_format_maker.py sync --audio ./audio --epub ./ebook/book.epub --out ./sync --tracks 3 4 5

# Use a more accurate Whisper model
python AudioBookFormatMaker/audiobook_format_maker.py sync --audio ./audio --epub ./ebook/book.epub --out ./sync --model small
```

| Whisper model | Speed (CPU) | Accuracy |
|---|---|---|
| `tiny` | ~4× real-time | Low |
| `base` | ~10× real-time | Good *(default)* |
| `small` | ~20× real-time | Better |
| `medium` | ~40× real-time | High |
| `large` | GPU recommended | Best |

For AMD GPU acceleration, use `sync_audiobook_amd_rocm_v2.py` instead.

```bash
python AudioBookFormatMaker/audiobook_format_maker.py sync-rocm-v2 --audio ./audio --epub ./ebook --out ./sync --model medium --device cuda
```

#### Inspect EPUB structure (optional diagnostic)

```bash
python AudioBookFormatMaker/audiobook_format_maker.py inspect ./ebook/OEBPS/Text/
```

Prints each XHTML chapter file with its heading, character count, and a text preview — useful for verifying the EPUB matches the audio before running the full sync.

### Step 3 — Launch the reader

**Browser mode (recommended for most setups):**

```bash
python launch.py
# Opens http://localhost:8080/reader.html automatically

python launch.py --port 9000   # Custom port
```

**Electron desktop app:**

```bash
npm start
```

---

## How the Sync Works

1. `convert_m4b.py` uses `ffprobe` to read chapter markers from the M4B file and `ffmpeg` to export each chapter as an MP3.
2. `sync_audiobook.py` transcribes each MP3 with Whisper (word-level timestamps), then uses fuzzy string matching (`rapidfuzz`) to align each transcribed segment to the corresponding paragraph in the EPUB's XHTML.
3. The output is one JSON file per chapter in `sync/`, each containing an array of `{ start, end, paragraphIndex }` entries.
4. `reader.html` loads these JSON files at runtime and updates the highlighted paragraph as the audio's `currentTime` advances.

---

## License

MIT