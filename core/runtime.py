"""Everything that behaves differently once the app is packaged.

Running from source, three things are true that stop being true inside a
PyInstaller bundle:

1. `sys.executable` is python.exe, so we can launch `-m osgeo_utils.gdal2tiles`.
2. `__file__` sits in a real folder, so style.qss and icons/ are findable.
3. GDAL knows where its own data directory is, because it was installed there.

Packaged, all three break. This module is the single place that knows the
difference, so nothing else has to care.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller sets sys.frozen on the bundled interpreter.
IS_FROZEN = bool(getattr(sys, "frozen", False))

# First argument that turns this executable into a gdal2tiles runner rather
# than the GUI. Frozen, `sys.executable` IS this app, so "run gdal2tiles"
# has to be expressed as a flag we recognise ourselves.
CHILD_FLAG = "--run-gdal2tiles"


# ----------------------------------------------------------------------
# Finding bundled files
# ----------------------------------------------------------------------


def resource_root() -> Path:
    """Folder holding style.qss, icons/ and the GDAL data directories."""
    if IS_FROZEN:
        # One-file builds unpack to a temp dir whose path is in _MEIPASS.
        # One-folder builds have no _MEIPASS, so fall back to the exe's dir.
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).parent

    # core/runtime.py -> core/ -> project root
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


# ----------------------------------------------------------------------
# GDAL environment
# ----------------------------------------------------------------------


def configure_gdal_environment() -> None:
    """Point GDAL and PROJ at their bundled data directories.

    Must run BEFORE anything imports osgeo, because PROJ reads these
    variables when its library initialises.

    Running from source this does nothing to the data paths -- the installed
    GDAL already knows where its files are.
    """
    # Stop PROJ trying to fetch transformation grids over the network, which
    # can hang for a long time on a machine behind a firewall.
    os.environ.setdefault("PROJ_NETWORK", "OFF")

    if not IS_FROZEN:
        return

    gdal_data = resource_path("osgeo", "data", "gdal")
    if gdal_data.is_dir():
        os.environ.setdefault("GDAL_DATA", str(gdal_data))

    proj_data = resource_path("osgeo", "data", "proj")
    if proj_data.is_dir():
        # PROJ 9 reads PROJ_DATA; older builds read PROJ_LIB. Set both.
        os.environ.setdefault("PROJ_DATA", str(proj_data))
        os.environ.setdefault("PROJ_LIB", str(proj_data))


def gdal_data_report() -> str:
    """Human-readable check of where GDAL will look. Useful in bug reports."""
    lines = [f"frozen: {IS_FROZEN}", f"resource root: {resource_root()}"]
    for name in ("GDAL_DATA", "PROJ_DATA", "PROJ_LIB", "PROJ_NETWORK"):
        value = os.environ.get(name)
        marker = ""
        if value and name != "PROJ_NETWORK":
            marker = " (missing!)" if not Path(value).is_dir() else " (ok)"
        lines.append(f"{name}: {value or '<unset>'}{marker}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Launching gdal2tiles
# ----------------------------------------------------------------------


def gdal2tiles_launcher() -> list[str] | None:
    """Command prefix that runs gdal2tiles, or None if it can't be found.

    Frozen, this re-invokes our own executable with CHILD_FLAG; main() spots
    the flag and hands control to gdal2tiles instead of building a window.
    """
    if IS_FROZEN:
        return [sys.executable, CHILD_FLAG]

    import importlib.util

    if importlib.util.find_spec("osgeo_utils") is None:
        return None

    entry = resource_path("main.py")
    if entry.is_file():
        return [sys.executable, str(entry), CHILD_FLAG]

    # Running from an odd layout -- go straight to the module.
    return [sys.executable, "-m", "osgeo_utils.gdal2tiles"]


def run_gdal2tiles_child(args: list[str]) -> int:
    """Body of the child process: hand `args` to gdal2tiles and return its code."""
    configure_gdal_environment()

    try:
        from osgeo_utils import gdal2tiles
    except ImportError as exc:
        print(f"gdal2tiles is not available: {exc}", file=sys.stderr)
        return 2

    # gdal2tiles.main() expects a full argv, program name included.
    try:
        return int(gdal2tiles.main(["gdal2tiles"] + list(args)) or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
