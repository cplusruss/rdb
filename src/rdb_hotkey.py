import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_WIN = 0x8
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_QT_MODS = (
    (Qt.KeyboardModifier.ControlModifier, MOD_CONTROL),
    (Qt.KeyboardModifier.AltModifier, MOD_ALT),
    (Qt.KeyboardModifier.ShiftModifier, MOD_SHIFT),
    (Qt.KeyboardModifier.MetaModifier, MOD_WIN),
)
_K = Qt.Key
# US layout virtual-key codes for the non-alphanumeric keys
_VK = {
    _K.Key_Space: 0x20,
    _K.Key_Return: 0x0D,
    _K.Key_Enter: 0x0D,
    _K.Key_Escape: 0x1B,
    _K.Key_Tab: 0x09,
    _K.Key_Backspace: 0x08,
    _K.Key_Insert: 0x2D,
    _K.Key_Delete: 0x2E,
    _K.Key_Home: 0x24,
    _K.Key_End: 0x23,
    _K.Key_PageUp: 0x21,
    _K.Key_PageDown: 0x22,
    _K.Key_Left: 0x25,
    _K.Key_Up: 0x26,
    _K.Key_Right: 0x27,
    _K.Key_Down: 0x28,
    _K.Key_Semicolon: 0xBA,
    _K.Key_Equal: 0xBB,
    _K.Key_Comma: 0xBC,
    _K.Key_Minus: 0xBD,
    _K.Key_Period: 0xBE,
    _K.Key_Slash: 0xBF,
    _K.Key_QuoteLeft: 0xC0,
    _K.Key_BracketLeft: 0xDB,
    _K.Key_Backslash: 0xDC,
    _K.Key_BracketRight: 0xDD,
    _K.Key_Apostrophe: 0xDE,
}

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = (
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
)
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_user32.GetWindowTextW.restype = ctypes.c_int


def parse_key(text):
    seq = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if seq.count() != 1:
        raise ValueError(f"hotkey {text!r} is not one key combination")
    combo = seq[0]
    qmods = combo.keyboardModifiers()
    key = combo.key()
    if key in _VK:
        vk = _VK[key]
    elif _K.Key_F1 <= key <= _K.Key_F24:
        vk = 0x70 + key.value - _K.Key_F1.value
    elif _K.Key_A <= key <= _K.Key_Z or _K.Key_0 <= key <= _K.Key_9:
        vk = key.value
    else:
        raise ValueError(f"hotkey {text!r} has no virtual key")
    if Qt.KeyboardModifier.KeypadModifier in qmods:
        raise ValueError(f"hotkey {text!r} is a keypad key")
    mods = 0
    for qm, bit in _QT_MODS:
        if qm in qmods:
            mods |= bit
    # a bare letter or digit would be swallowed system-wide
    function = _K.Key_F1 <= key <= _K.Key_F24
    if not function and not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise ValueError(f"hotkey {text!r} needs Ctrl, Alt or Win")
    return mods, vk


def register(hwnd, mods, vk):
    if _user32.RegisterHotKey(hwnd, HOTKEY_ID, mods, vk):
        return True
    err = ctypes.get_last_error()
    if err == ERROR_HOTKEY_ALREADY_REGISTERED:
        return False
    raise ctypes.WinError(err)


def unregister(hwnd):
    if not _user32.UnregisterHotKey(hwnd, HOTKEY_ID):
        raise ctypes.WinError(ctypes.get_last_error())


def foreground_title():
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class DumpKey(QAbstractNativeEventFilter):
    def __init__(self, win, fire, taken):
        super().__init__()
        self.win = win
        self.fire = fire
        self.taken = taken
        self.want = None
        self.hwnd = 0
        QApplication.instance().installNativeEventFilter(self)

    def set_key(self, text):
        if text == self.want:
            return
        mods, vk = parse_key(text) if text else (0, 0)
        self.want = text
        self._drop()
        if not text:
            return
        hwnd = int(self.win.winId())
        if register(hwnd, mods | MOD_NOREPEAT, vk):
            self.hwnd = hwnd
        else:
            self.taken(text)

    def release(self):
        self._drop()
        QApplication.instance().removeNativeEventFilter(self)

    def _drop(self):
        if self.hwnd:
            unregister(self.hwnd)
            self.hwnd = 0

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG" and self.hwnd:
            msg = wintypes.MSG.from_address(int(message))
            if (
                msg.message == WM_HOTKEY
                and msg.wParam == HOTKEY_ID
                and msg.hWnd == self.hwnd
            ):
                # the title is read now, before the dump box takes focus
                title = foreground_title()
                QTimer.singleShot(0, lambda: self.fire(title))
                return True, 0
        return False, 0
