# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Raster Image Tiler.

Build with build/build.py rather than calling pyinstaller directly, so the
output lands in exe/ and the installer step runs afterwards.

ONEFILE = True produces a single ImageTiler.exe. It is the nicer thing to
hand someone, at the cost of unpacking the whole bundle to %TEMP% on every
launch. Flip it to False for a folder build if that startup delay annoys you.
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ONEFILE = True

PROJECT_ROOT = Path(SPECPATH).parent  # noqa: F821 - injected by PyInstaller

APP_NAME = "ImageTiler"
ICON_FILE = PROJECT_ROOT / "icons" / "icon.ico"


# ----------------------------------------------------------------------
# Data files
# ----------------------------------------------------------------------

datas = [
    (str(PROJECT_ROOT / "gui" / "style.qss"), "gui"),
    (str(PROJECT_ROOT / "icons"), "icons"),
]

binaries = []
hiddenimports = []

# GDAL ships a data directory -- proj.db (every coordinate system in the
# world) plus GDAL's own lookup tables. Nothing in the source code names
# these files, so PyInstaller cannot infer them; without proj.db every
# coordinate transform fails with "Cannot find proj.db".
try:
    datas += collect_data_files("osgeo")
    binaries += collect_dynamic_libs("osgeo")
    # gdal2tiles lives in osgeo_utils and is only ever named as a string.
    hiddenimports += collect_submodules("osgeo_utils")
except Exception as exc:  # pragma: no cover - build-time diagnostics
    raise SystemExit(
        f"Could not locate the GDAL Python bindings: {exc}\n"
        "Activate the venv that has GDAL installed before building."
    )

hiddenimports += ["osgeo_utils.gdal2tiles", "numpy"]


# ----------------------------------------------------------------------
# Trimming
# ----------------------------------------------------------------------

# Qt ships far more than a form and a progress bar need. This app uses only
# QtCore, QtGui, QtWidgets, and the SVG image plugin for the dropdown arrow.
QT_EXCLUDES = [
    "PyQt6.Qt3DAnimation", "PyQt6.Qt3DCore", "PyQt6.Qt3DExtras",
    "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic", "PyQt6.Qt3DRender",
    "PyQt6.QtBluetooth", "PyQt6.QtCharts", "PyQt6.QtDataVisualization",
    "PyQt6.QtDBus", "PyQt6.QtDesigner", "PyQt6.QtHelp",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtNfc",
    "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets", "PyQt6.QtPositioning", "PyQt6.QtQml",
    "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtQuickWidgets",
    "PyQt6.QtRemoteObjects", "PyQt6.QtSensors", "PyQt6.QtSerialPort",
    "PyQt6.QtSpatialAudio", "PyQt6.QtSql", "PyQt6.QtTest",
    "PyQt6.QtTextToSpeech", "PyQt6.QtWebChannel", "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineQuick", "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebSockets", "PyQt6.QtXml",
    "PyQt6.QtDesigner", "PyQt6.uic",
]

# Toolkits and science stacks that get dragged in by accident.
THIRD_PARTY_EXCLUDES = [
    "tkinter", "_tkinter", "Tkinter",
    "matplotlib", "scipy", "pandas", "PIL", "Pillow",
    "IPython", "jupyter", "notebook", "pytest", "setuptools", "pip",
    "PySide6", "PyQt5", "wx",
    # GDAL's own optional extras
    "osgeo_utils.samples",
]

# Standard library corners a GUI never reaches.
STDLIB_EXCLUDES = [
    "unittest", "doctest", "pydoc", "pydoc_data", "test", "lib2to3",
    "distutils", "ensurepip", "idlelib", "turtledemo", "sqlite3",
]

excludes = QT_EXCLUDES + THIRD_PARTY_EXCLUDES + STDLIB_EXCLUDES


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe_kwargs = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX shrinks the bundle but is a well known cause of corrupted Qt and
    # GDAL DLLs. Not worth the debugging time.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_FILE) if ICON_FILE.is_file() else None,
)

if ONEFILE:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **exe_kwargs,
    )
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_kwargs,
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
