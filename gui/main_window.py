"""Main application window for the Raster Image Tiler.

UI only at this stage -- Start builds the gdal2tiles argument list and
reports it. Tiling execution will live in core/tiler.py.
"""

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import estimate, tiler
from core.raster import RasterError
from core.worker import MetadataLoader, TileWorker
from gui.advanced_panel import AdvancedPanel
from gui.collapsible import CollapsibleSection
from gui.layout_utils import (
    FIELD_WIDTH,
    NARROW_FIELD_WIDTH,
    field_label,
    make_form,
    section_label,
    size_field,
)
from gui.properties_panel import PropertiesPanel
from gui.settings_store import SettingsStore

RASTER_FILTER = (
    "Raster images (*.tif *.tiff *.jpg *.jpeg *.png *.vrt);;All files (*)"
)

TILE_SIZES = ["256", "512", "1024"]

# gdal2tiles --tiledriver
TILE_DRIVERS = ["PNG", "WEBP", "JPEG"]

# gdal2tiles -r
RESAMPLING = [
    "average",
    "near",
    "bilinear",
    "cubic",
    "cubicspline",
    "lanczos",
    "mode",
    "max",
    "min",
    "med",
    "q1",
    "q3",
]

COMMON_SRS = [
    "",
    "EPSG:4326",
    "EPSG:3857",
    "EPSG:32633",
    "EPSG:32733",
    "EPSG:21037",
]


class MainWindow(QMainWindow):

    # Emitted to the metadata thread; queued, so it never blocks the GUI.
    metadata_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Raster Image Tiler")
        self.setMinimumSize(400, 300)
        self.resize(620, 540)

        # False until the user picks or types an output path of their own.
        # While False, the output folder tracks the input file.
        self._output_edited_by_user = False

        # Set while a tiling job is in flight.
        self._thread: QThread | None = None
        self._worker: TileWorker | None = None

        # Header of the currently selected raster, or None.
        self._info = None
        # Path whose metadata we are waiting on, to discard stale replies.
        self._pending_path: str | None = None

        # Ticks once a second during a run so the elapsed readout keeps
        # moving between the worker's less frequent stats updates.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._elapsed_seconds = 0
        self._last_eta: float | None = None

        self._store = SettingsStore()

        self._build_ui()
        self._start_metadata_thread()

        # Restore before connecting signals, so replaying saved values does
        # not fire a burst of estimate recalculations.
        advanced_expanded = self._restore_settings()

        self._connect_signals()
        self._update_start_state()
        self._update_driver_options()
        self.advanced_section.toggle_button.setChecked(advanced_expanded)

    # ------------------------------------------------------------------
    # Background metadata thread
    # ------------------------------------------------------------------

    def _start_metadata_thread(self):
        """One long-lived thread for reading raster headers.

        It is created once and reused, rather than spun up per file, because
        the expensive part is importing GDAL -- which we do here at startup
        so the first file the user picks is already fast.
        """
        self._meta_thread = QThread(self)
        self._meta_loader = MetadataLoader()
        self._meta_loader.moveToThread(self._meta_thread)

        self.metadata_requested.connect(self._meta_loader.load)
        self._meta_loader.loaded.connect(self._on_metadata_loaded)
        self._meta_loader.failed.connect(self._on_metadata_failed)
        self._meta_thread.started.connect(self._meta_loader.warm_up)

        self._meta_thread.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 14)
        #root.setSpacing(10)

        root.addLayout(self._build_top_row())
        #root.addWidget(section_label("Settings"))
        root.addWidget(self._build_settings_area(), stretch=1)
        root.addLayout(self._build_footer())

    # -- top: paths on the left, preview on the right --------------------

    def _build_top_row(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setSpacing(5)

        paths = QVBoxLayout()
        paths.setSpacing(4)

        paths.addWidget(self._small_label("input"))
        self.input_edit, self.input_browse = self._path_row(
            paths, placeholder=r"C:\data\drone\orthomosaic.tif"
        )
        paths.addSpacing(6)

        paths.addWidget(self._small_label("output"))
        self.output_edit, self.output_browse = self._path_row(
            paths, placeholder=r"C:\data\tiles"
        )
        paths.addSpacing(20)
        paths.addWidget(section_label("Settings"))
        paths.addStretch(1)

        top.addLayout(paths, stretch=1)

        self.properties = PropertiesPanel()
        self.properties.setMaximumWidth(260)
        top.addWidget(self.properties)

        return top

    # -- middle: scrollable settings box ---------------------------------

    def _build_settings_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        scroll.setWidget(content)

        body = QVBoxLayout(content)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(4)

        self.settings_form = make_form()

        # 1. Zoom, min and max sharing one row -----------------------------
        self.zoom_min_spin = QSpinBox()
        self.zoom_min_spin.setRange(0, 22)
        self.zoom_min_spin.setValue(0)
        self.zoom_min_spin.setToolTip("-z / --zoom, lower bound")
        size_field(self.zoom_min_spin, NARROW_FIELD_WIDTH)

        self.zoom_max_spin = QSpinBox()
        self.zoom_max_spin.setRange(0, 22)
        self.zoom_max_spin.setValue(18)
        self.zoom_max_spin.setToolTip("-z / --zoom, upper bound")
        size_field(self.zoom_max_spin, NARROW_FIELD_WIDTH)

        zoom_row = QWidget()
        zoom_layout = QHBoxLayout(zoom_row)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(6)
        zoom_layout.addWidget(self.zoom_min_spin)
        dash = QLabel("-")
        dash.setObjectName("rangeDash")
        zoom_layout.addWidget(dash)
        zoom_layout.addWidget(self.zoom_max_spin)
        zoom_layout.addStretch(1)
        zoom_row.setFixedWidth(FIELD_WIDTH)

        self.settings_form.addRow(field_label("Zoom:"), zoom_row)

        # 2. Tile driver ---------------------------------------------------
        self.driver_combo = QComboBox()
        self.driver_combo.addItems(TILE_DRIVERS)
        self.driver_combo.setToolTip(
            "--tiledriver (default PNG)\n"
            "PNG  - lossless, supports transparency\n"
            "WEBP - much smaller, supports transparency\n"
            "JPEG - smallest, no transparency (edge tiles go black)"
        )
        size_field(self.driver_combo)
        self.settings_form.addRow(field_label("Tile driver:"), self.driver_combo)

        # 3. Image quality (WEBP / JPEG only) ------------------------------
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(75)
        self.quality_spin.setToolTip(
            "--webp-quality / --jpeg-quality (default 75)"
        )
        size_field(self.quality_spin, NARROW_FIELD_WIDTH)
        self.settings_form.addRow(
            field_label("Image quality:"), self.quality_spin
        )

        # 4. Lossless WEBP -------------------------------------------------
        self.webp_lossless_check = QCheckBox("Lossless WEBP")
        self.webp_lossless_check.setToolTip(
            "--webp-lossless\nOverrides the quality setting."
        )
        self.settings_form.addRow(field_label(""), self.webp_lossless_check)

        # 5. Tile size -----------------------------------------------------
        self.tile_size_combo = QComboBox()
        self.tile_size_combo.addItems(TILE_SIZES)
        self.tile_size_combo.setToolTip("--tilesize (default 256)")
        size_field(self.tile_size_combo)
        self.settings_form.addRow(field_label("Tile size:"), self.tile_size_combo)

        # 6. Resampling ----------------------------------------------------
        self.resampling_combo = QComboBox()
        self.resampling_combo.addItems(RESAMPLING)
        self.resampling_combo.setToolTip(
            "-r / --resampling (default average)\n"
            "average is right for imagery; near preserves hard class "
            "boundaries in categorical rasters."
        )
        size_field(self.resampling_combo)
        self.settings_form.addRow(
            field_label("Resampling:"), self.resampling_combo
        )

        # 7. Source SRS ----------------------------------------------------
        self.srs_combo = QComboBox()
        self.srs_combo.setEditable(True)
        self.srs_combo.addItems(COMMON_SRS)
        self.srs_combo.setCurrentText("")
        self.srs_combo.lineEdit().setPlaceholderText("auto-detect")
        self.srs_combo.setToolTip(
            "-s / --s_srs\nSource spatial reference system. Leave blank to "
            "use whatever is embedded in the raster.\n"
            "Accepts EPSG:xxxx, a PROJ string, or WKT."
        )
        size_field(self.srs_combo)
        self.settings_form.addRow(field_label("Source SRS:"), self.srs_combo)

        # 8. Source nodata + transparency threshold, one row ---------------
        self.src_nodata_edit = QLineEdit()
        self.src_nodata_edit.setPlaceholderText("e.g. 0 or 0,0,0")
        self.src_nodata_edit.setToolTip(
            "-a / --srcnodata\nValue in the input treated as transparent. "
            "Overrides any nodata already set on the dataset."
        )

        self.nodata_pct_spin = QSpinBox()
        self.nodata_pct_spin.setRange(0, 100)
        self.nodata_pct_spin.setValue(100)
        self.nodata_pct_spin.setSuffix(" %")
        self.nodata_pct_spin.setToolTip(
            "--nodata-values-pct-threshold (default 100)\nHow much of a "
            "source pixel must be nodata before the tile pixel goes "
            "transparent. Average resampling only."
        )
        size_field(self.nodata_pct_spin, NARROW_FIELD_WIDTH)

        nodata_row = QWidget()
        nodata_layout = QHBoxLayout(nodata_row)
        nodata_layout.setContentsMargins(0, 0, 0, 0)
        nodata_layout.setSpacing(6)
        nodata_layout.addWidget(self.src_nodata_edit, stretch=1)
        nodata_layout.addWidget(self.nodata_pct_spin)
        nodata_row.setFixedWidth(FIELD_WIDTH)

        self.settings_form.addRow(field_label("Source nodata:"), nodata_row)

        # 9. Parallel processes --------------------------------------------
        cpu_count = os.cpu_count() or 1
        self.processes_spin = QSpinBox()
        self.processes_spin.setRange(1, max(1, min(64, cpu_count * 2)))
        self.processes_spin.setValue(1)
        self.processes_spin.setToolTip(
            "--processes\nParallel worker processes. 1 means the flag is not "
            f"passed at all.\nThis machine reports {cpu_count} logical cores."
        )
        size_field(self.processes_spin, NARROW_FIELD_WIDTH)
        self.settings_form.addRow(field_label("Processes:"), self.processes_spin)

        body.addLayout(self.settings_form)
        body.addSpacing(10)

        # --- Advanced -----------------------------------------------------
        self.advanced_panel = AdvancedPanel()
        self.advanced_section = CollapsibleSection("Advanced")

        advanced_layout = QVBoxLayout()
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(self.advanced_panel)
        self.advanced_section.set_content_layout(advanced_layout)

        body.addWidget(self.advanced_section)
        body.addStretch(1)

        return scroll

    # -- bottom: progress bar and buttons --------------------------------

    def _build_footer(self) -> QVBoxLayout:
        footer = QVBoxLayout()
        footer.setSpacing(8)

        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setFixedHeight(26)
        footer.addWidget(self.progress_bar)

        timing = QHBoxLayout()
        timing.setSpacing(12)

        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("timingLabel")
        timing.addWidget(self.elapsed_label)

        timing.addStretch(1)

        self.remaining_label = QLabel("")
        self.remaining_label.setObjectName("timingLabel")
        timing.addWidget(self.remaining_label)

        footer.addLayout(timing)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        buttons.addStretch(1)

        self.start_button = QPushButton("Start Tiling")
        self.start_button.setObjectName("startButton")
        self.start_button.setMinimumSize(130, 36)
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setDefault(True)
        buttons.addWidget(self.start_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setMinimumSize(110, 36)
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)

        buttons.addStretch(1)
        footer.addLayout(buttons)

        return footer

    # -- small builders --------------------------------------------------

    def _small_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("pathLabel")
        return label

    def _path_row(self, parent_layout, placeholder: str):
        """A line edit plus a Browse... button."""
        row = QHBoxLayout()
        row.setSpacing(6)

        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMinimumWidth(100)
        edit.setFixedHeight(30)
        row.addWidget(edit, stretch=1)

        button = QPushButton("...")
        button.setObjectName("browseButton")
        button.setFixedSize(42, 30)
        button.setToolTip("Browse")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(button)

        parent_layout.addLayout(row)
        return edit, button

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self._clock.timeout.connect(self._on_tick)

        self.input_browse.clicked.connect(self._browse_input)
        self.output_browse.clicked.connect(self._browse_output)

        self.input_edit.textChanged.connect(self._on_input_changed)
        self.output_edit.textChanged.connect(self._update_start_state)
        # textEdited only fires on typing, not on setText(), so the
        # auto-filled value does not count as a manual edit.
        self.output_edit.textEdited.connect(self._on_output_edited)

        self.zoom_min_spin.valueChanged.connect(self._sync_zoom_bounds)
        self.zoom_max_spin.valueChanged.connect(self._sync_zoom_bounds)

        self.driver_combo.currentTextChanged.connect(
            self._update_driver_options
        )
        self.webp_lossless_check.toggled.connect(self._update_driver_options)

        self.start_button.clicked.connect(self._on_start)
        self.cancel_button.clicked.connect(self._on_cancel)

        # Anything that moves the estimate re-runs it.
        for spin in (
            self.zoom_min_spin,
            self.zoom_max_spin,
            self.quality_spin,
            self.nodata_pct_spin,
            self.processes_spin,
        ):
            spin.valueChanged.connect(self._update_estimate)
        for combo in (
            self.driver_combo,
            self.tile_size_combo,
            self.resampling_combo,
            self.srs_combo,
        ):
            combo.currentTextChanged.connect(self._update_estimate)
        self.webp_lossless_check.toggled.connect(self._update_estimate)
        self.src_nodata_edit.textChanged.connect(self._update_estimate)
        self.advanced_panel.changed.connect(self._update_estimate)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse_input(self):
        start_at = self.input_edit.text().strip() or str(
            self._store.value("paths/last_input_dir", "") or ""
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Select input raster", start_at, RASTER_FILTER
        )
        if path:
            self.input_edit.setText(path)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select output folder", self.output_edit.text()
        )
        if path:
            self._output_edited_by_user = True
            self.output_edit.setText(path)

    def _on_output_edited(self, text: str):
        """Typing in the output box stops it tracking the input file.

        Clearing the box hands control back, so the next input choice
        auto-fills again.
        """
        self._output_edited_by_user = bool(text.strip())

    def _default_output_dir(self, input_path: str) -> str:
        """Sibling 'tiles' folder next to the chosen raster."""
        return str(Path(input_path).expanduser().resolve().parent / "tiles")

    def _autofill_output(self, input_path: str):
        if self._output_edited_by_user:
            return
        try:
            suggested = self._default_output_dir(input_path)
        except (OSError, ValueError):
            return
        # setText, not setText via textEdited, so the flag stays False.
        self.output_edit.setText(suggested)

    def _on_input_changed(self, path: str):
        self._update_start_state()

        path = path.strip()
        if not path:
            self._info = None
            self._pending_path = None
            self.properties.clear()
            self.set_status("Ready.")
            return

        # textChanged fires on every keystroke, so do nothing expensive
        # until the path actually points at a file.
        if not Path(path).is_file():
            self._info = None
            self._pending_path = None
            self.properties.clear()
            return

        self._autofill_output(path)
        self._load_metadata(path)

    def _load_metadata(self, input_path: str):
        """Ask the background thread for this file's header."""
        self._pending_path = input_path
        self.properties.show_loading()
        self.set_status("Reading image properties...")
        self.metadata_requested.emit(input_path)

    def _on_metadata_loaded(self, path: str, info):
        # A newer file may have been picked while this one was loading.
        if path != self._pending_path:
            return

        self._info = info
        self.properties.show_info(info)
        self._apply_detected_srs(info)
        self._update_estimate()

    def _on_metadata_failed(self, path: str, detail: str):
        if path != self._pending_path:
            return

        self._info = None
        self.properties.show_error(detail)
        self.set_status(detail.splitlines()[0])
        self._update_estimate()

    def _apply_detected_srs(self, info):
        """Put the raster's own CRS into the Source SRS box."""
        detected = info.srs_code or ""
        if not detected:
            if info.srs_name:
                # Georeferenced, but with no EPSG code we can hand to
                # gdal2tiles. Leave the box blank so GDAL uses the embedded
                # WKT rather than a guess.
                self.srs_combo.setCurrentText("")
                self.srs_combo.lineEdit().setPlaceholderText(
                    f"from file: {info.srs_name}"
                )
            else:
                self.srs_combo.setCurrentText("")
                self.srs_combo.lineEdit().setPlaceholderText("none in file")
            return

        if self.srs_combo.findText(detected) < 0:
            self.srs_combo.insertItem(1, detected)

        self.srs_combo.setCurrentText(detected)
        self.srs_combo.setToolTip(
            f"-s / --s_srs\nDetected in the file: {detected}"
            + (f"\n{info.srs_name}" if info.srs_name else "")
        )

    def _sync_zoom_bounds(self):
        """Keep zoom max at or above zoom min."""
        if self.zoom_max_spin.value() < self.zoom_min_spin.value():
            if self.sender() is self.zoom_min_spin:
                self.zoom_max_spin.setValue(self.zoom_min_spin.value())
            else:
                self.zoom_min_spin.setValue(self.zoom_max_spin.value())

    def _update_driver_options(self):
        """Only show quality controls belonging to the chosen driver."""
        driver = self.driver_combo.currentText()

        is_webp = driver == "WEBP"
        has_quality = driver in ("WEBP", "JPEG")

        self.settings_form.setRowVisible(self.quality_spin, has_quality)
        self.settings_form.setRowVisible(self.webp_lossless_check, is_webp)

        # Quality is meaningless in lossless mode.
        self.quality_spin.setEnabled(
            has_quality
            and not (is_webp and self.webp_lossless_check.isChecked())
        )

    def _update_start_state(self):
        ready = bool(self.input_edit.text().strip()) and bool(
            self.output_edit.text().strip()
        )
        self.start_button.setEnabled(ready)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persisted_widgets(self) -> dict:
        """Everything worth remembering between sessions.

        Input and output paths are deliberately left out -- they belong to
        one job, and the output box already derives itself from the input.
        """
        advanced = self.advanced_panel
        return {
            "zoom_min": self.zoom_min_spin,
            "zoom_max": self.zoom_max_spin,
            "tiledriver": self.driver_combo,
            "quality": self.quality_spin,
            "webp_lossless": self.webp_lossless_check,
            "tile_size": self.tile_size_combo,
            "resampling": self.resampling_combo,
            "s_srs": self.srs_combo,
            "src_nodata": self.src_nodata_edit,
            "nodata_pct": self.nodata_pct_spin,
            "processes": self.processes_spin,
            "profile": advanced.profile_combo,
            "xyz": advanced.xyz_check,
            "tms_compatible": advanced.tms_compatible_check,
            "resume": advanced.resume_check,
            "exclude_transparent": advanced.exclude_check,
            "excluded_values": advanced.excluded_values_edit,
            "excluded_pct": advanced.excluded_pct_spin,
            "webviewer": advanced.webviewer_combo,
            "title": advanced.title_edit,
            "copyright": advanced.copyright_edit,
            "url": advanced.url_edit,
            "google_key": advanced.google_key_edit,
            "bing_key": advanced.bing_key_edit,
            "mapml_template": advanced.mapml_template_edit,
            "kml": advanced.kml_combo,
            "verbose": advanced.verbose_check,
            "quiet": advanced.quiet_check,
        }

    def _restore_settings(self) -> bool:
        """Reload the saved form. Returns whether Advanced was expanded."""
        self._store.restore_widgets(self._persisted_widgets())
        return self._store.restore_window(self)

    def _save_settings(self):
        self._store.save_widgets(self._persisted_widgets())
        self._store.save_window(
            self, self.advanced_section.toggle_button.isChecked()
        )

        last_dir = self.input_edit.text().strip()
        if last_dir:
            self._store.set_value("paths/last_input_dir", str(Path(last_dir).parent))

    # ------------------------------------------------------------------
    # Estimating
    # ------------------------------------------------------------------

    def _update_estimate(self):
        """Recompute tiles / size / time for the current settings."""
        if self._thread is not None:
            return  # a run is in progress; the status line belongs to it

        if self._info is None:
            self.set_status("Select an input raster.")
            self.remaining_label.setText("")
            return

        try:
            prediction = estimate.estimate(self._info, self.current_settings())
        except RasterError as exc:
            self.set_status(str(exc).splitlines()[0])
            return

        if not prediction.total_tiles:
            self.set_status(prediction.note or "Nothing to estimate.")
            self.remaining_label.setText("")
            return

        text = f"Estimate: {prediction.summary()}"
        if prediction.note:
            text += f"  ({prediction.note})"
        self.set_status(text)

        levels = ", ".join(
            f"z{z}: {count:,}" for z, count in sorted(prediction.per_zoom.items())
        )
        self.status_label.setToolTip(
            f"Tiles per zoom level\n{levels}\n\n"
            f"Around {estimate.format_bytes(prediction.bytes_per_tile)} per "
            "tile. Size and time are rough averages - scene content moves "
            "both a long way."
        )
        self.remaining_label.setText("")

    # ------------------------------------------------------------------
    # Running a job
    # ------------------------------------------------------------------

    def _on_start(self):
        if self._thread is not None:
            return  # already running

        settings = self.current_settings()

        self._thread = QThread(self)
        self._worker = TileWorker(settings)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.set_progress)
        self._worker.message.connect(self.set_status)
        self._worker.stats.connect(self._on_job_stats)
        self._worker.succeeded.connect(self._on_job_succeeded)
        self._worker.failed.connect(self._on_job_failed)
        self._worker.cancelled.connect(self._on_job_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)

        self.set_running(True)
        self.set_progress(0)
        self.set_status("Starting gdal2tiles...")

        self._elapsed_seconds = 0
        self._last_eta = None
        self.elapsed_label.setText("Elapsed 00:00")
        self.remaining_label.setText("Remaining --:--")
        self._clock.start(1000)

        self._thread.start()

    def _on_tick(self):
        """Once a second, so elapsed keeps moving between worker updates."""
        self._elapsed_seconds += 1
        self.elapsed_label.setText(
            f"Elapsed {estimate.format_clock(self._elapsed_seconds)}"
        )

        if self._last_eta is not None:
            self._last_eta = max(0.0, self._last_eta - 1)
            self.remaining_label.setText(
                f"Remaining ~{estimate.format_clock(self._last_eta)}"
            )

    def _on_job_stats(self, stats: dict):
        """Authoritative counts from the worker; the tick interpolates."""
        self._elapsed_seconds = int(stats.get("elapsed") or 0)
        self.elapsed_label.setText(
            f"Elapsed {estimate.format_clock(self._elapsed_seconds)}"
        )

        eta = stats.get("eta")
        self._last_eta = eta
        if eta is None:
            self.remaining_label.setText("Remaining --:--")
        else:
            self.remaining_label.setText(
                f"Remaining ~{estimate.format_clock(eta)}"
            )

        done = stats.get("done") or 0
        total = stats.get("total") or 0
        if total:
            self.set_status(
                f"Writing tiles: {done:,} of ~{total:,}"
            )

    def _on_cancel(self):
        if self._worker is None:
            return
        self.cancel_button.setEnabled(False)
        self.set_status("Cancelling...")
        self._worker.cancel()

    def _on_job_succeeded(self, output_dir: str):
        self.set_progress(100)
        self._finish_timing()
        self.set_status(f"Done. Tiles written to {output_dir}")

    def _on_job_failed(self, detail: str):
        self.set_progress(0)
        self._finish_timing()
        self.set_status(detail.splitlines()[0] if detail else "Tiling failed.")
        QMessageBox.critical(self, "Tiling failed", detail)

    def _on_job_cancelled(self):
        self.set_progress(0)
        self._finish_timing()
        self.set_status("Cancelled. Any tiles already written were kept.")

    def _finish_timing(self):
        self._clock.stop()
        self._last_eta = None
        self.remaining_label.setText("")
        self.elapsed_label.setText(
            f"Took {estimate.format_clock(self._elapsed_seconds)}"
        )

    def _cleanup_thread(self):
        """Runs on the GUI thread once the worker thread's loop has ended."""
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        self._clock.stop()
        self.set_running(False)

    def closeEvent(self, event):
        """Save the form, and do not leave gdal2tiles orphaned on exit."""
        if self._thread is not None:
            answer = QMessageBox.question(
                self,
                "Tiling in progress",
                "A tiling job is still running. Cancel it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(8000)

        self._save_settings()

        self._meta_thread.quit()
        self._meta_thread.wait(3000)

        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Public helpers -- the worker will call these later
    # ------------------------------------------------------------------

    def current_settings(self) -> dict:
        settings = {
            "input_path": self.input_edit.text().strip(),
            "output_dir": self.output_edit.text().strip(),
            "zoom_min": self.zoom_min_spin.value(),
            "zoom_max": self.zoom_max_spin.value(),
            "tiledriver": self.driver_combo.currentText(),
            "quality": self.quality_spin.value(),
            "webp_lossless": self.webp_lossless_check.isChecked(),
            "tile_size": int(self.tile_size_combo.currentText()),
            "resampling": self.resampling_combo.currentText(),
            "s_srs": self.srs_combo.currentText().strip(),
            "src_nodata": self.src_nodata_edit.text().strip(),
            "nodata_pct": self.nodata_pct_spin.value(),
            "processes": self.processes_spin.value(),
        }
        settings.update(self.advanced_panel.values())
        return settings

    def gdal2tiles_args(self) -> list[str]:
        """Full gdal2tiles argument list, input and output last.

        Delegates to core.tiler so the GUI and a headless run build exactly
        the same command.
        """
        settings = self.current_settings()
        return tiler.build_arguments(settings) + [
            settings["input_path"],
            settings["output_dir"],
        ]

    def set_status(self, message: str):
        self.status_label.setText(message)

    def set_progress(self, percent: int):
        self.progress_bar.setValue(max(0, min(100, int(percent))))

    def set_running(self, running: bool):
        """Lock the inputs while a job is in flight."""
        for widget in (
            self.input_edit,
            self.input_browse,
            self.output_edit,
            self.output_browse,
            self.zoom_min_spin,
            self.zoom_max_spin,
            self.driver_combo,
            self.quality_spin,
            self.webp_lossless_check,
            self.tile_size_combo,
            self.resampling_combo,
            self.srs_combo,
            self.src_nodata_edit,
            self.nodata_pct_spin,
            self.advanced_panel,
        ):
            widget.setEnabled(not running)

        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

        if not running:
            self._update_start_state()
            self._update_driver_options()
