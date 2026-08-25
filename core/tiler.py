"""Turns a settings dict into a gdal2tiles run.

`build_arguments` is pure and testable. `run` executes gdal2tiles as a child
process, streams its progress back through a callback, and can be cancelled.

gdal2tiles is run out-of-process on purpose: it has no cancellation hook and
calling it in-process would mean no way to stop a long job, and any crash in
GDAL would take the GUI down with it.

`output_dir` is a container, not the tile tree itself. One run can produce up
to three things, and they each get their own folder inside it:

    <output_dir>/tiles/      the {z}/{x}/{y} tree gdal2tiles writes
    <output_dir>/mbtiles/    <name>.mbtiles
    <output_dir>/pmtiles/    <name>.pmtiles

gdal2tiles always runs, because the single-file archives are packed from its
output. If the XYZ tree itself was not asked for, it is removed afterwards.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from core import estimate, package, raster, runtime
from core.raster import RasterError

# Values the UI ships with. A setting equal to its default is left off the
# command line, so the generated command only ever shows what was changed.
DEFAULTS: dict[str, object] = {
    "profile": "mercator",
    "resampling": "average",
    "tile_size": 256,
    "tiledriver": "PNG",
    "quality": 75,
    "webp_lossless": False,
    "s_srs": "",
    "src_nodata": "",
    "nodata_pct": 100,
    "excluded_values": "",
    "excluded_pct": 50,
    "processes": 1,
    "webviewer": "all",
    "title": "",
    "copyright": "",
    "url": "",
    "google_key": "",
    "bing_key": "",
    "mapml_template": "",
    "kml": "auto",
    "xyz": False,
    "tms_compatible": False,
    "resume": False,
    "exclude_transparent": False,
    "verbose": False,
    "quiet": False,
}


# Output formats, and the subfolder each one lands in.
TILES_SUBDIR = "tiles"
MBTILES_SUBDIR = "mbtiles"
PMTILES_SUBDIR = "pmtiles"

# Share of the progress bar given to gdal2tiles when packaging follows it.
# Packing is file copying, so it is fast next to the tiling itself, but a
# large pyramid still takes long enough that the bar should not sit at 100.
_TILING_SHARE = 92


class TilerError(RuntimeError):
    """Raised when a tiling job cannot start or fails."""


class Cancelled(Exception):
    """Raised internally when the user cancels a run."""


# ----------------------------------------------------------------------
# Output layout
# ----------------------------------------------------------------------


def tiles_dir(output_dir: str) -> str:
    """Where gdal2tiles writes, inside the chosen output folder."""
    return os.path.join(output_dir, TILES_SUBDIR)


def default_output_dir(input_path: str) -> str:
    """Sibling 'output' folder next to the chosen raster."""
    return str(Path(input_path).expanduser().resolve().parent / "output")


def output_name(input_path: str) -> str:
    """Archive filename stem, taken from the raster's own name."""
    stem = Path(input_path).stem.strip()
    # Keep it usable as a filename on Windows regardless of the source name.
    cleaned = "".join(c for c in stem if c not in '<>:"/\\|?*').strip()
    return cleaned or "tiles"


def wanted_formats(settings: dict) -> tuple[bool, bool, bool]:
    """(xyz, mbtiles, pmtiles) as requested, defaulting to XYZ only."""
    if not any(
        key in settings for key in ("want_xyz", "want_mbtiles", "want_pmtiles")
    ):
        return True, False, False
    return (
        bool(settings.get("want_xyz", False)),
        bool(settings.get("want_mbtiles", False)),
        bool(settings.get("want_pmtiles", False)),
    )


ProgressFn = Callable[[int], None]
MessageFn = Callable[[str], None]
CancelFn = Callable[[], bool]
# Receives {"done", "total", "elapsed", "eta"} while a job runs.
StatsFn = Callable[[dict], None]


# ----------------------------------------------------------------------
# Argument building
# ----------------------------------------------------------------------


def _get(settings: dict, key: str):
    """Setting value, falling back to the documented gdal2tiles default."""
    value = settings.get(key, DEFAULTS.get(key))
    return DEFAULTS.get(key) if value is None else value


def _changed(settings: dict, key: str) -> bool:
    return _get(settings, key) != DEFAULTS.get(key)


def build_arguments(settings: dict) -> list[str]:
    """gdal2tiles flags for `settings`, without input/output paths."""
    args: list[str] = []

    zoom_min = int(_get(settings, "zoom_min") or 0)
    zoom_max = int(_get(settings, "zoom_max") or 0)
    if zoom_max < zoom_min:
        zoom_min, zoom_max = zoom_max, zoom_min
    args += ["--zoom", f"{zoom_min}-{zoom_max}"]

    if _changed(settings, "profile"):
        args += ["--profile", str(_get(settings, "profile"))]
    if _changed(settings, "resampling"):
        args += ["--resampling", str(_get(settings, "resampling"))]
    if _changed(settings, "tile_size"):
        args.append(f"--tilesize={int(_get(settings, 'tile_size'))}")

    driver = str(_get(settings, "tiledriver"))
    if _changed(settings, "tiledriver"):
        args.append(f"--tiledriver={driver}")

    if driver == "WEBP":
        if _get(settings, "webp_lossless"):
            args.append("--webp-lossless")
        elif _changed(settings, "quality"):
            args.append(f"--webp-quality={int(_get(settings, 'quality'))}")
    elif driver == "JPEG" and _changed(settings, "quality"):
        args.append(f"--jpeg-quality={int(_get(settings, 'quality'))}")

    if _changed(settings, "s_srs"):
        args += ["--s_srs", str(_get(settings, "s_srs"))]
    if _changed(settings, "src_nodata"):
        args += ["--srcnodata", str(_get(settings, "src_nodata"))]
    if _changed(settings, "nodata_pct"):
        args.append(
            f"--nodata-values-pct-threshold={int(_get(settings, 'nodata_pct'))}"
        )

    if _changed(settings, "excluded_values"):
        args.append(f"--excluded-values={_get(settings, 'excluded_values')}")
        if _changed(settings, "excluded_pct"):
            args.append(
                "--excluded-values-pct-threshold="
                f"{int(_get(settings, 'excluded_pct'))}"
            )

    if _get(settings, "xyz"):
        args.append("--xyz")
    if _get(settings, "tms_compatible"):
        args.append("--tmscompatible")
    if _get(settings, "resume"):
        args.append("--resume")
    if _get(settings, "exclude_transparent"):
        args.append("--exclude")

    if _changed(settings, "processes"):
        args.append(f"--processes={int(_get(settings, 'processes'))}")

    if _changed(settings, "webviewer"):
        args += ["--webviewer", str(_get(settings, "webviewer"))]
    for key, flag in (
        ("title", "--title"),
        ("copyright", "--copyright"),
        ("url", "--url"),
        ("google_key", "--googlekey"),
        ("bing_key", "--bingkey"),
    ):
        if _changed(settings, key):
            args += [flag, str(_get(settings, key))]
    if _changed(settings, "mapml_template"):
        args.append(f"--mapml-template={_get(settings, 'mapml_template')}")

    kml = _get(settings, "kml")
    if kml == "force":
        args.append("--force-kml")
    elif kml == "none":
        args.append("--no-kml")

    if _get(settings, "verbose"):
        args.append("--verbose")
    if _get(settings, "quiet"):
        args.append("--quiet")

    return args


def command_preview(settings: dict) -> str:
    """Human-readable command, for the status line."""
    args = build_arguments(settings)
    output_dir = str(settings.get("output_dir", "")).strip()
    args += [
        str(settings.get("input_path", "")),
        tiles_dir(output_dir) if output_dir else "",
    ]
    return "gdal2tiles " + " ".join(_quote(a) for a in args)


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


# ----------------------------------------------------------------------
# Locating gdal2tiles
# ----------------------------------------------------------------------


def gdal2tiles_command() -> list[str]:
    """How to invoke gdal2tiles in this environment.

    From source that is `python.exe -m osgeo_utils.gdal2tiles`. Packaged
    there is no python.exe, so core.runtime re-invokes this same executable
    with a flag that main() routes to gdal2tiles instead of the GUI.
    """
    launcher = runtime.gdal2tiles_launcher()
    if launcher:
        return launcher

    for name in ("gdal2tiles", "gdal2tiles.py"):
        found = shutil.which(name)
        if found:
            return [found]

    raise TilerError(
        "gdal2tiles was not found.\n"
        "It ships with the GDAL Python bindings -- install the GDAL wheel "
        "into this environment and try again."
    )


# ----------------------------------------------------------------------
# Progress tracking
# ----------------------------------------------------------------------


class _TileCounter:
    """Counts tiles already written under the output folder.

    Progress is measured from the filesystem rather than parsed from
    gdal2tiles' stdout. Its progress bar is drawn by GDAL's C-level
    TermProgress, which writes to a block-buffered C stdout when stdout is a
    pipe -- PYTHONUNBUFFERED does not reach it, so nothing arrives until the
    process exits. Counting files works regardless, and gives an honest
    throughput figure for the ETA.
    """

    EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self._last_duration = 0.0

    def count(self) -> int:
        start = time.monotonic()
        total = 0

        try:
            zoom_dirs = [
                entry.path
                for entry in os.scandir(self.output_dir)
                if entry.is_dir() and entry.name.isdigit()
            ]
        except OSError:
            return 0

        for zoom_dir in zoom_dirs:
            try:
                column_dirs = [e.path for e in os.scandir(zoom_dir) if e.is_dir()]
            except OSError:
                continue

            for column in column_dirs:
                try:
                    with os.scandir(column) as entries:
                        for entry in entries:
                            if entry.name.lower().endswith(self.EXTENSIONS):
                                total += 1
                except OSError:
                    continue

        self._last_duration = time.monotonic() - start
        return total

    @property
    def poll_interval(self) -> float:
        """Back off on huge trees so counting never dominates the runtime."""
        return min(5.0, max(0.5, self._last_duration * 5))


# ----------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------


def run(
    settings: dict,
    on_progress: ProgressFn | None = None,
    on_message: MessageFn | None = None,
    on_stats: StatsFn | None = None,
    is_cancelled: CancelFn | None = None,
) -> str:
    """Tile the raster described by `settings`. Returns the output folder."""
    progress = on_progress or (lambda _p: None)
    message = on_message or (lambda _m: None)
    stats = on_stats or (lambda _s: None)
    cancelled = is_cancelled or (lambda: False)

    input_path = str(settings.get("input_path", "")).strip()
    output_dir = str(settings.get("output_dir", "")).strip()

    if not input_path:
        raise TilerError("No input raster selected.")
    if not os.path.isfile(input_path):
        raise TilerError(f"Input raster does not exist:\n{input_path}")
    if not output_dir:
        raise TilerError("No output folder selected.")

    want_xyz, want_mbtiles, want_pmtiles = wanted_formats(settings)
    if not (want_xyz or want_mbtiles or want_pmtiles):
        raise TilerError("No output format selected.")

    tile_tree = tiles_dir(output_dir)
    # Only ever remove a tile tree this run created. One that was already
    # there belongs to the user, even when they asked for archives only.
    tree_existed = os.path.isdir(tile_tree)

    try:
        os.makedirs(tile_tree, exist_ok=True)
    except OSError as exc:
        raise TilerError(f"Could not create output folder:\n{exc}") from exc

    command = gdal2tiles_command()

    temp_vrt: str | None = None
    try:
        info = raster.describe(input_path)
        prediction = estimate.estimate(info, settings)

        if prediction.total_tiles:
            message(
                f"Tiling {estimate.format_count(prediction.total_tiles)} tiles "
                f"from {info.width} x {info.height} px"
            )
        else:
            message(f"Input: {info.summary()}")

        source = input_path
        if not info.is_byte:
            # gdal2tiles clamps non-Byte data instead of rescaling it.
            message(
                f"{info.data_type} input detected -- rescaling to 8-bit "
                "so the tiles keep their contrast."
            )
            temp_vrt = raster.make_byte_vrt(input_path, info)
            source = temp_vrt

        argv = command + build_arguments(settings) + [source, tile_tree]
        progress(0)

        packing = want_mbtiles or want_pmtiles
        ceiling = _TILING_SHARE if packing else 100

        _stream_process(
            argv,
            tile_tree,
            prediction.total_tiles,
            progress,
            message,
            stats,
            cancelled,
            settings,
            ceiling,
        )

    except RasterError as exc:
        raise TilerError(str(exc)) from exc
    finally:
        if temp_vrt:
            raster._quiet_remove(temp_vrt)

    if want_mbtiles or want_pmtiles:
        _package_archives(
            settings,
            input_path,
            output_dir,
            tile_tree,
            want_mbtiles,
            want_pmtiles,
            progress,
            message,
            cancelled,
        )

    if not want_xyz and not tree_existed:
        message("Removing the intermediate tile folder...")
        shutil.rmtree(tile_tree, ignore_errors=True)

    progress(100)
    return output_dir


def _package_archives(
    settings: dict,
    input_path: str,
    output_dir: str,
    tile_tree: str,
    want_mbtiles: bool,
    want_pmtiles: bool,
    progress: ProgressFn,
    message: MessageFn,
    cancelled: CancelFn,
):
    """Pack the tile tree into the requested single-file archives."""
    span = 100 - _TILING_SHARE

    def on_pack_progress(done: int, total: int):
        if total > 0:
            progress(_TILING_SHARE + int(done * span / total))

    try:
        results = package.package(
            tile_tree,
            output_dir,
            output_name(input_path),
            want_mbtiles=want_mbtiles,
            want_pmtiles=want_pmtiles,
            tile_size=int(_get(settings, "tile_size") or 256),
            # What we asked gdal2tiles for. Only a hint -- package.py
            # confirms it against the tiles themselves, since a folder can
            # also arrive from an earlier run with different settings.
            scheme="xyz" if _get(settings, "xyz") else "tms",
            on_progress=on_pack_progress,
            on_message=message,
            is_cancelled=cancelled,
        )
    except package.PackageCancelled as exc:
        raise Cancelled() from exc
    except package.PackageError as exc:
        raise TilerError(str(exc)) from exc

    for label in ("mbtiles", "pmtiles"):
        path = results.get(label)
        if path:
            message(f"Wrote {os.path.basename(path)}")


def _stream_process(
    argv: list[str],
    tile_tree: str,
    expected_tiles: int,
    progress: ProgressFn,
    message: MessageFn,
    stats: StatsFn,
    cancelled: CancelFn,
    settings: dict,
    ceiling: int = 100,
):
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    creation_flags = 0
    if os.name == "nt":
        # Stop a console window flashing up in front of the GUI.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=0,
            errors="replace",
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise TilerError(f"Could not start gdal2tiles:\n{exc}") from exc

    # stdout is drained on a side thread purely so the pipe never fills and
    # blocks the child; the text is kept only for error reporting.
    chunks: "queue.Queue[str | None]" = queue.Queue()

    def reader():
        try:
            while True:
                data = process.stdout.read(256)
                if not data:
                    break
                chunks.put(data)
        finally:
            chunks.put(None)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    counter = _TileCounter(tile_tree)
    started = time.monotonic()
    next_poll = started
    reader_done = False
    was_cancelled = False
    tail: list[str] = []
    last_percent = 0

    while True:
        if cancelled() and not was_cancelled:
            was_cancelled = True
            message("Cancelling...")
            _terminate(process)

        # Drain whatever stdout has produced without blocking.
        while True:
            try:
                chunk = chunks.get_nowait()
            except queue.Empty:
                break
            if chunk is None:
                reader_done = True
                break
            tail.append(chunk)
            if len(tail) > 80:
                del tail[:-80]

        now = time.monotonic()
        if now >= next_poll:
            done = counter.count()
            elapsed = now - started
            next_poll = now + counter.poll_interval

            if expected_tiles > 0:
                # Hold just below the ceiling until the process actually
                # exits, so the bar never sits at its maximum with work
                # still running. The ceiling is below 100 when archives are
                # still to be packed after tiling finishes.
                percent = min(
                    ceiling - 1, int(done * ceiling / expected_tiles)
                )
                if percent > last_percent:
                    last_percent = percent
                    progress(percent)

            stats(
                {
                    "done": done,
                    "total": expected_tiles,
                    "elapsed": elapsed,
                    "eta": _eta(done, expected_tiles, elapsed),
                }
            )

        if reader_done and process.poll() is not None:
            break

        time.sleep(0.15)

    process.wait()
    reader_thread.join(timeout=1)

    if was_cancelled:
        raise Cancelled()

    if process.returncode != 0:
        output = "".join(tail).strip()
        detail = output[-1500:] if output else "(no output captured)"
        if settings.get("quiet"):
            detail += (
                "\n\nQuiet mode was on, so gdal2tiles suppressed its own "
                "error text. Turn it off in Advanced to see more."
            )
        raise TilerError(
            f"gdal2tiles exited with code {process.returncode}.\n\n{detail}"
        )

    final = counter.count()
    stats(
        {
            "done": final,
            "total": expected_tiles or final,
            "elapsed": time.monotonic() - started,
            "eta": 0.0,
        }
    )
    message(
        f"Wrote {estimate.format_count(final)} tiles in "
        f"{estimate.format_duration(time.monotonic() - started)}"
    )


def _eta(done: int, total: int, elapsed: float) -> float | None:
    """Seconds remaining, from observed throughput. None until meaningful."""
    if total <= 0 or done <= 0 or elapsed <= 1.0:
        return None
    if done >= total:
        return 0.0

    rate = done / elapsed
    if rate <= 0:
        return None
    return (total - done) / rate


def _terminate(process: subprocess.Popen):
    try:
        process.terminate()
    except OSError:
        return

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
