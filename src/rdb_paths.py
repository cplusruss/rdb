import os
import shutil
import sys

from PySide6.QtCore import QSettings

FROZEN = getattr(sys, "frozen", False)
INI_NAME = "rdb.ini"


def exe_path():
    # onefile: __file__ points into the extraction folder; sys.executable is the exe itself
    return os.path.abspath(sys.executable)


def app_dir():
    if FROZEN:
        return os.path.dirname(exe_path())
    return os.path.dirname(os.path.abspath(__file__))


def resolve(path):
    # a relative setting (the shipped defaults) lives beside the exe; an absolute one is the user's
    return path if os.path.isabs(path) else os.path.join(app_dir(), path)


def default_file():
    # a fresh install with no last_file: beside the exe, like the ini, so it always exists
    return os.path.join(app_dir(), "rdb.rdb")


def legacy_ini():
    return os.path.join(os.environ["APPDATA"], "rdb", INI_NAME)


def _redirected_ini():
    # the tests isolate settings with QSettings.setPath; that moves Qt's user-scope ini off %APPDATA%
    qt = os.path.normpath(
        QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "rdb", "rdb"
        ).fileName()
    )
    if os.path.normcase(qt) == os.path.normcase(os.path.normpath(legacy_ini())):
        return None
    return qt


def ini_path():
    return _redirected_ini() or os.path.join(app_dir(), INI_NAME)


def migrate(old, new):
    if os.path.exists(new) or not os.path.isfile(old):
        return False
    shutil.copyfile(old, new)
    return True


def settings():
    path = ini_path()
    if _redirected_ini() is None:
        migrate(legacy_ini(), path)
    return QSettings(path, QSettings.Format.IniFormat)


def ensure_ini():
    s = settings()
    if os.path.exists(s.fileName()):
        return
    s.setValue("editor_zoom", 1.0)
    s.sync()
    if s.status() != QSettings.Status.NoError or not os.path.exists(s.fileName()):
        raise OSError(f"cannot create {s.fileName()}")
