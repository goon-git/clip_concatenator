# Clip Concatenator

Joins video clips together in order. Automatically detects whether your clips are compatible and picks the right strategy — lossless stream copy when possible, re-encode when necessary.

---

## How it works

Every clip is probed with `ffprobe` before anything is written. The result determines the strategy:

| Condition | Strategy | Quality |
|-----------|----------|---------|
| All clips share the same codec, resolution, and frame rate | **Concat demuxer** — stream copy, no re-encode | Lossless, instant |
| Any clip differs in codec, resolution, or frame rate | **filter_complex concat** — re-encodes to a consistent output | Re-encoded to H.264 CRF 18 |

In the re-encode path, all clips are scaled and padded (black bars) to the largest resolution found, so nothing gets cropped.

---

## Usage

### GUI

```bash
python3 concat_clips.py --gui
```

Or just run without arguments — the GUI launches automatically.

### CLI

```bash
python3 concat_clips.py clip1.mp4 clip2.mp4 clip3.mp4 -o output.mp4
```

The output path is optional — if omitted, the file is saved as `concat_output.mp4` next to the first clip.

```
usage: concat_clips.py [-h] [-o OUTPUT] [--gui] [clips ...]

positional arguments:
  clips           Input clip paths, in order

options:
  -o, --output    Output file path (default: concat_output.mp4 next to first clip)
  --gui           Launch the GUI
```

---

## GUI controls

| Control | Description |
|---------|-------------|
| **+ ADD** | Add one or more video clips via file picker |
| **↑ UP / ↓ DOWN** | Reorder the selected clip |
| **✕ REMOVE** | Remove the selected clip |
| **✕✕ CLEAR** | Remove all clips |
| **OUTPUT FILE** | Path for the output file — type or use Browse |
| **▶ CONCATENATE CLIPS** | Probe clips and run the concat |

The log panel at the bottom shows per-clip probe results, the chosen strategy, and ffmpeg progress.

---

## Log output

| Level | Color | Meaning |
|-------|-------|---------|
| INFO | Cream | Clip probed, strategy chosen, done |
| WARN | Mustard | Clips differ — re-encode required |
| ERROR | Rust | ffmpeg failure or bad input |
| DEBUG | Dim | Raw ffmpeg output |

---

## Re-encode settings

When clips are mixed, the output is re-encoded with:

- **Codec:** H.264 (`libx264`)
- **Quality:** CRF 18 (near-lossless)
- **Preset:** `slow` (best compression at this quality level)
- **Pixel format:** `yuv420p` (maximum compatibility)
- **Resolution:** Largest width/height found among all inputs, padded to maintain aspect ratio
- **Frame rate:** Highest frame rate found among all inputs

---

## Requirements

```bash
brew install ffmpeg      # macOS
# or
winget install ffmpeg    # Windows
```

`ffprobe` is included with ffmpeg and must also be on your PATH.

Pillow is optional — used only to display the Stellar Theory logo in the GUI header:

```bash
pip3 install pillow
```

---

## API

The concat logic can be imported and used directly:

```python
from concat_clips import run_concat

# Auto-detects strategy and concatenates
run_concat(["clip1.mp4", "clip2.mp4", "clip3.mp4"], "output.mp4")
```
