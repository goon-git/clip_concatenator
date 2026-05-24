#!/usr/bin/env python3
"""
concat_clips.py — Stellar Theory Clip Concatenator (ST-TOOLS-002)

Concatenates video clips in order.
- Probes every clip with ffprobe to detect codec, resolution, and frame rate.
- If all clips are uniform: uses the concat demuxer (lossless, instant).
- If clips differ: uses filter_complex concat (re-encodes to a consistent output).

Usage (CLI):
    python concat_clips.py clip1.mp4 clip2.mp4 clip3.mp4 -o output.mp4
    python concat_clips.py --gui

Dependencies:
    ffmpeg + ffprobe on PATH
    tkinter (stdlib)
    Pillow — optional, for logo display
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from fractions import Fraction
from tkinter import filedialog, messagebox

# ── Colour palette ────────────────────────────────────────────────────────────
C_BG        = "#1e2530"
C_PANEL     = "#252e3d"
C_BORDER    = "#3a4558"
C_RUST      = "#c4572a"
C_MUSTARD   = "#c49a2a"
C_SAGE      = "#7a9e8e"
C_BROWN     = "#6b4226"
C_TEXT      = "#e8dfc8"
C_TEXT_DIM  = "#8a9aaa"
C_SUCCESS   = "#7a9e8e"
C_WARNING   = "#c49a2a"
C_ERROR     = "#c4572a"

# ── Typography ────────────────────────────────────────────────────────────────
FONT_TITLE  = ("Georgia", 22, "bold")
FONT_BRAND  = ("Georgia", 11, "italic")
FONT_LABEL  = ("Courier", 8, "bold")
FONT_VALUE  = ("Courier", 10)
FONT_BUTTON = ("Georgia", 11, "bold")
FONT_LOG    = ("Courier", 9)

logger = logging.getLogger("concat_clips")


# ══════════════════════════════════════════════════════════════════════════════
#  Probe / detection logic
# ══════════════════════════════════════════════════════════════════════════════

def probe_clip(path: str) -> dict:
    """Return codec, width, height, fps for a video file."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path!r}:\n{result.stderr.strip()}")

    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"No video stream found in {path!r}")

    # Parse frame rate — stored as a fraction string e.g. "30000/1001"
    r_frame_rate = video.get("r_frame_rate", "0/1")
    try:
        fps = float(Fraction(r_frame_rate))
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    return {
        "path":  path,
        "codec": video.get("codec_name", "unknown"),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps":   round(fps, 4),
    }


def analyse_clips(paths: list[str]) -> tuple[list[dict], bool]:
    """
    Probe all clips and determine whether they are uniform.

    Returns:
        (infos, uniform)
        uniform=True  → all clips share codec, resolution, and fps
        uniform=False → at least one difference; re-encode required
    """
    infos = []
    for p in paths:
        logger.info(f"Probing: {os.path.basename(p)}")
        info = probe_clip(p)
        infos.append(info)
        logger.info(
            f"  codec={info['codec']}  "
            f"{info['width']}×{info['height']}  "
            f"fps={info['fps']}"
        )

    ref = infos[0]
    uniform = all(
        i["codec"] == ref["codec"] and
        i["width"] == ref["width"] and
        i["height"] == ref["height"] and
        abs(i["fps"] - ref["fps"]) < 0.01
        for i in infos[1:]
    )

    if uniform:
        logger.info("All clips are uniform → using lossless concat demuxer.")
    else:
        logger.warning("Clips differ in codec / resolution / fps → will re-encode.")
        for info in infos:
            logger.debug(
                f"  {os.path.basename(info['path'])}: "
                f"{info['codec']} {info['width']}×{info['height']} {info['fps']}fps"
            )

    return infos, uniform


# ══════════════════════════════════════════════════════════════════════════════
#  FFmpeg concat strategies
# ══════════════════════════════════════════════════════════════════════════════

def concat_lossless(infos: list[dict], output: str) -> None:
    """Concat demuxer — stream-copy, no re-encode. Requires uniform clips."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                    delete=False, encoding="utf-8") as f:
        list_path = f.name
        for info in infos:
            # Escape single quotes in paths for the concat list format
            safe = info["path"].replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output,
        ]
        logger.info(f"Running lossless concat → {output}")
        _run_ffmpeg(cmd)
    finally:
        os.unlink(list_path)


def concat_reencode(infos: list[dict], output: str) -> None:
    """
    filter_complex concat — re-encodes everything to a consistent stream.
    Scales all clips to the largest resolution found among the inputs,
    pads with black bars to maintain aspect ratio.
    """
    n = len(infos)

    # Determine target resolution (largest width found)
    target_w = max(i["width"] for i in infos)
    target_h = max(i["height"] for i in infos)
    # Force even dimensions (required by most codecs)
    target_w += target_w % 2
    target_h += target_h % 2

    target_fps = max(i["fps"] for i in infos)

    logger.info(
        f"Re-encoding: target {target_w}×{target_h} @ {target_fps}fps  "
        f"({n} clips)"
    )

    # Build input flags
    inputs = []
    for info in infos:
        inputs += ["-i", info["path"]]

    # Build filter_complex — scale+pad each input, then concat
    filter_parts = []
    for idx in range(n):
        filter_parts.append(
            f"[{idx}:v]"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={target_fps},"
            f"setsar=1"
            f"[v{idx}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[vout]")
    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "slow",
        "-pix_fmt", "yuv420p",
        output,
    ]
    logger.info(f"Running re-encode concat → {output}")
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg command, streaming stderr to the logger."""
    logger.debug("CMD: " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    for line in proc.stderr:
        line = line.rstrip()
        if line:
            logger.debug(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {proc.returncode}")


# ══════════════════════════════════════════════════════════════════════════════
#  High-level entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_concat(paths: list[str], output: str) -> None:
    """Probe clips, choose strategy, run concat."""
    if len(paths) < 2:
        raise ValueError("Need at least 2 clips to concatenate.")

    infos, uniform = analyse_clips(paths)

    if uniform:
        concat_lossless(infos, output)
    else:
        concat_reencode(infos, output)

    logger.info(f"Done → {output}")


# ══════════════════════════════════════════════════════════════════════════════
#  Tooltip
# ══════════════════════════════════════════════════════════════════════════════

class ToolTip:
    PAD, DELAY_MS, WRAP = 6, 500, 260

    def __init__(self, widget, text):
        self._widget, self._text = widget, text
        self._id = self._tip_win = None
        widget.bind("<Enter>",  self._on_enter, add="+")
        widget.bind("<Leave>",  self._on_leave, add="+")
        widget.bind("<Button>", self._on_leave, add="+")

    def _on_enter(self, e=None):
        self._id = self._widget.after(self.DELAY_MS, self._show)

    def _on_leave(self, e=None):
        if self._id:
            self._widget.after_cancel(self._id)
            self._id = None
        self._hide()

    def _show(self):
        if self._tip_win:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip_win = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=C_MUSTARD)
        tk.Label(tw, text=self._text, font=("Courier", 9),
                 fg=C_BG, bg=C_MUSTARD, wraplength=self.WRAP,
                 justify="left", padx=self.PAD, pady=self.PAD).pack()

    def _hide(self):
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

class ConcatApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Clip Concatenator — Stellar Theory")
        self.configure(bg=C_BG)
        self.resizable(False, False)

        self._clips: list[str] = []          # ordered list of clip paths
        self._output_var = tk.StringVar()
        self._status_var = tk.StringVar(value="Add clips to begin.")

        self._build_ui()
        self._setup_logging()

        w, h = 720, 860
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", padx=24, pady=(4, 12))
        self._build_clips_section()
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", padx=24, pady=(12, 12))
        self._build_output_section()
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", padx=24, pady=(12, 12))
        self._build_action()
        self._build_log()

    def _build_header(self):
        hdr = tk.Frame(self, bg=C_BG)
        hdr.pack(fill="x", padx=24, pady=(20, 0))

        # Logo fallback
        logo_lbl = tk.Label(hdr, text="✦", font=("Georgia", 36),
                            fg=C_RUST, bg=C_BG)
        logo_lbl.pack(side="left", padx=(0, 18))
        try:
            import base64, io
            from PIL import Image, ImageTk
            logo_b64_path = os.path.join(os.path.dirname(__file__), "assets", "logo_b64.txt")
            if os.path.exists(logo_b64_path):
                LOGO_B64 = open(logo_b64_path).read().strip()
                img_data = base64.b64decode(LOGO_B64)
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((64, 92), Image.LANCZOS)
                self._logo_img = ImageTk.PhotoImage(img)
                logo_lbl.configure(image=self._logo_img, text="")
        except Exception:
            pass

        title_frame = tk.Frame(hdr, bg=C_BG)
        title_frame.pack(side="left", fill="y")
        tk.Label(title_frame, text="CLIP CONCATENATOR",
                 font=FONT_TITLE, fg=C_TEXT, bg=C_BG).pack(anchor="w")
        tk.Label(title_frame, text="by Stellar Theory",
                 font=FONT_BRAND, fg=C_TEXT_DIM, bg=C_BG).pack(anchor="w")

        tag_frame = tk.Frame(hdr, bg=C_BG)
        tag_frame.pack(side="right", anchor="ne")
        tk.Label(tag_frame, text="ST-TOOLS-002",
                 font=("Courier", 8), fg=C_TEXT_DIM, bg=C_BG).pack(anchor="e")
        tk.Label(tag_frame, text="◄ CONCAT ►",
                 font=("Courier", 8), fg=C_TEXT_DIM, bg=C_BG).pack(anchor="e")

    def _build_clips_section(self):
        self._section_label("CLIPS", "drag clips into the list or use Add")

        # Listbox + scrollbar
        list_frame = tk.Frame(self, bg=C_PANEL, bd=0)
        list_frame.pack(fill="x", padx=24, pady=(6, 0))

        scrollbar = tk.Scrollbar(list_frame, bg=C_BORDER, troughcolor=C_BG,
                                 relief="flat", width=10)
        scrollbar.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            list_frame,
            bg=C_PANEL, fg=C_TEXT,
            font=FONT_VALUE,
            selectbackground=C_RUST, selectforeground=C_BG,
            activestyle="none",
            relief="flat", bd=0,
            height=8,
            yscrollcommand=scrollbar.set,
        )
        self._listbox.pack(fill="x", expand=True, padx=6, pady=6)
        scrollbar.configure(command=self._listbox.yview)

        # Buttons row
        btn_row = tk.Frame(self, bg=C_BG)
        btn_row.pack(fill="x", padx=24, pady=(6, 0))

        def _btn(parent, label, cmd, tip):
            b = tk.Button(parent, text=label, font=("Courier", 9, "bold"),
                          fg=C_TEXT, bg=C_BORDER,
                          activeforeground=C_BG, activebackground=C_MUSTARD,
                          relief="flat", padx=12, pady=4, cursor="hand2",
                          command=cmd)
            b.pack(side="left", padx=(0, 6))
            ToolTip(b, tip)
            return b

        _btn(btn_row, "+ ADD",    self._add_clips,
             "Add one or more video clips.")
        _btn(btn_row, "↑ UP",     self._move_up,
             "Move the selected clip earlier in the order.")
        _btn(btn_row, "↓ DOWN",   self._move_down,
             "Move the selected clip later in the order.")
        _btn(btn_row, "✕ REMOVE", self._remove_clip,
             "Remove the selected clip from the list.")
        _btn(btn_row, "✕✕ CLEAR", self._clear_clips,
             "Remove all clips from the list.")

    def _build_output_section(self):
        self._section_label("OUTPUT", "where to save the concatenated file")

        row = tk.Frame(self, bg=C_BG)
        row.pack(fill="x", padx=24, pady=(6, 0))

        tk.Label(row, text="OUTPUT FILE    ",
                 font=FONT_LABEL, fg=C_MUSTARD, bg=C_BG,
                 width=16, anchor="w").pack(side="left")

        entry = tk.Entry(row, textvariable=self._output_var,
                         font=FONT_VALUE,
                         bg=C_PANEL, fg=C_TEXT,
                         insertbackground=C_RUST,
                         relief="flat", bd=4)
        entry.pack(side="left", fill="x", expand=True)
        ToolTip(entry, "Path for the output file. Must end in .mp4")

        browse = tk.Button(row, text="BROWSE",
                           font=("Courier", 8, "bold"),
                           fg=C_TEXT, bg=C_BORDER,
                           activeforeground=C_BG, activebackground=C_MUSTARD,
                           relief="flat", padx=8, pady=4, cursor="hand2",
                           command=self._browse_output)
        browse.pack(side="left", padx=(6, 0))
        ToolTip(browse, "Choose output file location.")

    def _build_action(self):
        self._run_btn = tk.Button(
            self, text="▶   CONCATENATE CLIPS",
            font=FONT_BUTTON,
            fg=C_BG, bg=C_RUST,
            activeforeground=C_BG, activebackground=C_MUSTARD,
            relief="flat", padx=32, pady=10, cursor="hand2",
            command=self._on_run,
        )
        self._run_btn.pack(pady=(0, 6))
        ToolTip(self._run_btn,
                "Probe all clips, choose the best strategy, and concatenate.")

        status_lbl = tk.Label(self, textvariable=self._status_var,
                              font=("Courier", 9), fg=C_TEXT_DIM, bg=C_BG)
        status_lbl.pack()
        self._status_lbl = status_lbl

    def _build_log(self):
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", padx=24, pady=(12, 8))

        log_hdr = tk.Frame(self, bg=C_BG)
        log_hdr.pack(fill="x", padx=24)
        tk.Label(log_hdr, text="LOG",
                 font=("Courier", 8, "bold"), fg=C_MUSTARD, bg=C_BG).pack(side="left")

        log_frame = tk.Frame(self, bg="#161c26")
        log_frame.pack(fill="both", expand=True, padx=24, pady=(4, 20))

        scrollbar = tk.Scrollbar(log_frame, bg=C_BORDER,
                                 troughcolor="#161c26", relief="flat", width=10)
        scrollbar.pack(side="right", fill="y")

        self._log_text = tk.Text(
            log_frame, bg="#161c26", fg=C_TEXT_DIM,
            font=FONT_LOG, relief="flat", padx=10, pady=8,
            state="disabled", wrap="word",
            yscrollcommand=scrollbar.set,
        )
        self._log_text.pack(fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

        self._log_text.tag_configure("info",    foreground=C_TEXT)
        self._log_text.tag_configure("warn",    foreground=C_WARNING)
        self._log_text.tag_configure("error",   foreground=C_ERROR)
        self._log_text.tag_configure("debug",   foreground=C_TEXT_DIM)
        self._log_text.tag_configure("success", foreground=C_SUCCESS)

    # ── Section label helper ──────────────────────────────────────────────────

    def _section_label(self, name: str, desc: str):
        row = tk.Frame(self, bg=C_BG)
        row.pack(fill="x", padx=24)
        tk.Label(row, text=f"{name}  ",
                 font=("Courier", 8, "bold"), fg=C_MUSTARD, bg=C_BG).pack(side="left")
        tk.Label(row, text=f"─── {desc} " + "─" * 40,
                 font=("Courier", 8), fg=C_BORDER, bg=C_BG).pack(side="left")

    # ── Logging setup ─────────────────────────────────────────────────────────

    def _setup_logging(self):
        logger.setLevel(logging.DEBUG)
        if not any(not isinstance(h, logging.NullHandler) for h in logger.handlers):
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(ch)

        gui_handler = _GUILogHandler(self._append_log)
        gui_handler.setLevel(logging.DEBUG)
        logger.addHandler(gui_handler)

    def _append_log(self, msg: str, tag: str):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n", tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ── Clip management ───────────────────────────────────────────────────────

    def _add_clips(self):
        paths = filedialog.askopenfilenames(
            title="Select video clips",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")],
        )
        for p in paths:
            if p not in self._clips:
                self._clips.append(p)
                self._listbox.insert("end", os.path.basename(p))
        self._update_status()

    def _move_up(self):
        sel = self._listbox.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._clips[i - 1], self._clips[i] = self._clips[i], self._clips[i - 1]
        label = self._listbox.get(i)
        self._listbox.delete(i)
        self._listbox.insert(i - 1, label)
        self._listbox.selection_set(i - 1)

    def _move_down(self):
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._clips) - 1:
            return
        i = sel[0]
        self._clips[i], self._clips[i + 1] = self._clips[i + 1], self._clips[i]
        label = self._listbox.get(i)
        self._listbox.delete(i)
        self._listbox.insert(i + 1, label)
        self._listbox.selection_set(i + 1)

    def _remove_clip(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        i = sel[0]
        self._clips.pop(i)
        self._listbox.delete(i)
        self._update_status()

    def _clear_clips(self):
        self._clips.clear()
        self._listbox.delete(0, "end")
        self._update_status()

    def _update_status(self):
        n = len(self._clips)
        if n == 0:
            self._set_status("Add clips to begin.", C_TEXT_DIM)
        elif n == 1:
            self._set_status("Add at least one more clip.", C_WARNING)
        else:
            self._set_status(f"{n} clips loaded — ready.", C_TEXT_DIM)

    # ── Output browse ─────────────────────────────────────────────────────────

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save concatenated clip as…",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if path:
            self._output_var.set(path)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _on_run(self):
        clips = list(self._clips)
        output = self._output_var.get().strip()

        if len(clips) < 2:
            messagebox.showwarning("Not enough clips", "Add at least 2 clips.")
            return
        if not output:
            messagebox.showwarning("No output", "Choose an output file path.")
            return

        self._run_btn.configure(state="disabled", text="▶   RUNNING…", bg=C_BROWN)
        self._set_status("Running…", C_TEXT_DIM)
        threading.Thread(target=self._worker, args=(clips, output), daemon=True).start()

    def _worker(self, clips, output):
        try:
            run_concat(clips, output)
            self.after(0, self._on_done, output)
        except Exception as e:
            self.after(0, self._on_error, str(e))

    def _on_done(self, output):
        self._run_btn.configure(state="normal", text="▶   CONCATENATE CLIPS", bg=C_RUST)
        self._set_status(f"Done → {os.path.basename(output)}", C_SAGE)
        logger.info(f"✓ Saved to: {output}")

    def _on_error(self, msg):
        self._run_btn.configure(state="normal", text="▶   CONCATENATE CLIPS", bg=C_RUST)
        self._set_status("Error — see log.", C_ERROR)
        logger.error(f"Error: {msg}")

    def _set_status(self, msg: str, colour: str):
        self._status_var.set(msg)
        self._status_lbl.configure(fg=colour)


# ── GUI log handler ───────────────────────────────────────────────────────────

class _GUILogHandler(logging.Handler):
    _LEVEL_TAG = {
        logging.DEBUG:   "debug",
        logging.INFO:    "info",
        logging.WARNING: "warn",
        logging.ERROR:   "error",
        logging.CRITICAL:"error",
    }

    def __init__(self, append_fn):
        super().__init__()
        self._append = append_fn

    def emit(self, record):
        msg = self.format(record)
        tag = self._LEVEL_TAG.get(record.levelno, "info")
        try:
            self._append(msg, tag)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def _setup_cli_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


def main():
    parser = argparse.ArgumentParser(
        description="Stellar Theory Clip Concatenator — "
                    "auto-detects codec/fps differences and picks the right strategy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python concat_clips.py clip1.mp4 clip2.mp4 clip3.mp4 -o joined.mp4
  python concat_clips.py --gui
        """,
    )
    parser.add_argument("clips", nargs="*", help="Input clip paths, in order.")
    parser.add_argument("-o", "--output", default="",
                        help="Output file path (default: concat_output.mp4 next to first clip).")
    parser.add_argument("--gui", action="store_true", help="Launch the GUI.")
    args = parser.parse_args()

    if args.gui or not args.clips:
        app = ConcatApp()
        app.mainloop()
        return

    _setup_cli_logging()

    output = args.output
    if not output:
        first_dir = os.path.dirname(os.path.abspath(args.clips[0]))
        output = os.path.join(first_dir, "concat_output.mp4")

    try:
        run_concat(args.clips, output)
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
