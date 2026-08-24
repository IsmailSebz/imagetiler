"""Persisting the form between sessions.

QSettings on Windows is backed by the registry, so this lands under
HKEY_CURRENT_USER\\Software\\ImageTiler\\RasterImageTiler. The same code
falls back to an ini file on Linux/macOS, which keeps the app portable.

Organisation and application names are passed explicitly rather than taken
from QApplication, so persistence works no matter how the app is launched.
"""

from PyQt6.QtCore import QByteArray, QSettings
from PyQt6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox

ORGANISATION = "ImageTiler"
APPLICATION = "RasterImageTiler"


class SettingsStore:

    def __init__(self):
        self._settings = QSettings(ORGANISATION, APPLICATION)

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------

    def save_widgets(self, mapping: dict):
        """`mapping` is {settings key: widget}."""
        for key, widget in mapping.items():
            value = _widget_value(widget)
            if value is not None:
                self._settings.setValue(f"form/{key}", value)

    def restore_widgets(self, mapping: dict):
        for key, widget in mapping.items():
            stored = self._settings.value(f"form/{key}", None)
            if stored is not None:
                _apply_widget_value(widget, stored)

    # ------------------------------------------------------------------
    # Window
    # ------------------------------------------------------------------

    def save_window(self, window, advanced_expanded: bool):
        self._settings.setValue("window/geometry", window.saveGeometry())
        self._settings.setValue("window/advanced", advanced_expanded)

    def restore_window(self, window) -> bool:
        geometry = self._settings.value("window/geometry", None)
        if isinstance(geometry, (QByteArray, bytes, bytearray)):
            # A corrupt or foreign value would otherwise raise here, and a
            # bad saved geometry should never stop the app from opening.
            try:
                window.restoreGeometry(QByteArray(geometry))
            except (TypeError, ValueError):
                pass
        return _to_bool(self._settings.value("window/advanced", False))

    # ------------------------------------------------------------------
    # Simple values
    # ------------------------------------------------------------------

    def set_value(self, key: str, value):
        self._settings.setValue(key, value)

    def value(self, key: str, default=None):
        return self._settings.value(key, default)

    def clear(self):
        self._settings.clear()

    @property
    def location(self) -> str:
        """Where the settings actually live, for the About/tooltip text."""
        return self._settings.fileName()


# ----------------------------------------------------------------------
# Widget type dispatch
# ----------------------------------------------------------------------


def _widget_value(widget):
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def _apply_widget_value(widget, stored):
    # QSettings hands back strings from the registry, so every branch has to
    # coerce rather than assume the original type survived the round trip.
    if isinstance(widget, QCheckBox):
        widget.setChecked(_to_bool(stored))

    elif isinstance(widget, QSpinBox):
        try:
            widget.setValue(int(stored))
        except (TypeError, ValueError):
            pass

    elif isinstance(widget, QComboBox):
        text = str(stored)
        if widget.isEditable() or widget.findText(text) >= 0:
            widget.setCurrentText(text)

    elif isinstance(widget, QLineEdit):
        widget.setText(str(stored))


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")
