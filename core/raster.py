"""Reading metadata out of a raster, and preparing it for gdal2tiles.

GDAL is imported lazily so the GUI still starts (and reports a clear error)
on a machine where the bindings are missing.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass

# Mean Earth radius used for degree -> metre conversions.
EARTH_RADIUS_M = 6378137.0
METRES_PER_DEGREE_LAT = 111320.0


class RasterError(RuntimeError):
    """Raised when a raster cannot be opened or converted."""


def import_gdal():
    """Return the gdal module, or raise RasterError with a useful message."""
    try:
        from osgeo import gdal
    except ImportError as exc:
        raise RasterError(
            "GDAL Python bindings are not installed in this environment.\n"
            "Install a wheel matching your Python version, e.g.\n"
            "  pip install gdal-3.12.2-cp312-cp312-win_amd64.whl"
        ) from exc

    gdal.UseExceptions()
    return gdal


def import_osr():
    try:
        from osgeo import osr
    except ImportError as exc:
        raise RasterError("GDAL Python bindings are not installed.") from exc
    osr.UseExceptions()
    return osr


def gdal_version() -> str | None:
    try:
        gdal = import_gdal()
    except RasterError:
        return None
    return gdal.__version__


@dataclass(frozen=True)
class RasterInfo:
    path: str
    driver: str
    width: int
    height: int
    band_count: int
    data_type: str
    is_byte: bool
    nodata: float | None
    file_size: int

    srs_wkt: str | None
    srs_code: str | None       # e.g. "EPSG:32633"
    srs_name: str | None       # e.g. "WGS 84 / UTM zone 33N"
    is_projected: bool

    geotransform: tuple | None
    bounds: tuple | None       # (minx, miny, maxx, maxy) in the source CRS

    # Ground measurements, always in metres regardless of the source CRS.
    pixel_size_m: tuple | None   # (x, y) ground sample distance
    ground_size_m: tuple | None  # (width, height)

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000

    @property
    def is_georeferenced(self) -> bool:
        return self.srs_wkt is not None and self.bounds is not None

    def summary(self) -> str:
        srs = self.srs_code or self.srs_name or "no SRS"
        return (
            f"{self.width} x {self.height} px, {self.band_count} band"
            f"{'s' if self.band_count != 1 else ''}, {self.data_type}, {srs}"
        )


def describe(path: str) -> RasterInfo:
    """Open `path` and read its metadata. Does not read pixels."""
    gdal = import_gdal()

    if not os.path.isfile(path):
        raise RasterError(f"File not found: {path}")

    try:
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
    except Exception as exc:
        raise RasterError(f"GDAL could not open {path}:\n{exc}") from exc

    if dataset is None:
        raise RasterError(f"GDAL could not open {path}")

    try:
        width = dataset.RasterXSize
        height = dataset.RasterYSize

        band = dataset.GetRasterBand(1) if dataset.RasterCount else None
        data_type = (
            gdal.GetDataTypeName(band.DataType) if band is not None else "Unknown"
        )
        nodata = band.GetNoDataValue() if band is not None else None

        srs_wkt = srs_code = srs_name = None
        is_projected = False

        spatial_ref = dataset.GetSpatialRef()
        if spatial_ref is not None:
            srs_wkt = spatial_ref.ExportToWkt()
            srs_name = spatial_ref.GetName()
            is_projected = bool(spatial_ref.IsProjected())

            # AutoIdentifyEPSG fills in the authority when the file stores a
            # bare WKT with no EPSG code attached.
            try:
                spatial_ref.AutoIdentifyEPSG()
            except Exception:
                pass

            authority = spatial_ref.GetAuthorityName(None)
            code = spatial_ref.GetAuthorityCode(None)
            if authority and code:
                srs_code = f"{authority}:{code}"

        geotransform = None
        bounds = None
        pixel_size_m = None
        ground_size_m = None

        try:
            gt = dataset.GetGeoTransform(can_return_null=True)
        except Exception:
            gt = None

        if gt:
            geotransform = tuple(gt)
            bounds = _bounds_from_geotransform(gt, width, height)
            pixel_size_m, ground_size_m = _ground_measurements(
                gt, width, height, bounds, spatial_ref, is_projected
            )

        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0

        return RasterInfo(
            path=path,
            driver=dataset.GetDriver().ShortName,
            width=width,
            height=height,
            band_count=dataset.RasterCount,
            data_type=data_type,
            is_byte=(data_type == "Byte"),
            nodata=nodata,
            file_size=file_size,
            srs_wkt=srs_wkt,
            srs_code=srs_code,
            srs_name=srs_name,
            is_projected=is_projected,
            geotransform=geotransform,
            bounds=bounds,
            pixel_size_m=pixel_size_m,
            ground_size_m=ground_size_m,
        )
    finally:
        dataset = None


# ----------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------


def _bounds_from_geotransform(gt, width: int, height: int) -> tuple:
    """Axis-aligned bounds of the raster, honouring rotation terms."""
    corners = [(0, 0), (width, 0), (0, height), (width, height)]
    xs, ys = [], []
    for px, py in corners:
        xs.append(gt[0] + px * gt[1] + py * gt[2])
        ys.append(gt[3] + px * gt[4] + py * gt[5])
    return (min(xs), min(ys), max(xs), max(ys))


def _ground_measurements(gt, width, height, bounds, spatial_ref, is_projected):
    """Return ((gsd_x, gsd_y), (ground_w, ground_h)) in metres, or (None, None)."""
    pixel_x = math.hypot(gt[1], gt[4])
    pixel_y = math.hypot(gt[2], gt[5])

    if spatial_ref is None:
        return None, None

    if is_projected:
        # GetLinearUnits returns the metres-per-unit factor (1.0 for metres,
        # 0.3048 for feet, and so on).
        try:
            factor = spatial_ref.GetLinearUnits() or 1.0
        except Exception:
            factor = 1.0
        gsd = (pixel_x * factor, pixel_y * factor)
    else:
        # Geographic CRS: degrees. Longitude degrees shrink towards the poles,
        # so convert at the raster's centre latitude.
        centre_lat = (bounds[1] + bounds[3]) / 2.0
        m_per_deg_lon = METRES_PER_DEGREE_LAT * math.cos(math.radians(centre_lat))
        gsd = (pixel_x * m_per_deg_lon, pixel_y * METRES_PER_DEGREE_LAT)

    return gsd, (gsd[0] * width, gsd[1] * height)


# Reprojecting the same footprint over and over is wasteful: the estimate
# re-runs on every spinbox tick, and building a CoordinateTransformation is
# the expensive part (it opens PROJ's database on first use).
_BOUNDS_CACHE: dict[tuple, tuple | None] = {}


def bounds_in_epsg(info: RasterInfo, epsg: int, samples: int = 16) -> tuple | None:
    """Reproject the raster's bounds into `epsg`.

    Edges are densified before transforming, because a reprojected rectangle
    is generally not a rectangle -- transforming only the four corners can
    understate the extent.
    """
    if not info.is_georeferenced:
        return None

    cache_key = (info.srs_wkt, info.bounds, epsg, samples)
    if cache_key in _BOUNDS_CACHE:
        return _BOUNDS_CACHE[cache_key]

    result = _bounds_in_epsg_uncached(info, epsg, samples)
    _BOUNDS_CACHE[cache_key] = result
    return result


def _bounds_in_epsg_uncached(info: RasterInfo, epsg: int, samples: int):
    osr = import_osr()

    source = osr.SpatialReference()
    source.ImportFromWkt(info.srs_wkt)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    target = osr.SpatialReference()
    target.ImportFromEPSG(epsg)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    if source.IsSame(target):
        return info.bounds

    try:
        transform = osr.CoordinateTransformation(source, target)
    except Exception:
        return None

    minx, miny, maxx, maxy = info.bounds
    points = []
    for i in range(samples + 1):
        t = i / samples
        x = minx + (maxx - minx) * t
        y = miny + (maxy - miny) * t
        points += [(x, miny), (x, maxy), (minx, y), (maxx, y)]

    xs, ys = [], []
    for x, y in points:
        try:
            tx, ty, *_ = transform.TransformPoint(x, y)
        except Exception:
            continue
        if math.isfinite(tx) and math.isfinite(ty):
            xs.append(tx)
            ys.append(ty)

    if not xs or not ys:
        return None

    return (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------------------
# Byte conversion
# ----------------------------------------------------------------------


def make_byte_vrt(path: str, info: RasterInfo | None = None) -> str:
    """Return a path to an 8-bit view of `path`.

    gdal2tiles clamps non-Byte input to Byte without rescaling, which wrecks
    the radiometry of 16-bit drone imagery. This builds a VRT that rescales
    each band's actual range into 0-255 instead. A VRT is a small XML file --
    no pixels are copied, the conversion happens as gdal2tiles reads.
    """
    gdal = import_gdal()

    if info is None:
        info = describe(path)

    handle, vrt_path = tempfile.mkstemp(prefix="tiler_byte_", suffix=".vrt")
    os.close(handle)

    try:
        options = gdal.TranslateOptions(
            format="VRT",
            outputType=gdal.GDT_Byte,
            # An empty scale entry means "auto-compute min/max per band".
            scaleParams=[[]],
        )
        gdal.Translate(vrt_path, path, options=options)
    except Exception as exc:
        _quiet_remove(vrt_path)
        raise RasterError(
            f"Could not build an 8-bit view of {os.path.basename(path)}:\n{exc}"
        ) from exc

    return vrt_path


def _quiet_remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
