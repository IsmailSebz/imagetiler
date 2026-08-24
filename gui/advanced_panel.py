"""Advanced gdal2tiles options.

Everything not on the main Tile Settings form lives here. `values()` returns
a plain dict. core.tiler.build_arguments() turns the merged settings into
gdal2tiles flags, so argument building lives in one place only.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.layout_utils import (
    NARROW_FIELD_WIDTH,
    field_label,
    make_form,
    separator,
    size_field,
    sub_section_label,
)

PROFILES = ["mercator", "geodetic", "raster"]

WEB_VIEWERS = ["all", "google", "openlayers", "leaflet", "mapml", "none"]

KML_MODES = ["auto", "force", "none"]


class AdvancedPanel(QWidget):

    # Any control here changing -- the window re-runs its estimate.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(4)

        # -- Tile scheme ---------------------------------------------------
        root.addWidget(sub_section_label("Tile scheme"))
        scheme = make_form()

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(PROFILES)
        self.profile_combo.setToolTip(
            "-p / --profile (default mercator)\n"
            "mercator - Google/OSM compatible\n"
            "geodetic - EPSG:4326\n"
            "raster   - pixel-space pyramid, no reprojection"
        )
        size_field(self.profile_combo)
        scheme.addRow(field_label("Profile:"), self.profile_combo)

        self.xyz_check = QCheckBox("XYZ tiles (OSM slippy map)")
        self.xyz_check.setToolTip(
            "--xyz\nDefault is TMS, where y=0 is the southern-most tile.\n"
            "XYZ (used by OSM, Leaflet and WMTS) puts y=0 at the north."
        )
        scheme.addRow(field_label(""), self.xyz_check)

        self.tms_compatible_check = QCheckBox("TMS-compatible resolution")
        self.tms_compatible_check.setToolTip(
            "-d / --tmscompatible\nGeodetic profile only: base resolution "
            "0.703125, i.e. 2 tiles at zoom 0."
        )
        scheme.addRow(field_label(""), self.tms_compatible_check)

        root.addLayout(scheme)
        root.addWidget(separator())

        # -- Tiling behaviour ----------------------------------------------
        root.addWidget(sub_section_label("Tiling behaviour"))
        tiling = make_form()

        self.resume_check = QCheckBox("Resume - write missing tiles only")
        self.resume_check.setToolTip("-e / --resume")
        tiling.addRow(field_label(""), self.resume_check)

        self.exclude_check = QCheckBox("Exclude transparent tiles")
        self.exclude_check.setToolTip("-x / --exclude")
        tiling.addRow(field_label(""), self.exclude_check)

        self.excluded_values_edit = QLineEdit()
        self.excluded_values_edit.setPlaceholderText("(R,G,B),(R,G,B)")
        self.excluded_values_edit.setToolTip(
            "--excluded-values\nPixel tuples ignored when resampling. "
            "Average resampling only."
        )
        size_field(self.excluded_values_edit)
        tiling.addRow(field_label("Excluded values:"), self.excluded_values_edit)

        self.excluded_pct_spin = QSpinBox()
        self.excluded_pct_spin.setRange(0, 100)
        self.excluded_pct_spin.setValue(50)
        self.excluded_pct_spin.setSuffix(" %")
        self.excluded_pct_spin.setToolTip(
            "--excluded-values-pct-threshold (default 50)"
        )
        size_field(self.excluded_pct_spin, NARROW_FIELD_WIDTH)
        tiling.addRow(field_label("Excluded pct:"), self.excluded_pct_spin)

        root.addLayout(tiling)
        root.addWidget(separator())

        # -- Web viewer ----------------------------------------------------
        root.addWidget(sub_section_label("Web viewer"))
        viewer = make_form()

        self.webviewer_combo = QComboBox()
        self.webviewer_combo.addItems(WEB_VIEWERS)
        self.webviewer_combo.setToolTip("-w / --webviewer (default all)")
        size_field(self.webviewer_combo)
        viewer.addRow(field_label("Viewer:"), self.webviewer_combo)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Map title")
        self.title_edit.setToolTip("-t / --title")
        size_field(self.title_edit)
        viewer.addRow(field_label("Title:"), self.title_edit)

        self.copyright_edit = QLineEdit()
        self.copyright_edit.setPlaceholderText("Copyright notice")
        self.copyright_edit.setToolTip("-c / --copyright")
        size_field(self.copyright_edit)
        viewer.addRow(field_label("Copyright:"), self.copyright_edit)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com/tiles/")
        self.url_edit.setToolTip(
            "-u / --url\nWhere the tiles will be published."
        )
        size_field(self.url_edit)
        viewer.addRow(field_label("Publish URL:"), self.url_edit)

        self.google_key_edit = QLineEdit()
        self.google_key_edit.setPlaceholderText("Google Maps API key")
        self.google_key_edit.setToolTip("-g / --googlekey")
        size_field(self.google_key_edit)
        viewer.addRow(field_label("Google key:"), self.google_key_edit)

        self.bing_key_edit = QLineEdit()
        self.bing_key_edit.setPlaceholderText("Bing Maps API key")
        self.bing_key_edit.setToolTip("-b / --bingkey")
        size_field(self.bing_key_edit)
        viewer.addRow(field_label("Bing key:"), self.bing_key_edit)

        self.mapml_template_edit = QLineEdit()
        self.mapml_template_edit.setPlaceholderText("MapML template file")
        self.mapml_template_edit.setToolTip("--mapml-template")
        size_field(self.mapml_template_edit)
        viewer.addRow(field_label("MapML template:"), self.mapml_template_edit)

        root.addLayout(viewer)
        root.addWidget(separator())

        # -- KML -----------------------------------------------------------
        root.addWidget(sub_section_label("Google Earth (KML)"))
        kml = make_form()

        self.kml_combo = QComboBox()
        self.kml_combo.addItems(KML_MODES)
        self.kml_combo.setToolTip(
            "auto  - default behaviour\n"
            "force - -k / --force-kml\n"
            "none  - -n / --no-kml"
        )
        size_field(self.kml_combo)
        kml.addRow(field_label("KML output:"), self.kml_combo)

        root.addLayout(kml)
        root.addWidget(separator())

        # -- Console output ------------------------------------------------
        root.addWidget(sub_section_label("Console output"))
        verbosity = make_form()

        self.verbose_check = QCheckBox("Verbose (-v)")
        verbosity.addRow(field_label(""), self.verbose_check)

        self.quiet_check = QCheckBox("Quiet (-q)")
        verbosity.addRow(field_label(""), self.quiet_check)

        root.addLayout(verbosity)

        # Verbose and quiet contradict each other.
        self.verbose_check.toggled.connect(
            lambda on: on and self.quiet_check.setChecked(False)
        )
        self.quiet_check.toggled.connect(
            lambda on: on and self.verbose_check.setChecked(False)
        )

        self._forward_changes()

    def _forward_changes(self):
        """Re-emit every control's change as a single `changed` signal."""
        relay = lambda *_: self.changed.emit()  # noqa: E731

        for combo in self.findChildren(QComboBox):
            combo.currentTextChanged.connect(relay)
        for spin in self.findChildren(QSpinBox):
            spin.valueChanged.connect(relay)
        for check in self.findChildren(QCheckBox):
            check.toggled.connect(relay)
        for edit in self.findChildren(QLineEdit):
            edit.textChanged.connect(relay)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def values(self) -> dict:
        return {
            "profile": self.profile_combo.currentText(),
            "xyz": self.xyz_check.isChecked(),
            "tms_compatible": self.tms_compatible_check.isChecked(),
            "resume": self.resume_check.isChecked(),
            "exclude_transparent": self.exclude_check.isChecked(),
            "excluded_values": self.excluded_values_edit.text().strip(),
            "excluded_pct": self.excluded_pct_spin.value(),
            "webviewer": self.webviewer_combo.currentText(),
            "title": self.title_edit.text().strip(),
            "copyright": self.copyright_edit.text().strip(),
            "url": self.url_edit.text().strip(),
            "google_key": self.google_key_edit.text().strip(),
            "bing_key": self.bing_key_edit.text().strip(),
            "mapml_template": self.mapml_template_edit.text().strip(),
            "kml": self.kml_combo.currentText(),
            "verbose": self.verbose_check.isChecked(),
            "quiet": self.quiet_check.isChecked(),
        }

