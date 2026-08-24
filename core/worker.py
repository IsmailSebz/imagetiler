"""Qt worker that runs a tiling job off the GUI thread.

This is the only file in core/ that imports Qt, and it is deliberately thin:
it owns no tiling logic, it just adapts core.tiler's callbacks to signals.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from core import raster, tiler
from core.raster import RasterError
from core.tiler import Cancelled, TilerError


class MetadataLoader(QObject):
    """Reads raster headers off the GUI thread.

    Only the header is touched -- no pixels. The reason this needs a thread
    at all is `import osgeo.gdal`, which pulls in a large stack of native
    driver libraries and costs a noticeable pause the first time. Warming it
    up here means picking a file never blocks the window.
    """

    loaded = pyqtSignal(str, object)   # path, RasterInfo
    failed = pyqtSignal(str, str)      # path, message
    ready = pyqtSignal(str)            # GDAL version, once warmed up

    @pyqtSlot()
    def warm_up(self):
        """Pay the GDAL import cost before the user picks anything."""
        version = raster.gdal_version()
        if version:
            self.ready.emit(version)

    @pyqtSlot(str)
    def load(self, path: str):
        try:
            info = raster.describe(path)
        except RasterError as exc:
            self.failed.emit(path, str(exc))
            return
        except Exception as exc:
            self.failed.emit(path, f"Could not read {path}:\n{exc!r}")
            return

        # Warm the projection cache here rather than on the GUI thread, so
        # the first estimate does not stall on PROJ opening its database.
        if info.is_georeferenced:
            for epsg in (3857, 4326):
                try:
                    raster.bounds_in_epsg(info, epsg)
                except Exception:
                    pass

        self.loaded.emit(path, info)


class TileWorker(QObject):
    """Runs one gdal2tiles job. Move it to a QThread and call run()."""

    progress = pyqtSignal(int)      # 0-100
    message = pyqtSignal(str)       # status line text
    stats = pyqtSignal(dict)        # {"done", "total", "elapsed", "eta"}
    failed = pyqtSignal(str)        # error detail
    cancelled = pyqtSignal()
    succeeded = pyqtSignal(str)     # output folder
    finished = pyqtSignal()         # always last, for thread teardown

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(settings)
        self._cancel_requested = False

    def cancel(self):
        """Safe to call from the GUI thread while run() is in flight."""
        self._cancel_requested = True

    @pyqtSlot()
    def run(self):
        try:
            output_dir = tiler.run(
                self._settings,
                on_progress=self.progress.emit,
                on_message=self.message.emit,
                on_stats=self.stats.emit,
                is_cancelled=lambda: self._cancel_requested,
            )
        except Cancelled:
            self.cancelled.emit()
        except TilerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never let a worker thread die silently
            self.failed.emit(f"Unexpected error:\n{exc!r}")
        else:
            self.succeeded.emit(output_dir)
        finally:
            self.finished.emit()
