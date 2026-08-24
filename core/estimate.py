"""Predicting tile count, output size and runtime before a job starts.

Tile counts are computed exactly from the raster's footprint and the tile
pyramid geometry. Size and duration are *estimates* built on measured
averages -- they are meant to tell you "minutes or hours", "megabytes or
gigabytes", not to be precise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.raster import RasterInfo, bounds_in_epsg

# Web Mercator (EPSG:3857) world half-extent, in metres.
MERCATOR_HALF = 20037508.342789244

# Average bytes for one 256x256 tile of aerial imagery, per driver.
# Real files vary hugely with scene content -- dense urban PNG tiles can be
# triple this, uniform farmland a third of it.
BASE_TILE_BYTES = {
    "PNG": 46_000,
    "WEBP": 12_000,
    "JPEG": 16_000,
}

# 256px tiles generated per second by one gdal2tiles process, warping from a
# local GeoTIFF on an SSD.
TILES_PER_SECOND = 22.0

# Fixed cost of starting gdal2tiles and opening the source.
STARTUP_SECONDS = 6.0


@dataclass
class TileEstimate:
    per_zoom: dict[int, int] = field(default_factory=dict)
    total_tiles: int = 0
    bytes_per_tile: int = 0
    total_bytes: int = 0
    seconds: float = 0.0
    exact_count: bool = False
    note: str = ""

    def summary(self) -> str:
        if not self.total_tiles:
            return self.note or "Nothing to estimate."

        about = "" if self.exact_count else "about "
        return (
            f"~{format_count(self.total_tiles)} tiles, "
            f"{about}{format_bytes(self.total_bytes)}, "
            f"~{format_duration(self.seconds)}"
        )


# ----------------------------------------------------------------------
# Tile geometry
# ----------------------------------------------------------------------


def _axis_tiles(lo: float, hi: float, origin: float, world: float, count: int) -> int:
    """How many tiles of a `count`-wide grid the span lo..hi touches."""
    if count <= 0 or world <= 0:
        return 0

    span = world / count
    first = int(math.floor((lo - origin) / span))
    last = int(math.ceil((hi - origin) / span)) - 1

    first = max(0, min(first, count - 1))
    last = max(0, min(last, count - 1))

    return max(1, last - first + 1)


def _mercator_tiles(bounds: tuple, zoom: int) -> int:
    count = 2 ** zoom
    x = _axis_tiles(bounds[0], bounds[2], -MERCATOR_HALF, 2 * MERCATOR_HALF, count)
    y = _axis_tiles(bounds[1], bounds[3], -MERCATOR_HALF, 2 * MERCATOR_HALF, count)
    return x * y


def _geodetic_tiles(bounds: tuple, zoom: int, tms_compatible: bool) -> int:
    # tmscompatible starts from 2 tiles at zoom 0; the default starts from 1.
    if tms_compatible:
        nx, ny = 2 ** (zoom + 1), 2 ** zoom
    else:
        nx = 2 ** zoom
        ny = max(1, 2 ** (zoom - 1)) if zoom else 1

    x = _axis_tiles(bounds[0], bounds[2], -180.0, 360.0, nx)
    y = _axis_tiles(bounds[1], bounds[3], -90.0, 180.0, ny)
    return x * y


def _raster_profile_tiles(info: RasterInfo, tile_size: int, zoom: int, top_zoom: int) -> int:
    """'raster' profile: a pixel-space pyramid, halved at each level down."""
    shrink = 2 ** max(0, top_zoom - zoom)
    width = max(1, info.width / shrink)
    height = max(1, info.height / shrink)
    return math.ceil(width / tile_size) * math.ceil(height / tile_size)


def native_zoom(info: RasterInfo, tile_size: int) -> int:
    """Top pyramid level for the 'raster' profile."""
    longest = max(info.width, info.height)
    return max(0, math.ceil(math.log2(max(1.0, longest / tile_size))))


# ----------------------------------------------------------------------
# Size and duration
# ----------------------------------------------------------------------


def bytes_per_tile(driver: str, tile_size: int, quality: int, lossless: bool) -> int:
    base = BASE_TILE_BYTES.get(driver.upper(), BASE_TILE_BYTES["PNG"])

    if driver.upper() == "WEBP" and lossless:
        # Lossless WebP lands a little under PNG.
        base = int(BASE_TILE_BYTES["PNG"] * 0.7)
    elif driver.upper() in ("WEBP", "JPEG"):
        # Compressed size climbs faster than linearly with quality.
        base = int(base * (max(1, quality) / 75.0) ** 1.6)

    # Pixel count scales with the square of the tile edge.
    return max(1, int(base * (tile_size / 256.0) ** 2))


def duration_seconds(total_tiles: int, tile_size: int, processes: int) -> float:
    if total_tiles <= 0:
        return 0.0

    rate = TILES_PER_SECOND * (256.0 / max(1, tile_size)) ** 2

    # Extra processes help, but not linearly -- IO and the merge phase don't
    # parallelise cleanly.
    rate *= max(1, processes) ** 0.8

    return STARTUP_SECONDS + total_tiles / max(0.5, rate)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def estimate(info: RasterInfo | None, settings: dict) -> TileEstimate:
    """Predict the cost of tiling `info` with `settings`."""
    result = TileEstimate()

    if info is None:
        result.note = "Load a raster to see an estimate."
        return result

    profile = str(settings.get("profile") or "mercator")
    tile_size = int(settings.get("tile_size") or 256)
    zoom_min = int(settings.get("zoom_min") or 0)
    zoom_max = int(settings.get("zoom_max") or 0)
    if zoom_max < zoom_min:
        zoom_min, zoom_max = zoom_max, zoom_min

    if profile == "raster":
        top = native_zoom(info, tile_size)
        levels = range(min(zoom_min, top), min(zoom_max, top) + 1)
        result.per_zoom = {
            z: _raster_profile_tiles(info, tile_size, z, top) for z in levels
        }
        result.exact_count = True

    elif not info.is_georeferenced:
        result.note = (
            "This raster has no georeferencing, so tile counts for the "
            f"'{profile}' profile cannot be predicted. Try the 'raster' profile."
        )
        return result

    else:
        if profile == "geodetic":
            bounds = bounds_in_epsg(info, 4326)
            tms = bool(settings.get("tms_compatible"))
            counter = lambda b, z: _geodetic_tiles(b, z, tms)  # noqa: E731
        else:
            bounds = bounds_in_epsg(info, 3857)
            counter = _mercator_tiles

        if bounds is None:
            result.note = (
                "Could not reproject this raster's bounds, so the tile "
                "count cannot be predicted."
            )
            return result

        result.per_zoom = {
            z: counter(bounds, z) for z in range(zoom_min, zoom_max + 1)
        }
        result.exact_count = True

    result.total_tiles = sum(result.per_zoom.values())

    result.bytes_per_tile = bytes_per_tile(
        str(settings.get("tiledriver") or "PNG"),
        tile_size,
        int(settings.get("quality") or 75),
        bool(settings.get("webp_lossless")),
    )
    result.total_bytes = result.total_tiles * result.bytes_per_tile

    result.seconds = duration_seconds(
        result.total_tiles, tile_size, int(settings.get("processes") or 1)
    )

    if settings.get("exclude_transparent"):
        result.note = (
            "Transparent tiles are excluded, so the real count will be lower."
        )

    return result


# ----------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------


def format_count(value: int) -> str:
    return f"{value:,}"


def format_bytes(value: float) -> str:
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            precision = 0 if value >= 100 else 1
            return f"{value:.{precision}f} {unit}"
    return f"{value:.1f} TB"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))

    if seconds < 60:
        return f"{seconds}s"

    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_clock(seconds: float) -> str:
    """mm:ss / h:mm:ss, for the elapsed and remaining readouts."""
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_metres(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f} km"
    if value >= 1:
        return f"{value:.1f} m"
    return f"{value * 100:.1f} cm"
