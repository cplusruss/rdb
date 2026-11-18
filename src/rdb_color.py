import json

import colorpopup
import rdb_paths as paths
import rdb_theme

PREFIX = "colorpopup/"


def _get(key, default):
    raw = paths.settings().value(PREFIX + key)
    return default if raw in (None, "") else json.loads(raw)


def _set(key, value):
    paths.settings().setValue(PREFIX + key, json.dumps(value))


def _style(popup):
    rdb_theme.follow(popup, rdb_theme.popup_frame)


colorpopup.configure(_get, _set, _style, rdb_theme.shade, rdb_theme.unshade)
get_color = colorpopup.get_color
