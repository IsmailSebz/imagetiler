# exe/

Build output. Nothing in here is written by hand.

```
.venv\Scripts\activate
python build\build.py
```

Produces:

| File | What it is |
|---|---|
| `ImageTiler.exe` | The application, single file |
| `ImageTiler-Setup-1.0.0.exe` | Installer, if Inno Setup is present |

## Options

```
python build\build.py --onedir         # folder build instead of one file
python build\build.py --no-installer   # skip Inno Setup
```

`--onedir` prints the ten largest files in the bundle, which is the place to
start if you want to trim further.

## One file vs one folder

The single file is nicer to hand someone, but the bootloader unpacks the
whole bundle to `%TEMP%` on every launch. With GDAL in the mix that is a few
hundred megabytes, so expect a pause before the window appears — and a
second pause the first time you press Start Tiling, since the tiling child
process is this same executable being launched again.

`--onedir` has neither delay. Wrap the folder in the installer and the user
never sees the difference.

## Requirements

- The project venv, active, with GDAL importable — the build reads `proj.db`
  out of the installed `osgeo` package. Building without it produces an exe
  that fails on every coordinate transform.
- `pip install pyinstaller`
- [Inno Setup 6](https://jrsoftware.org/isdl.php) for the installer step
  (optional; the build skips it with a note if missing)
- `icons/icon.ico` for the executable icon. Windows will not use a `.png`
  here — convert `icons/icon.png` if the exe shows a blank icon.
