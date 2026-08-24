import multiprocessing
import sys

from core.runtime import (
    CHILD_FLAG,
    configure_gdal_environment,
    resource_path,
    run_gdal2tiles_child,
)

STYLESHEET_PATH = resource_path("gui", "style.qss")
ICONS_DIR = resource_path("icons")
APP_ICON_PATH = ICONS_DIR / "icon.png"


def load_stylesheet() -> str:
    """Read gui/style.qss.

    url() paths in a stylesheet resolve against the process working
    directory, not against the .qss file, so the sheet uses a %ICONS%
    placeholder that gets swapped for an absolute path here. That keeps the
    app working no matter where it is launched from, bundled or not.

    A missing stylesheet is not fatal -- the app still runs, unstyled.
    """
    try:
        sheet = STYLESHEET_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not load stylesheet ({exc})", file=sys.stderr)
        return ""

    return sheet.replace("%ICONS%", ICONS_DIR.as_posix())


def run_gui() -> int:
    # Imported here rather than at module scope so the gdal2tiles child
    # never pays for loading Qt.
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Raster Image Tiler")
    app.setStyleSheet(load_stylesheet())

    if APP_ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    else:
        print(f"Warning: app icon not found at {APP_ICON_PATH}", file=sys.stderr)

    window = MainWindow()
    window.show()

    return app.exec()


def main():
    # Must come first. gdal2tiles uses multiprocessing when --processes > 1,
    # and on Windows each worker is spawned by re-launching this executable.
    # Without freeze_support() those workers would fall through and open a
    # new copy of the GUI instead of doing any work.
    multiprocessing.freeze_support()

    # Must run before anything imports osgeo -- PROJ reads its data path at
    # library initialisation time.
    configure_gdal_environment()

    # Packaged, this executable doubles as the gdal2tiles runner.
    if len(sys.argv) > 1 and sys.argv[1] == CHILD_FLAG:
        sys.exit(run_gdal2tiles_child(sys.argv[2:]))

    sys.exit(run_gui())


if __name__ == "__main__":
    main()
