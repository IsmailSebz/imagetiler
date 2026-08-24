"""Raster properties readout.

Replaces the old image preview: decoding a full orthomosaic just to draw a
thumbnail was costing seconds and a lot of memory, while the metadata below
is free -- GDAL reads it from the header without touching a single pixel.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from core.estimate import format_bytes, format_metres
from core.raster import RasterInfo

EMPTY = "—"  # em dash


class PropertiesPanel(QFrame):
    """Compact key/value view of the selected raster's header."""

    ROWS = (
        ("dimensions", "Size"),
        ("ground", "Ground"),
        ("gsd", "Pixel"),
        ("bands", "Bands"),
        ("crs", "CRS"),
        ("filesize", "File"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("propertiesPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(190)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        heading = QLabel("Image properties")
        heading.setObjectName("propertiesHeading")
        outer.addWidget(heading)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._values: dict[str, QLabel] = {}
        for key, caption in self.ROWS:
            name = QLabel(caption)
            name.setObjectName("propertyName")

            value = QLabel(EMPTY)
            value.setObjectName("propertyValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

            self._values[key] = value
            form.addRow(name, value)

        outer.addLayout(form)
        outer.addStretch(1)

        self.clear()

    # ------------------------------------------------------------------

    def clear(self):
        for value in self._values.values():
            value.setText(EMPTY)
        self._values["dimensions"].setText("No image loaded")
        self.setToolTip("")

    def show_loading(self):
        for value in self._values.values():
            value.setText(EMPTY)
        self._values["dimensions"].setText("Reading...")
        self.setToolTip("")

    def show_error(self, message: str):
        self.clear()
        self._values["dimensions"].setText("Could not read")
        self.setToolTip(message)

    def show_info(self, info: RasterInfo):
        self._values["dimensions"].setText(
            f"{info.width:,} x {info.height:,} px"
        )

        if info.ground_size_m:
            width_m, height_m = info.ground_size_m
            self._values["ground"].setText(
                f"{format_metres(width_m)} x {format_metres(height_m)}"
            )
        else:
            self._values["ground"].setText(EMPTY)

        if info.pixel_size_m:
            gsd_x, gsd_y = info.pixel_size_m
            # Square pixels are the norm; only spell out both when they differ.
            if abs(gsd_x - gsd_y) < max(gsd_x, gsd_y) * 0.01:
                self._values["gsd"].setText(f"{format_metres(gsd_x)}/px")
            else:
                self._values["gsd"].setText(
                    f"{format_metres(gsd_x)} x {format_metres(gsd_y)}/px"
                )
        else:
            self._values["gsd"].setText(EMPTY)

        self._values["bands"].setText(f"{info.band_count} x {info.data_type}")

        crs = info.srs_code or info.srs_name or "none"
        self._values["crs"].setText(crs)

        self._values["filesize"].setText(
            f"{format_bytes(info.file_size)} ({info.driver})"
        )

        tooltip = [info.summary()]
        if info.srs_name and info.srs_code:
            tooltip.append(info.srs_name)
        if info.bounds:
            minx, miny, maxx, maxy = info.bounds
            tooltip.append(
                f"Bounds: {minx:.4f}, {miny:.4f} -> {maxx:.4f}, {maxy:.4f}"
            )
        if info.nodata is not None:
            tooltip.append(f"NoData: {info.nodata:g}")
        self.setToolTip("\n".join(tooltip))
