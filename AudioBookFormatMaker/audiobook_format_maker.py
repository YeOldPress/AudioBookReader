#!/usr/bin/env python3
"""
AudioBookFormat Maker
=====================
Standalone formatter app for audiobook conversion + EPUB sync generation.

Use as GUI (default):
  python audiobook_format_maker.py
  python audiobook_format_maker.py gui

Use as CLI passthrough:
  python audiobook_format_maker.py convert input.m4b --out ./audio
  python audiobook_format_maker.py sync --audio ./audio --epub ./ebook/book.epub --out ./sync
  python audiobook_format_maker.py sync-rocm-v2 --audio ./audio --epub ./ebook --out ./sync --model medium --device cuda
  python audiobook_format_maker.py inspect ./ebook/OEBPS/Text
"""

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


MODEL_OPTIONS = ["tiny", "base", "small", "medium", "large"]
BACKEND_OPTIONS = [
    "Auto Detect",
    "Apple GPU (MPS/MLX)",
    "AMD GPU (ROCm)",
    "NVIDIA GPU (CUDA)",
    "CPU",
]


def run_script(script_name: str, script_args: list[str]) -> int:
    app_dir = Path(__file__).resolve().parent
    script_path = app_dir / script_name
    if not script_path.exists():
        print(f"ERROR: Missing script: {script_path}")
        return 1

    cmd = [sys.executable, str(script_path), *script_args]
    return subprocess.call(cmd)


class FormatMakerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AudioBookFormat Maker")
        self.root.geometry("980x720")
        self.root.minsize(860, 620)

        self.app_dir = Path(__file__).resolve().parent
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen | None = None

        self.progress_total = 0
        self.progress_current = 0
        self.progress_mode = "idle"

        self._build_ui()
        self.root.after(100, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="both", expand=True)

        title = ttk.Label(top, text="AudioBookFormat Maker", font=("TkDefaultFont", 16, "bold"))
        title.pack(anchor="w")
        subtitle = ttk.Label(
            top,
            text="Convert M4B files and generate Whisper sync JSON with GPU/backend selection.",
        )
        subtitle.pack(anchor="w", pady=(2, 10))

        notebook = ttk.Notebook(top)
        notebook.pack(fill="x")

        convert_tab = ttk.Frame(notebook, padding=10)
        sync_tab = ttk.Frame(notebook, padding=10)
        notebook.add(convert_tab, text="Convert")
        notebook.add(sync_tab, text="Sync")

        self._build_convert_tab(convert_tab)
        self._build_sync_tab(sync_tab)

        progress_frame = ttk.LabelFrame(top, text="Progress", padding=10)
        progress_frame.pack(fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(progress_frame, textvariable=self.status_var).pack(anchor="w")
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(8, 0))

        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(10, 0))
        self.run_convert_btn = ttk.Button(btns, text="Run Convert", command=self._run_convert)
        self.run_sync_btn = ttk.Button(btns, text="Run Sync", command=self._run_sync)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._stop_process, state="disabled")
        self.run_convert_btn.pack(side="left")
        self.run_sync_btn.pack(side="left", padx=(8, 0))
        self.stop_btn.pack(side="right")

        log_frame = ttk.LabelFrame(top, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_frame, height=14, wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _build_convert_tab(self, parent: ttk.Frame):
        self.convert_input_var = tk.StringVar()
        self.convert_out_var = tk.StringVar(value=str((self.app_dir.parent / "audio").resolve()))
        self.convert_author_var = tk.StringVar()
        self.convert_start_track_var = tk.StringVar(value="1")
        self.convert_quality_var = tk.StringVar(value="2")
        self.convert_list_only_var = tk.BooleanVar(value=False)

        self._path_row(parent, "Input .m4b", self.convert_input_var, file_mode=True)
        self._path_row(parent, "Output audio dir", self.convert_out_var, file_mode=False)
        self._entry_row(parent, "Author (optional)", self.convert_author_var)
        self._entry_row(parent, "Start track", self.convert_start_track_var)
        self._entry_row(parent, "Quality (0-9)", self.convert_quality_var)
        ttk.Checkbutton(parent, text="List chapters only (no conversion)", variable=self.convert_list_only_var).pack(anchor="w", pady=(8, 0))

    def _build_sync_tab(self, parent: ttk.Frame):
        self.sync_audio_var = tk.StringVar(value=str((self.app_dir.parent / "audio").resolve()))
        self.sync_epub_var = tk.StringVar(value=str((self.app_dir.parent / "ebook").resolve()))
        self.sync_out_var = tk.StringVar(value=str((self.app_dir.parent / "sync").resolve()))
        self.sync_model_var = tk.StringVar(value="base")
        self.sync_backend_var = tk.StringVar(value="Auto Detect")
        self.sync_tracks_var = tk.StringVar()
        self.sync_resume_var = tk.BooleanVar(value=True)
        self.rocm_env_var = tk.BooleanVar(value=True)

        self._path_row(parent, "Audio directory", self.sync_audio_var, file_mode=False)
        self._path_row(parent, "EPUB path (file or dir)", self.sync_epub_var, file_mode=True)
        self._path_row(parent, "Output sync dir", self.sync_out_var, file_mode=False)

        model_row = ttk.Frame(parent)
        model_row.pack(fill="x", pady=(8, 0))
        ttk.Label(model_row, text="Whisper model", width=20).pack(side="left")
        ttk.Combobox(model_row, textvariable=self.sync_model_var, values=MODEL_OPTIONS, state="readonly", width=14).pack(side="left")

        backend_row = ttk.Frame(parent)
        backend_row.pack(fill="x", pady=(8, 0))
        ttk.Label(backend_row, text="Hardware backend", width=20).pack(side="left")
        ttk.Combobox(
            backend_row,
            textvariable=self.sync_backend_var,
            values=BACKEND_OPTIONS,
            state="readonly",
            width=26,
        ).pack(side="left")

        self._entry_row(parent, "Tracks (optional, e.g. 3 4 5)", self.sync_tracks_var)
        ttk.Checkbutton(parent, text="Resume (skip existing sync JSON files)", variable=self.sync_resume_var).pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(
            parent,
            text="Set ROCm compatibility env vars for AMD backend",
            variable=self.rocm_env_var,
        ).pack(anchor="w", pady=(4, 0))

    def _path_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, file_mode: bool):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text=label, width=20).pack(side="left")
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        if file_mode:
            ttk.Button(row, text="Browse", command=lambda: self._browse_file_or_dir(var)).pack(side="left")
        else:
            ttk.Button(row, text="Browse", command=lambda: self._browse_dir(var)).pack(side="left")

    def _entry_row(self, parent: ttk.Frame, label: str, var: tk.StringVar):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text=label, width=20).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

    def _browse_dir(self, var: tk.StringVar):
        selected = filedialog.askdirectory(initialdir=var.get() or str(self.app_dir.parent))
        if selected:
            var.set(selected)

    def _browse_file_or_dir(self, var: tk.StringVar):
        current = Path(var.get()) if var.get() else self.app_dir.parent
        file_selected = filedialog.askopenfilename(initialdir=str(current.parent if current.exists() else self.app_dir.parent))
        if file_selected:
            var.set(file_selected)
            return
        dir_selected = filedialog.askdirectory(initialdir=str(current.parent if current.exists() else self.app_dir.parent))
        if dir_selected:
            var.set(dir_selected)

    def _append_log(self, text: str):
        self.log.insert("end", text)
        self.log.see("end")

    def _set_running(self, running: bool):
        self.run_convert_btn.configure(state="disabled" if running else "normal")
        self.run_sync_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _reset_progress(self, mode: str):
        self.progress_mode = mode
        self.progress_total = 0
        self.progress_current = 0
        self.progress.configure(mode="determinate", maximum=100, value=0)

    def _set_progress(self, current: int, total: int):
        total = max(total, 1)
        current = max(0, min(current, total))
        self.progress.configure(mode="determinate", maximum=total, value=current)

    def _update_progress_from_line(self, line: str):
        if self.progress_mode == "convert":
            total_match = re.search(r"Chapters:\s*(\d+)", line)
            if total_match:
                self.progress_total = int(total_match.group(1))
                self._set_progress(self.progress_current, self.progress_total)
            if re.search(r"^\s*\[\d+\]", line):
                self.progress_current += 1
                if self.progress_total:
                    self._set_progress(self.progress_current, self.progress_total)

        if self.progress_mode == "sync":
            total_match = re.search(r"Chapters with EPUB text\s*:\s*(\d+)", line)
            if total_match:
                self.progress_total = int(total_match.group(1))
                self._set_progress(self.progress_current, self.progress_total)

            tracks_match = re.search(r"After --tracks filter\s*:\s*(\d+)", line)
            if tracks_match:
                self.progress_total = int(tracks_match.group(1))
                self._set_progress(self.progress_current, self.progress_total)

            resume_match = re.search(r"After --resume filter\s*:\s*(\d+)", line)
            if resume_match:
                self.progress_total = int(resume_match.group(1))
                self.progress_current = 0
                self._set_progress(self.progress_current, self.progress_total)

            if re.search(r"^\s*Track\s+\d+:", line):
                self.progress_current += 1
                if self.progress_total:
                    self._set_progress(self.progress_current, self.progress_total)

            tqdm_match = re.search(r"(\d+)/(\d+)", line)
            if tqdm_match:
                self.progress_current = int(tqdm_match.group(1))
                self.progress_total = int(tqdm_match.group(2))
                self._set_progress(self.progress_current, self.progress_total)

    def _build_sync_command(self) -> tuple[str, list[str], dict[str, str]]:
        audio_dir = self.sync_audio_var.get().strip()
        epub_path = self.sync_epub_var.get().strip()
        out_dir = self.sync_out_var.get().strip()
        model = self.sync_model_var.get().strip()
        backend = self.sync_backend_var.get().strip()
        tracks_raw = self.sync_tracks_var.get().strip()

        if not audio_dir or not epub_path or not out_dir:
            raise ValueError("Sync requires audio directory, EPUB path, and output directory.")
        if model not in MODEL_OPTIONS:
            raise ValueError(f"Invalid model: {model}")

        script = "sync_audiobook.py"
        cmd = ["--audio", audio_dir, "--epub", epub_path, "--out", out_dir, "--model", model]
        env = os.environ.copy()

        if backend == "Apple GPU (MPS/MLX)":
            cmd.extend(["--device", "mps"])
        elif backend == "AMD GPU (ROCm)":
            script = "sync_audiobook_amd_rocm_v2.py"
            cmd.extend(["--device", "cuda"])
            if self.rocm_env_var.get():
                env.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")
        elif backend == "NVIDIA GPU (CUDA)":
            cmd.extend(["--device", "cuda"])
        elif backend == "CPU":
            cmd.extend(["--device", "cpu"])

        if tracks_raw:
            track_tokens = tracks_raw.split()
            if not all(t.isdigit() for t in track_tokens):
                raise ValueError("Tracks must be space-separated integers (example: 3 4 5).")
            cmd.append("--tracks")
            cmd.extend(track_tokens)

        if self.sync_resume_var.get():
            cmd.append("--resume")

        return script, cmd, env

    def _run_convert(self):
        input_file = self.convert_input_var.get().strip()
        out_dir = self.convert_out_var.get().strip()
        if not input_file or not out_dir:
            messagebox.showerror("Missing fields", "Convert requires input .m4b and output directory.")
            return

        args = [input_file, "--out", out_dir]
        author = self.convert_author_var.get().strip()
        if author:
            args.extend(["--author", author])

        start_track = self.convert_start_track_var.get().strip()
        quality = self.convert_quality_var.get().strip()
        if not start_track.isdigit():
            messagebox.showerror("Invalid start track", "Start track must be an integer.")
            return
        if not quality.isdigit() or not (0 <= int(quality) <= 9):
            messagebox.showerror("Invalid quality", "Quality must be an integer from 0 to 9.")
            return

        args.extend(["--start-track", start_track, "--quality", quality])
        if self.convert_list_only_var.get():
            args.append("--list")

        self._run_subprocess("convert_m4b.py", args, "convert", os.environ.copy())

    def _run_sync(self):
        try:
            script, args, env = self._build_sync_command()
        except ValueError as exc:
            messagebox.showerror("Invalid sync settings", str(exc))
            return

        self._run_subprocess(script, args, "sync", env)

    def _run_subprocess(self, script_name: str, args: list[str], mode: str, env: dict[str, str]):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "A job is already running.")
            return

        script_path = self.app_dir / script_name
        if not script_path.exists():
            messagebox.showerror("Missing script", f"Missing script: {script_path}")
            return

        cmd = [sys.executable, "-u", str(script_path), *args]
        self._reset_progress(mode)
        self.status_var.set("Running")
        self._set_running(True)
        self._append_log(f"\n$ {' '.join(cmd)}\n")

        def worker():
            try:
                self.process = subprocess.Popen(
                    cmd,
                    cwd=str(self.app_dir.parent),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.output_queue.put(("line", line))
                code = self.process.wait()
                self.output_queue.put(("done", str(code)))
            except Exception as exc:
                self.output_queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _stop_process(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status_var.set("Stopping...")

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "line":
                    self._append_log(payload)
                    self._update_progress_from_line(payload)
                elif kind == "done":
                    code = int(payload)
                    if code == 0:
                        self.status_var.set("Completed")
                        if self.progress_total:
                            self._set_progress(self.progress_total, self.progress_total)
                    else:
                        self.status_var.set(f"Failed (exit {code})")
                    self._set_running(False)
                    self.process = None
                elif kind == "error":
                    self._append_log(f"\nERROR: {payload}\n")
                    self.status_var.set("Failed")
                    self._set_running(False)
                    self.process = None
        except queue.Empty:
            pass

        self.root.after(100, self._drain_queue)

    def _on_close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Quit", "A job is still running. Stop it and quit?"):
                return
            self.process.terminate()
        self.root.destroy()


def launch_gui() -> int:
    root = tk.Tk()
    FormatMakerGUI(root)
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="AudioBookFormat Maker",
        description="Standalone formatter app for audiobook/EPUB sync generation.",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_gui = sub.add_parser("gui", help="Launch desktop GUI")
    p_gui.add_argument("args", nargs=argparse.REMAINDER)

    p_convert = sub.add_parser("convert", help="Split .m4b into chapter MP3 files")
    p_convert.add_argument("args", nargs=argparse.REMAINDER)

    p_sync = sub.add_parser("sync", help="Generate sync JSON with default backend")
    p_sync.add_argument("args", nargs=argparse.REMAINDER)

    p_sync_rocm = sub.add_parser("sync-rocm", help="Generate sync JSON with AMD/ROCm backend")
    p_sync_rocm.add_argument("args", nargs=argparse.REMAINDER)

    p_sync_rocm_v2 = sub.add_parser("sync-rocm-v2", help="Generate sync JSON with AMD/ROCm backend v2")
    p_sync_rocm_v2.add_argument("args", nargs=argparse.REMAINDER)

    p_inspect = sub.add_parser("inspect", help="Inspect EPUB XHTML layout")
    p_inspect.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if args.command in (None, "gui"):
        return launch_gui()
    if args.command == "convert":
        return run_script("convert_m4b.py", args.args)
    if args.command == "sync":
        return run_script("sync_audiobook.py", args.args)
    if args.command == "sync-rocm":
        return run_script("sync_audiobook_amd_rocm.py", args.args)
    if args.command == "sync-rocm-v2":
        return run_script("sync_audiobook_amd_rocm_v2.py", args.args)
    if args.command == "inspect":
        return run_script("inspect_epub.py", args.args)

    print("ERROR: Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
