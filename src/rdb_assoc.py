import ctypes
import os
import winreg

import rdb_paths

# per user (no admin): HKCU\Software\Classes
CLASSES = "Software\\Classes\\"
PROGID = "rdb.file"
HERE = rdb_paths.app_dir()
if rdb_paths.FROZEN:
    # the single-file exe is its own launcher and carries its icon
    LAUNCHER = rdb_paths.exe_path()
    ICON = f'"{LAUNCHER}",0'
else:
    LAUNCHER = os.path.join(HERE, "rdb.exe")
    ICON = os.path.join(HERE, "rdb.ico")
# swapped in tests, so they never touch the real registry or the shell
REG = winreg
SHCNE_ASSOCCHANGED = 0x08000000


def _notify():
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)


NOTIFY = _notify


def command():
    return f'"{LAUNCHER}" "%1"'


def _read(path):
    try:
        return REG.QueryValue(REG.HKEY_CURRENT_USER, CLASSES + path)
    except FileNotFoundError:
        return None


def _write(path, value):
    key = REG.CreateKey(REG.HKEY_CURRENT_USER, CLASSES + path)
    REG.SetValueEx(key, "", 0, REG.REG_SZ, value)
    REG.CloseKey(key)


def status():
    return (
        _read(".rdb") == PROGID
        and _read(PROGID + "\\shell\\open\\command") == command()
    )


def associate():
    if not os.path.isfile(LAUNCHER):
        raise FileNotFoundError(f"{LAUNCHER} not found")
    _write(PROGID, "RDB File")
    _write(PROGID + "\\DefaultIcon", ICON)
    _write(PROGID + "\\shell\\open\\command", command())
    _write(".rdb", PROGID)
    NOTIFY()


def remove():
    # deepest keys first: a key with subkeys cannot be deleted
    paths = [
        PROGID + "\\shell\\open\\command",
        PROGID + "\\shell\\open",
        PROGID + "\\shell",
        PROGID + "\\DefaultIcon",
        PROGID,
    ]
    if _read(".rdb") == PROGID:
        paths.append(".rdb")
    for path in paths:
        try:
            REG.DeleteKey(REG.HKEY_CURRENT_USER, CLASSES + path)
        except FileNotFoundError:
            pass
    NOTIFY()
