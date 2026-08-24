# Raster Image Tiler

A desktop GUI for turning drone orthomosaics into web map tiles with GDAL.

Wraps `gdal2tiles` in a PyQt6 window: pick a raster, set the zoom range and
output format, and watch a real progress bar. Before you start, it tells you
how many tiles you are about to make, roughly how large they will be, and
roughly how long it will take.

---

## Requirements

- **Python 3.12** (64-bit)
- **PyQt6**
- **GDAL Python bindings**, including `osgeo_utils`

### Installing GDAL on Windows

PyPI ships GDAL as source only, so `pip install gdal` tries to compile it and
fails. Use a prebuilt wheel from
[cgohlke/geospatial-wheels](https://github.com/cgohlke/geospatial-wheels/releases):

```bat
.venv\Scripts\activate
pip install numpy
pip install C:\path\to\gdal-3.12.2-cp312-cp312-win_amd64.whl
python -c "from osgeo import gdal; print(gdal.__version__)"
```

The wheel filename must match your Python version exactly — `cp312` for
Python 3.12, `win_amd64` for 64-bit Windows. Pip rejects a mismatch with
"not a supported wheel on this platform".

If you get `ImportError: DLL load failed while importing _gdal`, install the
Microsoft VC++ redistributable.

---

## Running

```bat
.venv\Scripts\activate
python main.py
```

---

## Using it

Pick an **input raster**. The output folder fills itself in as a `tiles`
folder beside the image, and keeps tracking your choice until you type or
browse to something else. The image's own CRS is read from its header and
dropped into **Source SRS**.

The **properties panel** shows pixel dimensions, ground size, GSD, band
count, CRS and file size — all read from the header, so it stays fast on a
multi-gigabyte orthomosaic.

The status line under the settings carries a live estimate:

```
Estimate: ~24,796 tiles, 1.1 GB, ~18m 53s
```

Tile counts are computed exactly from the raster footprint and the pyramid
geometry. **Size and time are rough** — bytes-per-tile is an average and
scene content moves it a long way, while the time figure assumes a local SSD.
Hover the status line for a per-zoom-level breakdown.

### Settings

| Setting | gdal2tiles flag | Notes |
|---|---|---|
| Zoom min / max | `--zoom` | |
| Tile driver | `--tiledriver` | PNG, WEBP or JPEG |
| Image quality | `--webp-quality` / `--jpeg-quality` | Hidden for PNG |
| Lossless WEBP | `--webp-lossless` | Overrides quality |
| Tile size | `--tilesize` | |
| Resampling | `--resampling` | `average` for imagery, `near` for categorical data |
| Source SRS | `--s_srs` | Blank uses whatever the file carries |
| Source nodata | `--srcnodata` | Paired with `--nodata-values-pct-threshold` |
| Processes | `--processes` | Parallel workers |

**Advanced** holds the rest: profile, XYZ vs TMS, resume, excluded values,
web viewer options, API keys, KML and verbosity.

Settings that are left at their default are simply not passed on the command
line, so an untouched form produces just:

```
gdal2tiles --zoom 0-18 input.tif output/
```

Everything except the two paths is saved on exit and restored next launch.

### JPEG has no transparency

Edge tiles come out black rather than transparent. Use PNG or WEBP for a
rotated orthomosaic with nodata around the edges.

---

## Project layout

```
main.py                  entry point; also the gdal2tiles runner when frozen
core/
  runtime.py             frozen-vs-source paths, GDAL data dirs, child launch
  raster.py              header reading, ground measurements, 8-bit VRT
  estimate.py            tile counts, output size, duration
  tiler.py               builds the gdal2tiles command and runs it
  worker.py              Qt threads (the only file in core/ that imports Qt)
gui/
  main_window.py         the window
  advanced_panel.py      Advanced settings
  properties_panel.py    raster properties readout
  collapsible.py         expanding section widget
  layout_utils.py        shared field widths, so both panels align
  settings_store.py      persistence via QSettings
  style.qss              the stylesheet
build/                   PyInstaller spec, build script, Inno Setup script
exe/                     build output
icons/                   app icon and UI glyphs
```

`core/` never imports Qt except in `worker.py`, so the tiling logic can be
driven from a script or a test without a GUI:

```python
from core import tiler

tiler.run({
    "input_path": "ortho.tif",
    "output_dir": "tiles",
    "zoom_min": 12,
    "zoom_max": 20,
    "tiledriver": "WEBP",
})
```

---

## How it works

**16-bit input is rescaled first.** gdal2tiles clamps non-Byte data to Byte
without rescaling, which wrecks the radiometry of 16-bit drone imagery. When
the source is not already 8-bit, the app builds a VRT that stretches each
band's real range into 0–255 and feeds gdal2tiles that instead. A VRT is a
small XML file — no pixels are copied.

**gdal2tiles runs as a child process.** It has no cancellation hook, so
running it in-process would mean no way to stop a long job, and a crash
inside GDAL would take the window down with it.

**Progress comes from counting files, not from parsing stdout.** gdal2tiles
draws its `0...10...20...` bar through GDAL's C-level `TermProgress`, which
block-buffers when stdout is a pipe — `PYTHONUNBUFFERED` does not reach C
buffering, so nothing arrives until the process exits. Counting tiles on disk
works regardless, and gives an honest throughput figure for the ETA.

**Header reads happen on a background thread.** The expensive part is
`import osgeo.gdal`, which pulls in a large stack of native libraries. It is
warmed up at startup so picking a file never blocks the window.

---

## Building an executable

See [`exe/README.md`](exe/README.md). Short version:

```bat
.venv\Scripts\activate
pip install pyinstaller
python build\build.py
```

Produces `exe/ImageTiler.exe` and, if [Inno Setup](https://jrsoftware.org/isdl.php)
is installed, `exe/ImageTiler-Setup-1.0.0.exe`.

You need `icons/icon.ico` for the executable icon — Windows will not accept a
`.png` there.

---

## Notes

**gdal2tiles is deprecated from GDAL 3.13**, remapped to `gdal raster tile`,
with legacy mode removed in 3.15. Nothing breaks on 3.12, but the command
this app builds will need re-mapping eventually. `core/tiler.py` is the only
file that would change.

**Settings live in the registry** on Windows, under
`HKEY_CURRENT_USER\Software\ImageTiler\RasterImageTiler`. On Linux and macOS
the same code writes an ini file instead.
