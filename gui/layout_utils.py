"""Shared layout constants and small builders.

Both the Tile Settings form and the Advanced panel import from here so the
two line up on the same grid.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QSizePolicy,
)

# Every input control is this wide, in both panels.
FIELD_WIDTH = 250       #200

# Zoom min/max, quality, percentages -- things that share a row.
NARROW_FIELD_WIDTH = 110    #74

# Keeps the label column aligned across both panels.
LABEL_WIDTH = 138 #108


def make_form() -> QFormLayout:
    form = QFormLayout()
    form.setContentsMargins(0, 2, 0, 2)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    form.setLabelAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    form.setFieldGrowthPolicy(
        QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
    )
    return form


def size_field(widget, width: int = FIELD_WIDTH):
    """Pin a control to the shared field width."""
    widget.setFixedWidth(width)
    widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return widget


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    label.setMinimumWidth(LABEL_WIDTH)
    return label


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def sub_section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("subSectionLabel")
    return label


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line
