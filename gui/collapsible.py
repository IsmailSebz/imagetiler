"""A simple collapsible section, used for the Advanced Settings panel."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleSection(QWidget):
    """Header button that shows/hides a content area beneath it."""

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)

        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("collapsibleToggle")
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.toggle_button.setSizePolicy(
            self.toggle_button.sizePolicy().horizontalPolicy(),
            self.toggle_button.sizePolicy().verticalPolicy(),
        )

        self.content = QWidget()
        self.content.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.toggled.connect(self._on_toggled)

    def set_content_layout(self, content_layout: QLayout):
        old = self.content.layout()
        if old is not None:
            QWidget().setLayout(old)  # reparent the old layout away
        self.content.setLayout(content_layout)

    def _on_toggled(self, checked: bool):
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(checked)
