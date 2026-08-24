"""One command to build the executable and the installer.

    .venv\\Scripts\\activate
    python build\\build.py

Everything lands in exe/. Pass --no-installer to skip the Inno Setup step,
or --onedir to build a folder instead of a single file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = BUILD_DIR / "ImageTiler.spec"
WORK_DIR = BUILD_DIR / "_work"
DIST_DIR = PROJECT_ROOT / "exe"
INSTALLER_SCRIPT = BUILD_DIR / "installer.iss"

# Where Inno Setup usually puts its compiler.
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
]


def fail(message: str):
    print(f"\n[!] {message}", file=sys.stderr)
    sys.exit(1)


def check_prerequisites():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        fail("PyInstaller is not installed.  pip install pyinstaller")

    try:
        from osgeo import gdal  # noqa: F401
    except ImportError:
        fail(
            "GDAL is not importable from this interpreter.\n"
            "    Activate the project venv before building, otherwise the "
            "bundle will be missing proj.db."
        )

    try:
        import osgeo_utils.gdal2tiles  # noqa: F401
    except ImportError:
        fail("osgeo_utils.gdal2tiles is missing from this GDAL install.")


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def set_onefile(enabled: bool):
    """Flip the ONEFILE switch inside the spec without hand-editing it."""
    text = SPEC_FILE.read_text(encoding="utf-8")
    wanted = f"ONEFILE = {enabled}"

    for value in ("ONEFILE = True", "ONEFILE = False"):
        if value in text:
            if value != wanted:
                SPEC_FILE.write_text(
                    text.replace(value, wanted), encoding="utf-8"
                )
            return

    fail("Could not find the ONEFILE switch in ImageTiler.spec")


def run_pyinstaller():
    print("=" * 62)
    print("Building executable")
    print("=" * 62)

    DIST_DIR.mkdir(exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        str(SPEC_FILE),
    ]

    started = time.monotonic()
    result = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        fail(f"PyInstaller failed with code {result.returncode}")

    print(f"\nBuilt in {time.monotonic() - started:.0f}s")


def report_size(onefile: bool) -> Path | None:
    if onefile:
        target = DIST_DIR / "ImageTiler.exe"
        if not target.is_file():
            fail(f"Expected {target} but it was not produced.")
        print(f"\n  ImageTiler.exe  {human_size(target.stat().st_size)}")
        return target

    folder = DIST_DIR / "ImageTiler"
    if not folder.is_dir():
        fail(f"Expected {folder} but it was not produced.")

    total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    print(f"\n  ImageTiler/  {human_size(total)}")

    # The ten fattest files, so trimming has somewhere obvious to start.
    biggest = sorted(
        (f for f in folder.rglob("*") if f.is_file()),
        key=lambda f: f.stat().st_size,
        reverse=True,
    )[:10]
    print("\n  Largest files:")
    for item in biggest:
        print(f"    {human_size(item.stat().st_size):>10}  "
              f"{item.relative_to(folder)}")

    return folder


def find_iscc() -> str | None:
    found = shutil.which("ISCC")
    if found:
        return found
    for candidate in ISCC_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def run_installer():
    print("\n" + "=" * 62)
    print("Building installer")
    print("=" * 62)

    iscc = find_iscc()
    if not iscc:
        print(
            "Inno Setup not found, skipping the installer.\n"
            "    Install it from https://jrsoftware.org/isdl.php and "
            "re-run, or pass --no-installer.\n"
            f"    The executable is ready in {DIST_DIR}"
        )
        return

    result = subprocess.run(
        [iscc, str(INSTALLER_SCRIPT)], cwd=str(BUILD_DIR)
    )
    if result.returncode != 0:
        fail(f"Inno Setup failed with code {result.returncode}")

    for installer in DIST_DIR.glob("ImageTiler-Setup-*.exe"):
        print(f"\n  {installer.name}  "
              f"{human_size(installer.stat().st_size)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="build a folder instead of a single file",
    )
    parser.add_argument(
        "--no-installer",
        action="store_true",
        help="skip the Inno Setup step",
    )
    args = parser.parse_args()

    onefile = not args.onedir

    check_prerequisites()
    set_onefile(onefile)
    run_pyinstaller()
    report_size(onefile)

    if not args.no_installer:
        run_installer()

    print(f"\nDone. Output is in {DIST_DIR}\n")


if __name__ == "__main__":
    main()
