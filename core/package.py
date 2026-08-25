"""Packaging a gdal2tiles output folder into single-file tile archives.

gdal2tiles writes a `{z}/{x}/{y}.ext` tree. That is the right thing to hand a
plain web server, but it is thousands to hundreds of thousands of small files,
which makes it awkward to move, back up, or serve from object storage. Both
formats here carry exactly the same tile bytes in one file instead:

  MBTiles   a SQLite database. Editable -- tiles can be inserted or replaced
            later -- so it is the format to keep as the local master.
  PMTiles   a sealed archive designed to be read over HTTP range requests
            straight from object storage, with no tile server in front of it.
            Made from the MBTiles, never edited afterwards.

Nothing here imports Qt or GDAL. The tiles are copied byte for byte, so
packaging can never change image quality.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from typing import Callable, Iterator

TILE_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")

# MBTiles metadata wants the format name, not the file extension.
_EXTENSION_FORMATS = {
    "png": "png",
    "webp": "webp",
    "jpg": "jpeg",
    "jpeg": "jpeg",
}

_TILE_NAME = re.compile(r"(\d+)\.(png|webp|jpe?g)$", re.IGNORECASE)

# Rows written per transaction. Large enough that SQLite overhead disappears,
# small enough that progress stays responsive and memory stays flat.
_CHUNK = 2000


class PackageError(RuntimeError):
    """Raised when an archive cannot be written."""


class PackageCancelled(Exception):
    """Raised internally when the user cancels during packaging."""


ProgressFn = Callable[[int, int], None]   # done, total
CancelFn = Callable[[], bool]


# ----------------------------------------------------------------------
# Reading the tile tree
# ----------------------------------------------------------------------


def scan_tiles(tiles_dir: str) -> tuple[list[tuple[int, int, int, str]], str]:
    """Every tile under `tiles_dir` as (z, x, y, path), plus the extension.

    y is the XYZ row as gdal2tiles --xyz writes it (origin top-left). The
    flip to MBTiles' bottom-left rows happens at insert time.
    """
    tiles: list[tuple[int, int, int, str]] = []
    extension = ""

    try:
        zoom_entries = sorted(os.scandir(tiles_dir), key=lambda e: e.name)
    except OSError as exc:
        raise PackageError(f"Could not read the tile folder:\n{exc}") from exc

    for zoom_entry in zoom_entries:
        if not zoom_entry.is_dir() or not zoom_entry.name.isdigit():
            continue
        zoom = int(zoom_entry.name)

        try:
            column_entries = os.scandir(zoom_entry.path)
        except OSError:
            continue

        for column_entry in column_entries:
            if not column_entry.is_dir() or not column_entry.name.isdigit():
                continue
            column = int(column_entry.name)

            try:
                with os.scandir(column_entry.path) as rows:
                    for row_entry in rows:
                        match = _TILE_NAME.fullmatch(row_entry.name)
                        if not match:
                            continue
                        extension = extension or match.group(2).lower()
                        tiles.append(
                            (zoom, column, int(match.group(1)), row_entry.path)
                        )
            except OSError:
                continue

    if not tiles:
        raise PackageError(
            f"No tiles were found under:\n{tiles_dir}\n\n"
            "Expected a gdal2tiles {z}/{x}/{y} folder tree."
        )

    return tiles, extension


# ----------------------------------------------------------------------
# Bounds
# ----------------------------------------------------------------------


def _bounds_from_tilemapresource(tiles_dir: str) -> list[float] | None:
    """The raster's real footprint, as gdal2tiles recorded it.

    For the mercator profile the BoundingBox in this file is in degrees
    despite the SRS element saying EPSG:3857 -- that is a gdal2tiles quirk,
    and degrees is what MBTiles metadata wants anyway.
    """
    path = os.path.join(tiles_dir, "tilemapresource.xml")
    if not os.path.isfile(path):
        return None

    try:
        box = ET.parse(path).getroot().find("BoundingBox")
        if box is None:
            return None
        bounds = [
            float(box.get(key))
            for key in ("minx", "miny", "maxx", "maxy")
        ]
    except (ET.ParseError, TypeError, ValueError, OSError):
        return None

    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return None
    return bounds


def _tile_to_lon_lat(zoom: int, x: int, y: int) -> tuple[float, float]:
    """North-west corner of an XYZ tile, in degrees."""
    n = 1 << zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def _bounds_from_tiles(tiles: list[tuple[int, int, int, str]]) -> list[float]:
    """Fallback footprint, derived from the tiles actually present.

    Slightly looser than the raster's true extent because it snaps out to
    tile edges, but it never disagrees with the archive's contents.
    """
    top_zoom = max(tile[0] for tile in tiles)
    at_zoom = [tile for tile in tiles if tile[0] == top_zoom]
    limit = (1 << top_zoom) - 1

    min_x = min(tile[1] for tile in at_zoom)
    max_x = max(tile[1] for tile in at_zoom)
    rows = [tile[2] for tile in at_zoom]
    if scheme == "tms":
        rows = [limit - row for row in rows]

    west, north = _tile_to_lon_lat(top_zoom, min_x, min(rows))
    east, south = _tile_to_lon_lat(top_zoom, max_x + 1, max(rows) + 1)
    return [west, south, east, north]


# ----------------------------------------------------------------------
# Tile numbering scheme
# ----------------------------------------------------------------------


def _lat_to_row_xyz(latitude: float, zoom: int) -> float:
    """XYZ row (origin top-left) containing a latitude."""
    clamped = max(-85.05112878, min(85.05112878, latitude))
    fraction = (
        1 - math.asinh(math.tan(math.radians(clamped))) / math.pi
    ) / 2
    return fraction * (1 << zoom)


def detect_scheme(
    tiles: list[tuple[int, int, int, str]],
    bounds: list[float] | None,
    declared: str | None = None,
) -> str:
    """Whether the filenames hold XYZ rows or TMS rows.

    This has to be established rather than assumed. gdal2tiles writes TMS
    rows (origin bottom-left) by default and XYZ rows (origin top-left)
    only with --xyz, while MBTiles always stores TMS. Guess wrong and every
    tile lands in the mirrored row: no error, no missing tiles, just an
    archive whose imagery is not where its own bounds say it is.

    When the raster's real bounds are known the answer is read off the data
    -- the rows present either sit in the XYZ band for that latitude or the
    TMS one. `declared` (what the tiling settings asked for) is the fallback
    for a folder with no bounds to check against.
    """
    fallback = declared if declared in ("xyz", "tms") else "tms"
    if not bounds:
        return fallback

    top_zoom = max(tile[0] for tile in tiles)
    rows = [tile[2] for tile in tiles if tile[0] == top_zoom]
    if not rows:
        return fallback

    limit = (1 << top_zoom) - 1
    edges = sorted(
        (_lat_to_row_xyz(bounds[3], top_zoom),
         _lat_to_row_xyz(bounds[1], top_zoom))
    )
    # A tile of slack at each end: gdal2tiles covers whole tiles, so the
    # rows present spill slightly past the raster's own edges.
    low, high = edges[0] - 1, edges[1] + 1

    lowest, highest = min(rows), max(rows)
    as_xyz = low <= lowest and highest <= high
    as_tms = low <= limit - highest and limit - lowest <= high

    if as_xyz and not as_tms:
        return "xyz"
    if as_tms and not as_xyz:
        return "tms"
    # Both fit (a raster straddling the equator symmetrically, where the two
    # numberings coincide and the choice cannot matter) or neither does.
    return fallback


# ----------------------------------------------------------------------
# MBTiles
# ----------------------------------------------------------------------


def _rows(
    tiles: list[tuple[int, int, int, str]],
    scheme: str,
) -> Iterator[tuple[int, int, int, sqlite3.Binary]]:
    for zoom, column, row, path in tiles:
        # MBTiles always stores TMS rows, so an XYZ tree has to be flipped
        # and a TMS one must be left exactly as it is.
        row_tms = row if scheme == "tms" else (1 << zoom) - 1 - row
        with open(path, "rb") as handle:
            yield zoom, column, row_tms, sqlite3.Binary(handle.read())


def write_mbtiles(
    tiles_dir: str,
    output_path: str,
    name: str = "tiles",
    tile_size: int | None = None,
    scheme: str | None = None,
    on_progress: ProgressFn | None = None,
    is_cancelled: CancelFn | None = None,
) -> tuple[int, dict]:
    """Pack a tile tree into `output_path`. Returns (tile count, metadata)."""
    progress = on_progress or (lambda _d, _t: None)
    cancelled = is_cancelled or (lambda: False)

    tiles, extension = scan_tiles(tiles_dir)
    zooms = [tile[0] for tile in tiles]

    declared_bounds = _bounds_from_tilemapresource(tiles_dir)
    scheme = detect_scheme(tiles, declared_bounds, scheme)
    bounds = declared_bounds or _bounds_from_tiles(tiles, scheme)

    metadata = {
        "name": name,
        "description": name,
        "type": "overlay",
        "version": "1.1",
        "format": _EXTENSION_FORMATS.get(extension, extension),
        "minzoom": str(min(zooms)),
        "maxzoom": str(max(zooms)),
        "bounds": ",".join(f"{value:.10f}" for value in bounds),
        "center": (
            f"{(bounds[0] + bounds[2]) / 2:.10f},"
            f"{(bounds[1] + bounds[3]) / 2:.10f},"
            f"{max(zooms)}"
        ),
    }
    if tile_size and int(tile_size) != 256:
        # Not in the MBTiles spec, which assumes 256. Recorded anyway so a
        # viewer that reads it can set up the right tile grid instead of
        # rendering every tile at a quarter scale.
        metadata["tilesize"] = str(int(tile_size))

    _quiet_remove(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    total = len(tiles)
    written = 0

    try:
        connection = sqlite3.connect(output_path)
    except sqlite3.Error as exc:
        raise PackageError(f"Could not create {output_path}:\n{exc}") from exc

    try:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE metadata (name text, value text);
            CREATE TABLE tiles (
                zoom_level  integer,
                tile_column integer,
                tile_row    integer,
                tile_data   blob
            );
            """
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)", metadata.items()
        )

        source = _rows(tiles, scheme)
        while True:
            if cancelled():
                raise PackageCancelled()

            chunk = []
            for row in source:
                chunk.append(row)
                if len(chunk) >= _CHUNK:
                    break
            if not chunk:
                break

            connection.executemany(
                "INSERT INTO tiles VALUES (?, ?, ?, ?)", chunk
            )
            connection.commit()
            written += len(chunk)
            progress(written, total)

        # Built after the inserts rather than before: maintaining a unique
        # index during a bulk load is markedly slower than creating it once.
        connection.execute(
            "CREATE UNIQUE INDEX tile_index ON tiles "
            "(zoom_level, tile_column, tile_row)"
        )
        connection.commit()
    except PackageCancelled:
        connection.close()
        _quiet_remove(output_path)
        raise
    except (sqlite3.Error, OSError) as exc:
        connection.close()
        _quiet_remove(output_path)
        raise PackageError(f"Could not write {output_path}:\n{exc}") from exc
    else:
        connection.close()

    return written, metadata


# ----------------------------------------------------------------------
# PMTiles
# ----------------------------------------------------------------------


def write_pmtiles(mbtiles_path: str, output_path: str) -> None:
    """Convert an MBTiles archive into a sealed PMTiles archive.

    The conversion is imported rather than shelled out to, so it still works
    from a PyInstaller build where there is no console script on PATH.
    """
    try:
        from pmtiles.convert import mbtiles_to_pmtiles
    except ImportError as exc:
        raise PackageError(
            "PMTiles output needs the 'pmtiles' package.\n\n"
            "Install it with:\n    pip install pmtiles"
        ) from exc

    try:
        connection = sqlite3.connect(mbtiles_path)
        row = connection.execute(
            "SELECT value FROM metadata WHERE name = 'maxzoom'"
        ).fetchone()
        connection.close()
        max_zoom = int(row[0]) if row else 22
    except (sqlite3.Error, TypeError, ValueError):
        max_zoom = 22

    _quiet_remove(output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    try:
        mbtiles_to_pmtiles(mbtiles_path, output_path, max_zoom)
    except Exception as exc:
        _quiet_remove(output_path)
        raise PackageError(
            f"Could not write {output_path}:\n{exc}"
        ) from exc


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


def package(
    tiles_dir: str,
    output_root: str,
    stem: str,
    want_mbtiles: bool,
    want_pmtiles: bool,
    tile_size: int | None = None,
    scheme: str | None = None,
    on_progress: ProgressFn | None = None,
    on_message: Callable[[str], None] | None = None,
    is_cancelled: CancelFn | None = None,
) -> dict[str, str]:
    """Build the requested archives from `tiles_dir`.

    Returns {"mbtiles": path, "pmtiles": path} for whatever was produced.
    PMTiles is always made from an MBTiles; when only PMTiles was asked for,
    that intermediate is written under the pmtiles folder and removed after.
    """
    message = on_message or (lambda _m: None)
    results: dict[str, str] = {}

    if not (want_mbtiles or want_pmtiles):
        return results

    mbtiles_path = os.path.join(output_root, "mbtiles", f"{stem}.mbtiles")
    keep_mbtiles = want_mbtiles
    if not keep_mbtiles:
        mbtiles_path = os.path.join(
            output_root, "pmtiles", f"{stem}.mbtiles.tmp"
        )

    message("Packaging tiles into MBTiles...")
    count, _metadata = write_mbtiles(
        tiles_dir,
        mbtiles_path,
        name=stem,
        tile_size=tile_size,
        scheme=scheme,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    if keep_mbtiles:
        results["mbtiles"] = mbtiles_path

    try:
        if want_pmtiles:
            message(f"Sealing {count:,} tiles into PMTiles...")
            pmtiles_path = os.path.join(
                output_root, "pmtiles", f"{stem}.pmtiles"
            )
            write_pmtiles(mbtiles_path, pmtiles_path)
            results["pmtiles"] = pmtiles_path
    finally:
        if not keep_mbtiles:
            _quiet_remove(mbtiles_path)

    return results


def _quiet_remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
