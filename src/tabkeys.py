from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QDialog, QTabWidget, QWidget

# numpad + / - step a dialog's tabs from anywhere in it, fields included
STEP = {Qt.Key.Key_Plus: 1, Qt.Key.Key_Minus: -1}


def _tabs(widget):
    w = widget
    while w is not None:
        if isinstance(w, QTabWidget):
            return w
        w = w.parentWidget()
    shown = [t for t in widget.window().findChildren(QTabWidget) if t.isVisible()]
    return shown[0] if shown else None


class TabKeys(QObject):
    def eventFilter(self, obj, e):
        if (
            e.type() == QEvent.Type.KeyPress
            and e.key() in STEP
            and e.modifiers() == Qt.KeyboardModifier.KeypadModifier
            and isinstance(obj, QWidget)
            and isinstance(obj.window(), QDialog)
        ):
            tabs = _tabs(obj)
            if tabs is not None and tabs.count():
                tabs.setCurrentIndex(
                    (tabs.currentIndex() + STEP[e.key()]) % tabs.count()
                )
                # focus Qt moved onto the new page leaves it: no control there
                # takes the next + or -
                f = QApplication.focusWidget()
                bar = tabs.tabBar()
                if f is not None and f is not bar and tabs.isAncestorOf(f):
                    if bar.isVisible():
                        bar.setFocus()
                    else:
                        f.clearFocus()
                return True
        return False


def install(app):
    app._tab_keys = TabKeys(app)
    app.installEventFilter(app._tab_keys)
