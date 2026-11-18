import copy
import datetime
import json
import re
import secrets
from collections import namedtuple
from pathlib import Path, PureWindowsPath

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextListFormat,
)

import rdb_hotkey
import rdb_status
import rdb_theme

_U = QTextFormat.Property.UserProperty
PROPS = {
    "c": _U + 1,
    "d": _U + 2,
    "u": _U + 3,
    "fg": _U + 4,
    "bg": _U + 5,
    "k": _U + 6,
    "h": _U + 7,
    "i": _U + 8,
    "a": _U + 9,
    "no": _U + 10,
    "nt": _U + 11,
    "p": _U + 12,
    "hs": _U + 13,
    "ts": _U + 15,
    "tn": _U + 16,
    "tp": _U + 17,
    "ti": _U + 18,
    "ta": _U + 19,
    "tb": _U + 20,
    "ud": _U + 21,
    "hc": _U + 22,
    "ho": _U + 23,
    "hf": _U + 24,
    "is": _U + 25,
    "in": _U + 26,
    "st": _U + 27,
}
# display-only caption above the first Completed item of a home category; not meta, not stored
CAPTION = _U + 14
# a dark document draws each explicit character colour mirrored; these keep the colour as written
ORIG_FG, ORIG_BG = _U + 30, _U + 31
CAPTION_PAD = 16
# one marker per todo: priorities, then the superstars
PRIOS = ("p1", "p2", "p3", "p4")
STARS = ("ys", "os", "rb", "bi", "gc", "pq")
MARKS = ("", *PRIOS, *STARS)
# anchor of an item whose home is the top of the list; "" means no home recorded
TOP = "^"
# a block that starts a new item carries no id, no home and no note of its own
NO_HOME = {
    "i": "",
    "a": "",
    "no": False,
    "nt": "",
    "p": "",
    "hs": 0,
    "ud": "",
    "st": "",
}
# a timeline task's bar, id and links; a block that starts a new item carries none of them
NO_TASK = {"ts": "", "tn": 0, "tp": 0, "ti": "", "ta": "", "tb": ""}
# image names and sizes belong to the block that holds the images
NO_IMAGES = {"is": "", "in": ""}
NEW_ITEM = {**NO_HOME, **NO_TASK, **NO_IMAGES, "hc": "", "ho": "", "hf": ""}
DONE_MODES = {"place": "stay", "section": "move"}
# a note page never gathers checked lines under Completed
NOTE_MODE = "stay"
INK = QColor(30, 30, 30)
TODO, SECTION, HEADING, DIVIDER = "todo", "section", "heading", "divider"
# the inline note blocks of the old format; only the migration reads them
LEGACY_NOTE = "note"
# the gutter is block indent, not margin: the layout scales indents with the zoom, margins it leaves alone
INDENT_W = 22
# item row part widths at 100%: the gutter is the left pad, the parts in use with 3 px between,
# and the right pad before the text. Its whole indent steps are block indent; the rest is a
# display-only margin of the root frame (Editor._margin)
GUTTER_ITEM_KEYS = ("number", "check", "bubble", "note", "mark", "status")
GUTTER_PART_W = {
    "number": 24,
    "check": 13,
    "bubble": 10,
    "note": 9,
    "mark": 10,
    "status": 10,
}
GUTTER_LPAD, GUTTER_RPAD = 4, 6


def parts_width(items):
    return sum(GUTTER_PART_W[k] for k in items) + 3 * max(len(items) - 1, 0)


def heads_width(heads):
    # right edge of the heading row parts at 100% (Editor _head_columns): the first at 8, the fold
    # box 13 wide, the count 32 wide when last
    x = right = 8
    for i, k in enumerate(heads):
        if k == "fold":
            right, x = x + 13, x + 18
        elif k == "swatch":
            right, x = x + 10, x + 16
        else:
            right, x = x + (32 if i == len(heads) - 1 else 20), x + 25
    return right


def gutter_px(items, lpad=None, rpad=None, heads=()):
    lpad = GUTTER_LPAD if lpad is None else lpad
    rpad = GUTTER_RPAD if rpad is None else rpad
    return max(INDENT_W, lpad + parts_width(items) + rpad, heads_width(heads) + rpad)


GUTTER = gutter_px(GUTTER_ITEM_KEYS)
GUTTER_STEPS = GUTTER // INDENT_W
NOTE_PAD = 2
PREVIEW_LINES = 8
NOTE_PID = re.compile(r"n_[0-9a-f]{6}")
# first lines of each open preview, pid -> (lines, more); filled from the store when a list
# opens or a note page saves, and read by layout_block and the editor's painter
PREVIEWS = {}
LINE_HEIGHT = 125
EDITOR_FONT = QFont("Tahoma", 9)
NOTE_FAMILY = "Consolas"
WEIGHTS = {
    "Thin": QFont.Weight.Thin,
    "ExtraLight": QFont.Weight.ExtraLight,
    "Light": QFont.Weight.Light,
    "Normal": QFont.Weight.Normal,
    "Medium": QFont.Weight.Medium,
    "DemiBold": QFont.Weight.DemiBold,
    "Bold": QFont.Weight.Bold,
    "ExtraBold": QFont.Weight.ExtraBold,
    "Black": QFont.Weight.Black,
}
MODES = ("move", "stay", "hide")
EOD_SLOTS = (*(f"{h}:{mm}" for h in range(12, 20) for mm in ("00", "30")), "20:00")
(
    BOOL,
    INT,
    CHOICE,
    COLOR,
    FONT,
    COLORS,
    PATH,
    KEY,
    STYLE,
    FINDITEMS,
    GUTTERPARTS,
    STATUSES,
) = (
    "bool",
    "int",
    "choice",
    "color",
    "font",
    "colors",
    "path",
    "key",
    "style",
    "finditems",
    "gutterparts",
    "statuses",
)
# the statuses a new file starts with; keys are fixed, order is column order
DEFAULT_STATUSES = [
    {"key": k, "name": n, "icon": i, "color": c, "done": d}
    for k, n, i, c, d in (
        ("design", "In Design", "pencil", "#1a73e8", False),
        ("waiting", "Waiting", "hourglass", "#f29900", False),
        ("ordered", "Ordered", "cart", "#8430ce", False),
        ("shipped", "Shipped", "truck", "#00897b", False),
        ("received", "Received", "box", "#795548", False),
        ("completed", "Completed", "check", "#188038", True),
    )
]
# the to-do list's text levels: (key prefix, default style, default size); the Fonts > List grid
LIST_LEVELS = (
    ("body", "Regular", 9),
    ("h1", "Bold", 12),
    ("h2", "Bold", 11),
    ("h3", "Bold", 8),
)
LEVEL_LABELS = {"body": "Body", "h1": "H1", "h2": "H2", "h3": "H3"}
FIND_SIZES = ("Small", "Medium", "Large")
FIND_DIV = "div"
FIND_KEYS = (*PRIOS, *STARS, "tag")
GUTTER_HEAD_KEYS = ("fold", "swatch", "count")
# (tab, group, key, default, label, kind, *kind args); drives defaults, validation and the dialog
SCHEMA = [
    ("General", "Items", "on_check", "move", "On check", CHOICE, MODES),
    ("General", "Items", "autosave_ms", 400, "Autosave", INT, 100, 5000, " ms"),
    ("General", "Items", "esc_leaves", True, "Esc leaves list", BOOL),
    (
        "General",
        "Items",
        "ctrl_arrows",
        "History",
        "Ctrl+Left/Right",
        CHOICE,
        ("History", "Word"),
    ),
    ("General", "Window", "tooltips", True, "Tooltips", BOOL),
    ("General", "Map", "confirm_delete", True, "Confirm delete", BOOL),
    ("General", "Map", "preview_count", 50, "Card preview", INT, 1, 200, " items"),
    ("General", "Map", "preview_fill", True, "Fill card", BOOL),
    ("General", "Map", "card_numbers", True, "Card numbers", BOOL),
    ("General", "Map", "card_checkboxes", True, "Card checkboxes", BOOL),
    ("General", "Map", "card_headings", False, "Card headings", BOOL),
    ("General", "Window", "scrollbars", True, "Scrollbars", BOOL),
    (
        "General",
        "Title Bar",
        "theme",
        "Classic",
        "Theme",
        CHOICE,
        ("Classic", "Win11", "Win11 Compact"),
    ),
    (
        "General",
        "Title Bar",
        "mode",
        "Light",
        "Mode",
        CHOICE,
        ("Light", "Dark", "System"),
    ),
    ("General", "Title Bar", "tiles_side", "right", "Tiles", CHOICE, ("left", "right")),
    ("General", "Snips", "snip_folder", "snips", "Folder", PATH),
    ("General", "Snips", "snip_max", 1920, "Max", INT, 0, 16384, " px"),
    (
        "General",
        "Snips",
        "snip_format",
        "webp",
        "Format",
        CHOICE,
        ("webp", "png", "jpg"),
    ),
    ("General", "Snips", "snip_quality", 90, "Quality", INT, 1, 100, ""),
    ("General", "Reminders", "urgent_at_once", True, "At once", BOOL),
    ("General", "Reminders", "urgent_eod", "16:30", "End of day", CHOICE, EOD_SLOTS),
    ("General", "Reminders", "urgent_count", True, "Count", BOOL),
    ("General", "Reminders", "notepad_hide_urgent", False, "Hide on Note cards", BOOL),
    ("General", "Dump", "dump_key", "F4", "Key", KEY),
    (
        "General",
        "Links",
        "rdoc_path",
        "rdoc.exe",
        "rdoc",
        PATH,
    ),
    (
        "Editor",
        "Gutter",
        "gutter_items",
        list(GUTTER_ITEM_KEYS),
        "",
        GUTTERPARTS,
        GUTTER_ITEM_KEYS,
    ),
    (
        "Editor",
        "Gutter",
        "gutter_heads",
        list(GUTTER_HEAD_KEYS),
        "",
        GUTTERPARTS,
        GUTTER_HEAD_KEYS,
    ),
    ("Editor", "Gutter", "gutter_lpad", 4, "Left pad", INT, 0, 40, " px"),
    ("Editor", "Gutter", "gutter_rpad", 6, "Right pad", INT, 3, 40, " px"),
    ("Editor", "Gutter", "gutter_sep", False, "Gutter separator", BOOL),
    ("Editor", "Gutter", "gutter_sep_color", "#a0a0a0", "Separator", COLOR),
    ("Editor", "Gutter", "tag_gutter", False, "Tag gutter", BOOL),
    ("Editor", "Gutter", "tag_gutter_w", 60, "Width", INT, 20, 300, " px"),
    ("Editor", "Text", "line_height", 125, "Line height", INT, 100, 250, " %"),
    ("Editor", "Text", "keep_in_heading", False, "Keep items in heading", BOOL),
    ("Editor", "Text", "blank_todo", True, "Allow blank todo", BOOL),
    ("Editor", "Text", "card_zoom", True, "Zoom per card", BOOL),
    ("Editor", "Text", "line_hl", False, "Current line", BOOL),
    ("Editor", "Text", "line_hl_color", "#e8f0fb", "Line color", COLOR),
    ("Editor", "Text", "notepad_wrap", True, "Note wrap", BOOL),
    ("Editor", "Vim", "vim", False, "Vim", BOOL),
    ("Editor", "Vim", "vim_start", "insert", "Start in", CHOICE, ("insert", "normal")),
    ("Editor", "Checked", "strike", True, "Strike", BOOL),
    ("Editor", "Checked", "grey", True, "Grey", BOOL),
    ("Editor", "Checked", "italic", False, "Italic", BOOL),
    ("Editor", "In Completed", "sec_strike", True, "Strike", BOOL),
    ("Editor", "In Completed", "sec_grey", True, "Grey", BOOL),
    ("Editor", "In Completed", "sec_italic", False, "Italic", BOOL),
    ("Editor", "In Completed", "sec_size", -1, "Size", INT, -3, 0, " pt"),
    ("Editor", "In Completed", "sec_rule_px", 3, "Divider", INT, 1, 8, " px"),
    ("Editor", "Headings", "h1_bg", "#dbe8f7", "H1 bg", COLOR),
    ("Editor", "Headings", "h1_border", "#5b7fb5", "H1 border", COLOR),
    ("Editor", "Headings", "h1_sel", "#a9c6ea", "H1 selected", COLOR),
    (
        "Editor",
        "Headings",
        "h1_cursor",
        "Hand",
        "H1 cursor",
        CHOICE,
        ("Hand", "Pointer"),
    ),
    ("Editor", "Headings", "h2_rule", "#282828", "H2 rule", COLOR),
    ("Editor", "Headings", "h2_rule_px", 2, "H2 thick", INT, 1, 8, " px"),
    ("Editor", "Divider", "divider_color", "#282828", "Color", COLOR),
    ("Editor", "Divider", "divider_px", 2, "Thickness", INT, 1, 8, " px"),
    ("Editor", "Note", "note_bg", "#fff8c4", "Background", COLOR),
    ("Editor", "Note", "note_border", "#e0c860", "Border", COLOR),
    (
        "Editor",
        "Links",
        "chip_style",
        "pill",
        "Chips",
        CHOICE,
        ("box", "pill", "token"),
    ),
    ("Editor", "Links", "chip_font", "Tahoma", "Font", FONT),
    ("Editor", "Links", "chip_bold", False, "Bold", BOOL),
    ("Editor", "Links", "chip_pad", 5, "Pad", INT, 0, 20, " px"),
    ("Editor", "Links", "chip_vpad", 1, "V pad", INT, 0, 8, " px"),
    ("Editor", "Links", "chip_size", 8, "Size", INT, 6, 24, " pt"),
    ("Editor", "Links", "chip_weight", "Normal", "Weight", CHOICE, tuple(WEIGHTS)),
    ("Editor", "Links", "chip_icon", 14, "Icon", INT, 8, 32, " px"),
    ("Editor", "Links", "chip_fg", "#1a1a1a", "Text", COLOR),
    ("Editor", "Links", "chip_fill", "#e4e8ee", "Fill", COLOR),
    ("Editor", "Links", "chip_border", "#9aa6b8", "Border", COLOR),
    ("Editor", "Links", "link_color", "#0000ff", "Link", COLOR),
    ("Editor", "Links", "link_visited", "#551a8b", "Visited", COLOR),
    ("Editor", "Links", "link_cursor", "Hand", "Cursor", CHOICE, ("Hand", "Pointer")),
    ("Editor", "Tags", "tag_font", "Tahoma", "Font", FONT),
    ("Editor", "Tags", "tag_bold", False, "Bold", BOOL),
    ("Editor", "Tags", "tag_weight", "Normal", "Weight", CHOICE, tuple(WEIGHTS)),
    ("Editor", "Tags", "tag_size", 8, "Size", INT, 6, 24, " pt"),
    ("Editor", "Tags", "tag_pad", 3, "Pad", INT, 0, 20, " px"),
    ("Editor", "Tags", "tag_fg", "#1a1a1a", "Text", COLOR),
    ("Editor", "Tags", "tag_fill", "#e6efdc", "Fill", COLOR),
    ("Editor", "Tags", "tag_border", "#9ab088", "Border", COLOR),
    ("Editor", "Hover", "hover_accent", "#5b7fb5", "Accent", COLOR),
    ("Editor", "Hover", "hover_glow", True, "Glow", BOOL),
    ("Editor", "Hover", "chip_hover_ms", 400, "Hover delay", INT, 0, 3000, " ms"),
    ("Editor", "Hover", "preview_px", 480, "Preview size", INT, 64, 4000, " px"),
    ("Editor", "Find bar", "find_icon", "Medium", "Icon size", CHOICE, FIND_SIZES),
    ("Editor", "Find bar", "find_items", list(FIND_KEYS), "", FINDITEMS),
    (
        "Editor",
        "Statuses",
        "statuses",
        DEFAULT_STATUSES,
        "",
        STATUSES,
        rdb_status.ICON_KEYS,
    ),
    ("Editor", "Kanban Title", "kanban_title_fill", True, "Status fill", BOOL),
    (
        "Editor",
        "Kanban Title",
        "kanban_title_ink",
        "Auto",
        "Text",
        CHOICE,
        ("Auto", "Fixed"),
    ),
    ("Editor", "Kanban Title", "kanban_title_fg", "#1a1a1a", "Text color", COLOR),
    ("Editor", "Kanban Title", "kanban_title_font", "Tahoma", "Font", FONT),
    ("Editor", "Kanban Title", "kanban_title_size", 9, "Size", INT, 6, 24, " pt"),
    ("Editor", "Kanban Title", "kanban_counts", True, "Counts", BOOL),
    ("Editor", "Kanban Card", "kanban_card_bg", "#ffffff", "Fill", COLOR),
    ("Editor", "Kanban Card", "kanban_card_border", "#282828", "Border", COLOR),
    ("Editor", "Kanban Card", "kanban_radius", 4, "Radius", INT, 0, 16, " px"),
    ("Editor", "Kanban Card", "kanban_shadow", True, "Shadow", BOOL),
    ("Editor", "Kanban Card", "kanban_pad", 6, "Pad", INT, 0, 20, " px"),
    # the range is rdb_kanban's MIN_W..MAX_W
    ("Editor", "Kanban Layout", "kanban_card_w", 220, "Width", INT, 140, 640, " px"),
    ("Editor", "Kanban Layout", "kanban_gap", 8, "Gap", INT, 0, 40, " px"),
    ("Map", "Grid", "grid_show", True, "Grid", BOOL),
    ("Map", "Grid", "grid_style", "lines", "Style", CHOICE, ("lines", "dots")),
    ("Map", "Grid", "snap", True, "Snap", BOOL),
    ("Map", "New card", "card_w", 7, "Width", INT, 4, 30, " cells"),
    ("Map", "New card", "card_h", 4, "Height", INT, 2, 30, " cells"),
    ("Map", "Card", "card_title_rule", True, "Title rule", BOOL),
    ("Map", "Card", "card_footer_rule", True, "Footer rule", BOOL),
    ("Map", "Card", "card_color_scope", "card", "Colour", CHOICE, ("card", "header")),
    ("Map", "Text", "text_size", 14, "Size", INT, 6, 72, " pt"),
    ("Text Copy", "File", "text_copy", False, "Save text copy", BOOL),
    (
        "Text Copy",
        "File",
        "text_format",
        "Text",
        "Format",
        CHOICE,
        ("Text", "Markdown"),
    ),
    ("Text Copy", "Include", "text_checked", False, "Checked items", BOOL),
    ("Text Copy", "Include", "text_subs", True, "Sub-bullets", BOOL),
    ("Text Copy", "Include", "text_notes", True, "Item notes", BOOL),
    ("Text Copy", "Include", "text_notepads", True, "Notepads", BOOL),
    ("Text Copy", "Include", "text_headings", True, "Headings", BOOL),
    ("Text Copy", "Include", "text_contacts", False, "Contacts", BOOL),
    ("Text Copy", "Include", "text_blocks", False, "Map text blocks", BOOL),
    ("Timeline", "Chart", "tl_fields", True, "Fields", BOOL),
    ("Timeline", "Chart", "tl_weekdays", True, "Weekdays", BOOL),
    ("Timeline", "Chart", "tl_day_bg", "#ffffff", "Day", COLOR),
    ("Timeline", "Chart", "tl_weekend_bg", "#e4e4e4", "Weekend", COLOR),
    ("Timeline", "Chart", "tl_today_fill", True, "Today fill", BOOL),
    ("Timeline", "Chart", "tl_today_bg", "#fff176", "Today", COLOR),
    ("Timeline", "Chart", "tl_today_line", False, "Today line", BOOL),
    ("Timeline", "Chart", "tl_grid", True, "Cells", BOOL),
    ("Timeline", "Chart", "tl_grid_color", "#dcdcdc", "Cell lines", COLOR),
    (
        "Timeline",
        "Dates",
        "tl_date_fmt",
        "M/D",
        "Format",
        CHOICE,
        ("M/D", "D/M", "MM-DD", "MMM D", "D MMM", "YYYY-MM-DD"),
    ),
    ("Fonts", "UI", "ui_font", "Tahoma", "Font", FONT),
    ("Fonts", "UI", "ui_size", 8, "Size", INT, 6, 24, " pt"),
    ("Fonts", "UI", "ui_weight", "Normal", "Weight", CHOICE, tuple(WEIGHTS)),
    *(
        row
        for lvl, style, size in LIST_LEVELS
        for row in (
            ("Fonts", "List", f"{lvl}_font", "Tahoma", "Font", FONT),
            ("Fonts", "List", f"{lvl}_style", style, "Style", STYLE),
            ("Fonts", "List", f"{lvl}_size", size, "Size", INT, 6, 48, " pt"),
            ("Fonts", "List", f"{lvl}_fg", "#000000", "Color", COLOR),
        )
    ),
    ("Fonts", "Map", "map_font", "Tahoma", "Font", FONT),
    ("Fonts", "Map", "map_size", 8, "Size", INT, 6, 24, " pt"),
    ("Fonts", "Map", "map_weight", "Normal", "Weight", CHOICE, tuple(WEIGHTS)),
    ("Fonts", "Claude", "claude_font", "Consolas", "Font", FONT),
    ("Fonts", "Claude", "claude_size", 9, "Size", INT, 6, 24, " pt"),
    ("Fonts", "Claude", "claude_weight", "Normal", "Weight", CHOICE, tuple(WEIGHTS)),
    ("Colors", "Editor", "editor_bg", "#ffffff", "Background", COLOR),
    ("Colors", "Editor", "check_color", "#0078d7", "Checkbox", COLOR),
    ("Colors", "Editor", "number_color", "#666666", "Numbers", COLOR),
    ("Colors", "Editor", "checked_color", "#8a8a8a", "Checked text", COLOR),
    ("Colors", "Editor", "sec_color", "#a0a0a0", "Completed text", COLOR),
    ("Colors", "Editor", "sec_rule_color", "#505050", "Completed divider", COLOR),
    ("Colors", "Editor", "urgent_color", "#d42020", "Urgent", COLOR),
    ("Colors", "Map", "map_bg", "#eaeaea", "Background", COLOR),
    ("Colors", "Map", "grid_minor", "#e1e1e1", "Grid", COLOR),
    ("Colors", "Map", "card_bg", "#ffffff", "Card", COLOR),
    ("Colors", "Map", "card_border", "#282828", "Card border", COLOR),
    ("Colors", "Map", "card_select_bg", "#c8e2fc", "Selected", COLOR),
    ("Colors", "Map", "card_select_border", "#0078d7", "Selected border", COLOR),
    ("Colors", "Map", "frame_color", "#5b7fb5", "Frame", COLOR),
    ("Colors", "Map", "card_title_rule_color", "#dddddd", "Title rule", COLOR),
    ("Colors", "Claude", "claude_fg", "#3d3929", "Text", COLOR),
    ("Colors", "Claude", "claude_bg", "#faf9f5", "Background", COLOR),
    ("Colors", "Map", "card_footer_rule_color", "#dddddd", "Footer rule", COLOR),
    (
        "Colors",
        "Bubbles",
        "bubble_bg",
        ["#fff59d", "#f8bbd0", "#c8e6c9", "#b2ebf2", "#ffe0b2", "#ffcdd2", "#e0e0e0"],
        "bg",
        COLORS,
    ),
    (
        "Colors",
        "Bubbles",
        "bubble_bg_vivid",
        ["#ffff00", "#00ff00", "#00ffff", "#ff00ff", "#ff0000", "#0000ff", "#ff9900"],
        "bg vivid",
        COLORS,
    ),
    (
        "Colors",
        "Bubbles",
        "bubble_fg",
        ["#000000", "#808080", "#d42020", "#1565c0", "#2e7d32", "#ef6c00", "#6a1b9a"],
        "fg",
        COLORS,
    ),
    (
        "Colors",
        "Bubbles",
        "bubble_fg_vivid",
        ["#ff0000", "#0000ff", "#00b050", "#ff00ff", "#ff9900", "#00b0f0", "#7030a0"],
        "fg vivid",
        COLORS,
    ),
]
DEFAULT_SETTINGS = {row[2]: row[3] for row in SCHEMA}
# hand-picked dark values for colour settings still at their light default; a colour the user
# changed is mirrored instead
DARK_DEFAULTS = {
    "editor_bg": "#1c1c1c",
    "map_bg": "#202020",
    "grid_minor": "#2a2a2a",
    "card_bg": "#2b2b2b",
    "card_border": "#5a5a5a",
    "kanban_card_bg": "#2b2b2b",
    "kanban_card_border": "#5a5a5a",
    "card_select_bg": "#1f3a5a",
    "card_select_border": "#4cc2ff",
    "card_title_rule_color": "#3a3a3a",
    "card_footer_rule_color": "#3a3a3a",
    "frame_color": "#7aa2e0",
    "check_color": "#4cc2ff",
    "number_color": "#9a9a9a",
    "checked_color": "#8a8a8a",
    "sec_color": "#6e6e6e",
    "sec_rule_color": "#8a8a8a",
    "urgent_color": "#ff6b6b",
    "gutter_sep_color": "#505050",
    "line_hl_color": "#26303c",
    "h1_bg": "#23344d",
    "h1_border": "#7aa2e0",
    "h1_sel": "#2f4f7a",
    "h2_rule": "#c8c8c8",
    "divider_color": "#c8c8c8",
    "note_bg": "#3a3520",
    "note_border": "#8a7a30",
    "chip_fg": "#e0e0e0",
    "chip_fill": "#2f343c",
    "chip_border": "#5c6878",
    "link_color": "#4ea1ff",
    "link_visited": "#c58af9",
    "tag_fg": "#e0e0e0",
    "tag_fill": "#2c3a26",
    "tag_border": "#5f7a50",
    "hover_accent": "#7aa2e0",
    "tl_day_bg": "#1c1c1c",
    "tl_weekend_bg": "#262626",
    "tl_today_bg": "#4a4420",
    "tl_grid_color": "#333333",
    "claude_fg": "#e8e4d8",
    "claude_bg": "#262624",
}
COLOR_KEYS = {row[2] for row in SCHEMA if row[5] in (COLOR, COLORS)}


def shown(s, key):
    # a colour setting as drawn: the stored value in light mode; in dark, the hand-picked value
    # while it is still the default, else the stored value mirrored
    if key not in COLOR_KEYS:
        raise KeyError(f"{key} is not a colour setting")
    v = s[key]
    if not rdb_theme.dark():
        return v
    if v == DEFAULT_SETTINGS[key] and key in DARK_DEFAULTS:
        return DARK_DEFAULTS[key]
    if isinstance(v, list):
        return [rdb_theme.mirror(c).name() for c in v]
    return rdb_theme.mirror(v).name()


_KINDS = {row[2]: row[5:] for row in SCHEMA}
H1_PAD = 5


def head_pad(s):
    # top and bottom block margins per heading level; the display paints the H1 box
    # H1_PAD beyond the text and the H2 rule h2_rule_px thick under it
    return {1: (H1_PAD + 3, H1_PAD + 3), 2: (6, s["h2_rule_px"] + 4), 3: (4, 2)}


HEAD_PAD = head_pad(DEFAULT_SETTINGS)
SEC_GAP = 8


def section_pad(s):
    # the Completed divider is painted SEC_GAP under the block above and SEC_GAP over the header
    return (2 * SEC_GAP + s["sec_rule_px"], 4)


SEC_PAD = section_pad(DEFAULT_SETTINGS)
DIVIDER_PAD = (3, 3)
_HEX = set("0123456789abcdef")
BULLETS = (
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
)
RESERVED = {"con", "prn", "aux", "nul", "map", "settings"} | {
    f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)
}


def today():
    return datetime.date.today().isoformat()


def make_id(title, taken):
    base = "".join(ch if ch.isalnum() else "_" for ch in title.lower())
    base = "_".join(p for p in base.split("_") if p)[:40] or "p"
    if base in RESERVED or NOTE_PID.fullmatch(base):
        base += "_"
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}_{n}", n + 1
    return pid


CARD_DEFAULTS = {
    "type": "list",
    "bg": "",
    "fg": "",
    "tags": [],
    "url": "",
    "snap": "",
    "view": "list",
    "kanban": False,
    "kanban_done": True,
    "kanban_w": {},
    "kanban_status_only": False,
    "kanban_order": [],
    "done": "",
    "sort": "",
    "scope": "",
    "lang": "",
    "zoom": 0.0,
}
PROJECT_TYPES = ("list", "links", "timeline")
# cards with a project document: the item holders, and the text page
DOC_TYPES = (*PROJECT_TYPES, "notepad")


def new_card(pid, title, x, y, kind="list", w=7, h=4):
    return {
        "id": pid,
        "title": title,
        "x": float(x),
        "y": float(y),
        "w": w,
        "h": h,
        **CARD_DEFAULTS,
        "tags": [],
        "kanban_w": {},
        "kanban_order": [],
        "type": kind,
        # a timeline keeps its checked rows in place
        **({"done": "place"} if kind == "timeline" else {}),
    }


def new_map():
    return {
        "cards": [new_card("inbox", "Inbox", 0, 0, "board")],
        "frames": [],
        "blocks": [],
    }


def new_board():
    return {"cards": [], "frames": [], "blocks": []}


def _fill_cards(m):
    # maps written before card types or blocks existed lack these fields
    m.setdefault("blocks", [])
    for b in m["blocks"]:
        if b["kind"] == "image":
            b.setdefault("note", "")
    for c in m["cards"]:
        for k, v in CARD_DEFAULTS.items():
            c.setdefault(k, type(v)(v) if isinstance(v, (list, dict)) else v)
    return m


def load_map(store):
    m = store.get_map() or new_map()
    if not any(c["id"] == "inbox" for c in m["cards"]):
        m["cards"].insert(0, new_map()["cards"][0])
    return _fill_cards(m)


def load_board(store, pid):
    return _fill_cards(store.get_board(pid))


def save_board(store, pid, m):
    store.put_board(pid, m)


def snip_target(store):
    # the board that receives snips: the Inbox until another is chosen, and again when the
    # chosen board is no longer a card on any map
    pid = store.get_snip_board() or "inbox"
    if pid != "inbox" and not any(
        c["id"] == pid and c["type"] == "board"
        for mp in all_maps(store)
        for c in mp["cards"]
    ):
        return "inbox"
    return pid


def all_maps(store):
    return [load_map(store)] + [load_board(store, b) for b in store.board_ids()]


def card_ids(store):
    return {c["id"] for mp in all_maps(store) for c in mp["cards"]}


def block_ids(store):
    return {b["id"] for mp in all_maps(store) for b in mp["blocks"]}


def board_contents(store, pid):
    projects, boards = set(), {pid}
    for c in load_board(store, pid)["cards"]:
        if c["type"] == "board":
            p, b = board_contents(store, c["id"])
            projects |= p
            boards |= b
        elif c["type"] in DOC_TYPES:
            projects.add(c["id"])
    return projects, boards


def delete_board(store, pid):
    projects, boards = board_contents(store, pid)
    for p in projects:
        if store.has_project(p):
            trash_project(store, p)
    for b in boards:
        mp = load_board(store, b)
        for blk in mp["blocks"]:
            if blk["kind"] == "image":
                store.delete_image(blk["img"])
                if store.has_project(blk["note"]):
                    trash_project(store, blk["note"])
        for c in mp["cards"]:
            if c["type"] == "contacts" and store.has_contacts(c["id"]):
                store.delete_contacts(c["id"])
        store.delete_board(b)


IMG_SRC = re.compile(r'src="(img/[^"]+)"')
COPY_TAG = re.compile(r" \(copy\d*\)$")


def copy_title(title, used):
    base = COPY_TAG.sub("", title)
    n = 1
    while (new := f"{base} (copy{n if n > 1 else ''})") in used:
        n += 1
    return new


def _project_rows(store, pid):
    # a list's stored row and, recursively, the note documents its items link
    rows = {pid: store.get_project(pid)}
    for nt in note_links(rows[pid]["meta"]):
        if store.has_project(nt):
            rows.update(_project_rows(store, nt))
    return rows


def _rows_bundle(store, pid):
    rows = _project_rows(store, pid)
    images = {
        n: store.get_image(n) for r in rows.values() for n in IMG_SRC.findall(r["html"])
    }
    markup = {n: store.get_markup(n) for n in images}
    return {"rows": rows, "images": images, "markup": markup}


def _copy_card(store, c):
    e = {"card": copy.deepcopy(c)}
    if c["type"] in DOC_TYPES and store.has_project(c["id"]):
        e |= _rows_bundle(store, c["id"])
    elif c["type"] == "board":
        mp = load_board(store, c["id"])
        e["board"] = copy_bundle(store, mp["cards"], mp["blocks"], mp["frames"])
    elif c["type"] == "page" and c["snap"]:
        e["snap"] = store.get_image(c["snap"])
    elif c["type"] == "contacts":
        e["contacts"] = store.get_contacts(c["id"])
    return e


def _copy_block(store, b):
    e = {"block": copy.deepcopy(b)}
    if b["kind"] == "image":
        e["image"] = store.get_image(b["img"])
        if store.has_project(b["note"]):
            e |= _rows_bundle(store, b["note"])
    return e


def copy_bundle(store, cards, blocks, frames=()):
    # everything a paste needs, read now: the source may change or go before it is pasted
    return {
        "cards": [_copy_card(store, c) for c in cards],
        "blocks": [_copy_block(store, b) for b in blocks],
        "frames": copy.deepcopy(list(frames)),
    }


def _paste_rows(store, pid, old, e, taken):
    ids = {old: pid}
    ids |= {n: new_note_pid(store, taken) for n in e["rows"] if n != old}
    names = store.image_names()
    for src_pid, row in e["rows"].items():
        row = copy.deepcopy(row)
        for src in dict.fromkeys(IMG_SRC.findall(row["html"])):
            n = 1
            while (name := f"img/{ids[src_pid]}_{n}{Path(src).suffix}") in names:
                n += 1
            names.add(name)
            store.put_image(name, e["images"][src])
            store.put_markup(name, e["markup"][src])
            row["html"] = row["html"].replace(f'"{src}"', f'"{name}"')
        for r in row["meta"]:
            if r.get("nt") in ids:
                r["nt"] = ids[r["nt"]]
            elif r.get("nt"):
                del r["nt"]
                r.pop("no", None)
        store.put_project(ids[src_pid], row["html"], row["meta"], row["collapsed"])


def _paste_card(store, e, taken, titles):
    c = copy.deepcopy(e["card"])
    old, c["id"] = c["id"], make_id(c["title"], taken)
    taken.add(c["id"])
    if titles is not None:
        c["title"] = copy_title(c["title"], titles)
        titles.add(c["title"])
    if "rows" in e:
        _paste_rows(store, c["id"], old, e, taken)
    if "board" in e:
        cards, blocks = paste_bundle(store, e["board"], taken)
        frames = copy.deepcopy(e["board"]["frames"])
        save_board(store, c["id"], {"cards": cards, "frames": frames, "blocks": blocks})
    if "snap" in e:
        c["snap"] = f"img/{c['id']}_snap.png"
        store.put_image(c["snap"], e["snap"])
    if "contacts" in e:
        store.put_contacts(c["id"], copy.deepcopy(e["contacts"]))
    return c


def _paste_block(store, e, taken):
    b = copy.deepcopy(e["block"])
    b["id"] = make_id(b["kind"], taken)
    taken.add(b["id"])
    if b["kind"] == "image":
        b["img"] = f"img/blk_{b['id']}.png"
        store.put_image(b["img"], e["image"])
        if "rows" in e:
            old, b["note"] = b["note"], new_note_pid(store, taken)
            _paste_rows(store, b["note"], old, e, taken)
        else:
            b["note"] = ""
    return b


def paste_bundle(store, bundle, taken, titles=None):
    # writes a fresh copy to the store under new ids (taken grows by them) and, given the
    # titles in use, new titles (which grow by them); returns the map entries of the pasted
    # cards and blocks, still at their source positions
    cards = [_paste_card(store, e, taken, titles) for e in bundle["cards"]]
    return cards, [_paste_block(store, e, taken) for e in bundle["blocks"]]


def done_mode(done, settings):
    return DONE_MODES[done] if done else settings["on_check"]


def board_summary(store, pid, settings):
    out = {"count": 0, "open": 0, "urgent": 0, "done": 0, "cards": []}
    for c in load_board(store, pid)["cards"]:
        if c["type"] == "board":
            s = board_summary(store, c["id"], settings)
        elif c["type"] in PROJECT_TYPES and store.has_project(c["id"]):
            s = counts(open_doc(store, c["id"], done_mode(c["done"], settings)))
        else:
            s = {"open": 0, "urgent": 0, "done": 0}
        out["count"] += 1
        for k in ("open", "urgent", "done"):
            out[k] += s[k]
        out["cards"].append(
            {"title": c["title"], "open": s["open"], "urgent": s["urgent"]}
        )
    return out


def save_map(store, m):
    store.put_map(m)


def _is_color(v):
    return (
        isinstance(v, str)
        and len(v) == 7
        and v[0] == "#"
        and set(v[1:].lower()) <= _HEX
    )


def is_key(v):
    if not v:
        return True
    try:
        rdb_hotkey.parse_key(v)
    except ValueError:
        return False
    return True


def _find_items_ok(value):
    if not isinstance(value, list) or not all(
        isinstance(v, str) and v in (*FIND_KEYS, FIND_DIV) for v in value
    ):
        return False
    keys = [v for v in value if v != FIND_DIV]
    return len(set(keys)) == len(keys)


def _parts_ok(value, keys):
    return (
        isinstance(value, list)
        and all(isinstance(v, str) and v in keys for v in value)
        and len(set(value)) == len(value)
    )


STATUS_FIELDS = {"key", "name", "icon", "color", "done"}


def _statuses_ok(value, icons):
    if not isinstance(value, list):
        return False
    keys = set()
    for r in value:
        if not (isinstance(r, dict) and set(r) == STATUS_FIELDS):
            return False
        key, name = r["key"], r["name"]
        if not (isinstance(key, str) and key and key not in keys):
            return False
        keys.add(key)
        if not (isinstance(name, str) and name and name == name.strip()):
            return False
        if not (r["icon"] in icons and _is_color(r["color"])):
            return False
        if not isinstance(r["done"], bool):
            return False
    return True


def check_setting(key, value):
    kind, *args = _KINDS[key]
    ok = {
        BOOL: lambda: isinstance(value, bool),
        INT: lambda: (
            isinstance(value, int)
            and not isinstance(value, bool)
            and args[0] <= value <= args[1]
        ),
        CHOICE: lambda: value in args[0],
        COLOR: lambda: _is_color(value),
        FONT: lambda: isinstance(value, str) and bool(value.strip()),
        STYLE: lambda: isinstance(value, str) and bool(value.strip()),
        PATH: lambda: isinstance(value, str) and bool(value.strip()),
        KEY: lambda: isinstance(value, str) and is_key(value),
        FINDITEMS: lambda: _find_items_ok(value),
        GUTTERPARTS: lambda: _parts_ok(value, args[0]),
        STATUSES: lambda: _statuses_ok(value, args[0]),
        COLORS: lambda: (
            isinstance(value, list)
            and len(value) == len(DEFAULT_SETTINGS[key])
            and all(map(_is_color, value))
        ),
    }[kind]()
    if not ok:
        raise ValueError(f"setting {key}={value!r} is not a valid {kind}")


# settings an older build wrote that no longer exist; dropped on load, anything else unknown fails
RETIRED = {
    "h1_click",
    "grid_major_every",
    "grid_major",
    "header_bg",
    "show_numbers",
    "show_bubbles",
}


def family_styles(family):
    return QFontDatabase.styles(family)


def pick_style(styles, stored):
    # a family this machine lacks lists no styles: the stored one stays
    if not styles or stored in styles:
        return stored
    for name in ("Regular", "Normal"):
        if name in styles:
            return name
    return styles[0]


# longest names first so "extrabold" is not read as "bold"
_STYLE_WEIGHTS = (
    ("extralight", QFont.Weight.ExtraLight),
    ("ultralight", QFont.Weight.ExtraLight),
    ("extrabold", QFont.Weight.ExtraBold),
    ("ultrabold", QFont.Weight.ExtraBold),
    ("semibold", QFont.Weight.DemiBold),
    ("demibold", QFont.Weight.DemiBold),
    ("thin", QFont.Weight.Thin),
    ("light", QFont.Weight.Light),
    ("medium", QFont.Weight.Medium),
    ("black", QFont.Weight.Black),
    ("heavy", QFont.Weight.Black),
    ("bold", QFont.Weight.Bold),
)


def style_traits(style):
    low = style.lower().replace(" ", "")
    italic = "italic" in low or "oblique" in low
    for word, weight in _STYLE_WEIGHTS:
        if word in low:
            return weight, italic
    return QFont.Weight.Normal, italic


def _style_traits_in(family, style):
    # the installed family's own weight and slant for the style, else read from the name
    if style in family_styles(family):
        return (
            QFont.Weight(QFontDatabase.weight(family, style)),
            QFontDatabase.italic(family, style),
        )
    return style_traits(style)


def style_for_weight(family, label):
    # the family's upright style whose weight is nearest the old weight setting
    want = WEIGHTS[label]
    upright = [s for s in family_styles(family) if not _style_traits_in(family, s)[1]]
    if not upright:
        return "Regular" if label == "Normal" else label
    return min(upright, key=lambda s: abs(_style_traits_in(family, s)[0] - want))


def migrate_settings(d):
    # editor_* and hN_bold, written before the list fonts became one grid; find_<key> BOOLs,
    # written before the find bar kept one ordered list
    d = dict(d)
    family = d.pop("editor_font", None)
    if family is not None:
        d.setdefault("body_font", family)
        for lvl in ("h1", "h2", "h3"):
            d.setdefault(f"{lvl}_font", family)
    if "editor_size" in d:
        d.setdefault("body_size", d.pop("editor_size"))
    if "editor_weight" in d:
        weight = d.pop("editor_weight")
        d.setdefault(
            "body_style",
            style_for_weight(d.get("body_font", DEFAULT_SETTINGS["body_font"]), weight),
        )
    for lvl in ("h1", "h2", "h3"):
        if f"{lvl}_bold" in d:
            d.setdefault(f"{lvl}_style", "Bold" if d.pop(f"{lvl}_bold") else "Regular")
    old = {k: d.pop(f"find_{k}") for k in FIND_KEYS if f"find_{k}" in d}
    if old:
        d.setdefault("find_items", [k for k in FIND_KEYS if old.get(k, True)])
    if "chip_accent" in d:
        d.setdefault("hover_accent", d.pop("chip_accent"))
    # a short-lived image gutter part, retired when images became chips
    if "image" in d.get("gutter_items", ()):
        d["gutter_items"] = [k for k in d["gutter_items"] if k != "image"]
    return d


def load_settings(store):
    stored = store.get_settings()
    loaded = migrate_settings({k: v for k, v in stored.items() if k not in RETIRED})
    if "gutter_items" not in loaded:
        off = {
            part
            for part, old in (("number", "show_numbers"), ("bubble", "show_bubbles"))
            if stored.get(old) is False
        }
        if off:
            loaded["gutter_items"] = [k for k in GUTTER_ITEM_KEYS if k not in off]
    # plan 64 kept lone tags in a "tag" part of the item gutter; plan 65 gave them a gutter
    if "tag" in loaded.get("gutter_items", ()):
        loaded["gutter_items"] = [k for k in loaded["gutter_items"] if k != "tag"]
        loaded.setdefault("tag_gutter", True)
    # a file that predates statuses gets the status part, after the marker; once the settings
    # are saved the statuses row exists and the user's list stands
    items = loaded.get("gutter_items")
    if items is not None and "statuses" not in stored and "status" not in items:
        at = items.index("mark") + 1 if "mark" in items else len(items)
        loaded["gutter_items"] = [*items[:at], "status", *items[at:]]
    unknown = set(loaded) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"{store.path}: unknown settings {sorted(unknown)}")
    for key, value in loaded.items():
        check_setting(key, value)
    return copy.deepcopy({**DEFAULT_SETTINGS, **loaded})


def font_of(settings, name):
    f = QFont(settings[f"{name}_font"], settings[f"{name}_size"])
    f.setWeight(WEIGHTS[settings[f"{name}_weight"]])
    return f


def list_font(settings, level):
    family, style = settings[f"{level}_font"], settings[f"{level}_style"]
    if style in family_styles(family):
        return QFontDatabase.font(family, style, settings[f"{level}_size"])
    f = QFont(family, settings[f"{level}_size"])
    weight, italic = style_traits(style)
    f.setWeight(weight)
    f.setItalic(italic)
    return f


def chip_weight(settings):
    if settings["chip_bold"]:
        return QFont.Weight.Bold
    return WEIGHTS[settings["chip_weight"]]


def tag_weight(settings):
    if settings["tag_bold"]:
        return QFont.Weight.Bold
    return WEIGHTS[settings["tag_weight"]]


def set_gutter(items, lpad=None, rpad=None, heads=()):
    global GUTTER_STEPS, GUTTER, GUTTER_LPAD, GUTTER_RPAD
    GUTTER_LPAD = GUTTER_LPAD if lpad is None else lpad
    GUTTER_RPAD = GUTTER_RPAD if rpad is None else rpad
    GUTTER = gutter_px(items, heads=heads)
    GUTTER_STEPS = GUTTER // INDENT_W


# the configured statuses in column order, and by key
STATUS_LIST = copy.deepcopy(DEFAULT_STATUSES)
STATUS_BY_KEY = {r["key"]: r for r in STATUS_LIST}


def configure(settings):
    global LINE_HEIGHT, EDITOR_FONT, HEAD_PAD, SEC_PAD, STATUS_LIST, STATUS_BY_KEY
    STATUS_LIST = copy.deepcopy(settings["statuses"])
    STATUS_BY_KEY = {r["key"]: r for r in STATUS_LIST}
    set_gutter(
        settings["gutter_items"],
        settings["gutter_lpad"],
        settings["gutter_rpad"],
        settings["gutter_heads"],
    )
    LINE_HEIGHT = settings["line_height"]
    HEAD_PAD = head_pad(settings)
    SEC_PAD = section_pad(settings)
    EDITOR_FONT = list_font(settings, "body")


def save_settings(store, s):
    store.put_settings(s)


def iter_blocks(doc):
    b = doc.begin()
    while b.isValid():
        yield b
        b = b.next()


def _read(f, k):
    if k in ("u", "no"):
        return f.boolProperty(PROPS[k])
    if k in ("h", "hs", "tn", "tp"):
        return f.intProperty(PROPS[k])
    return f.stringProperty(PROPS[k])


def get_meta(block):
    f = block.blockFormat()
    meta = {k: _read(f, k) for k in PROPS}
    # only headings carry a level, only items with a home carry an id or anchor and only
    # todos with a note carry nt and no, a marked todo p and a sorted one hs, and a timeline
    # task ts, tn (0 is a milestone), ti, ta, a progress tp and a bar colour tb, so files
    # without them read and write as before; ud is the day a todo became P1, hc an H1's own fill, ho its outline, hf its text
    for k in (
        "h",
        "i",
        "a",
        "no",
        "nt",
        "p",
        "hs",
        "ud",
        "st",
        "ts",
        "ti",
        "ta",
        "tp",
        "tb",
        "hc",
        "ho",
        "hf",
        "is",
        "in",
    ):
        if not meta[k]:
            del meta[k]
    if "ts" not in meta:
        del meta["tn"]
        meta.pop("tp", None)
    return meta


def set_meta(block, **fields):
    f = QTextBlockFormat()
    for key, value in fields.items():
        f.setProperty(PROPS[key], value)
    # a level belongs to its kind: changing the kind without naming a level drops it
    if "k" in fields and "h" not in fields:
        f.setProperty(PROPS["h"], 0)
    QTextCursor(block).mergeBlockFormat(f)


SIZE_SHARE = {"s": 0.25, "m": 0.5, "l": 1.0}


def image_sizes(text):
    # "is" lists only the non-Large images of a block, as JSON {image: size}; files from before
    # read as name:size pairs joined by commas
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    return dict(p.rsplit(":", 1) for p in text.split(","))


def _sizes_text(sizes):
    return json.dumps(sizes) if sizes else ""


def set_image_size(block, name, size):
    if size not in SIZE_SHARE:
        raise ValueError(f"image size {size!r}")
    sizes = image_sizes(block.blockFormat().stringProperty(PROPS["is"]))
    if size == "l":
        sizes.pop(name, None)
    else:
        sizes[name] = size
    set_meta(block, **{"is": _sizes_text(sizes)})


def rename_image_sizes(meta, rename):
    # a pasted image taking a new name keeps its size and its label
    for row in meta:
        if row.get("is"):
            sizes = image_sizes(row["is"])
            row["is"] = _sizes_text({rename.get(n, n): t for n, t in sizes.items()})
        if row.get("in"):
            labels = image_labels(row["in"])
            row["in"] = json.dumps({rename.get(n, n): t for n, t in labels.items()})


def drop_image_meta(block, names):
    # the name and size entries of images no longer in the block
    sizes = image_sizes(block.blockFormat().stringProperty(PROPS["is"]))
    labels = image_labels(block.blockFormat().stringProperty(PROPS["in"]))
    keep_s = {n: v for n, v in sizes.items() if n in names}
    keep_l = {n: v for n, v in labels.items() if n in names}
    if keep_s != sizes:
        set_meta(block, **{"is": _sizes_text(keep_s)})
    if keep_l != labels:
        set_meta(block, **{"in": json.dumps(keep_l) if keep_l else ""})


def image_labels(text):
    # "in": the names a block's images were given, as JSON {image: label}; the rest read "image N"
    return json.loads(text) if text else {}


def image_label(block, name):
    stored = image_labels(block.blockFormat().stringProperty(PROPS["in"])).get(name)
    return stored or image_label_default(block, name)


def set_image_label(block, name, label):
    labels = image_labels(block.blockFormat().stringProperty(PROPS["in"]))
    if label and label != image_label_default(block, name):
        labels[name] = label
    else:
        labels.pop(name, None)
    set_meta(block, **{"in": json.dumps(labels) if labels else ""})


def image_label_default(block, name):
    names = [n for _, _, n in item_images(owner_todo(block) or block)]
    return f"image {names.index(name) + 1}"


def todo_meta():
    return {"c": today(), "d": "", "u": False, "fg": "", "bg": "", "k": TODO}


def h1_colour(block, key="hc"):
    return block.blockFormat().stringProperty(PROPS[key])


def kind(block):
    return block.blockFormat().stringProperty(PROPS["k"])


def is_todo(block):
    return kind(block) == TODO


def is_section(block):
    return kind(block) == SECTION


def is_divider(block):
    return kind(block) == DIVIDER


def all_struck(block):
    seen = False
    it = block.begin()
    while not it.atEnd():
        fr = it.fragment()
        if fr.text().strip():
            if not fr.charFormat().fontStrikeOut():
                return False
            seen = True
        it += 1
    return seen


def clear_strike(block):
    c = QTextCursor(block)
    c.movePosition(
        QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
    )
    f = QTextCharFormat()
    f.setFontStrikeOut(False)
    c.mergeCharFormat(f)


CHAR_ATTRS = {
    "bold": (
        lambda f: f.fontWeight() > QFont.Weight.Normal,
        lambda f, on: f.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal),
    ),
    "italic": (QTextCharFormat.fontItalic, QTextCharFormat.setFontItalic),
    "underline": (QTextCharFormat.fontUnderline, QTextCharFormat.setFontUnderline),
    "strike": (QTextCharFormat.fontStrikeOut, QTextCharFormat.setFontStrikeOut),
}


def _fragments(cursor):
    # (start, end, char format) of each text fragment, cut to the selection
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    b = cursor.document().findBlock(start)
    while b.isValid() and b.position() < end:
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            s, e = max(fr.position(), start), min(fr.position() + fr.length(), end)
            if s < e and not fr.charFormat().isImageFormat():
                yield s, e, fr.charFormat()
            it += 1
        b = b.next()


def _selected_formats(cursor):
    return (f for _, _, f in _fragments(cursor))


def char_has(cursor, attr):
    # true when the whole selected text carries the attribute; an empty selection has none
    get = CHAR_ATTRS[attr][0]
    fmts = list(_selected_formats(cursor))
    return bool(fmts) and all(get(f) for f in fmts)


def char_toggle(cursor, attr):
    # remove the attribute when the whole selection has it, else set it; returns the new state
    if not cursor.hasSelection():
        return False
    on = not char_has(cursor, attr)
    f = QTextCharFormat()
    CHAR_ATTRS[attr][1](f, on)
    cursor.mergeCharFormat(f)
    return on


ALIGNS = (
    Qt.AlignmentFlag.AlignLeft,
    Qt.AlignmentFlag.AlignHCenter,
    Qt.AlignmentFlag.AlignRight,
    Qt.AlignmentFlag.AlignJustify,
)


def _paragraphs(cursor):
    # the blocks the cursor's selection touches, else its own; a selection that ends at the
    # start of a block leaves that block out
    end = cursor.selectionEnd()
    b = cursor.document().findBlock(cursor.selectionStart())
    yield b
    b = b.next()
    while b.isValid() and b.position() < end:
        yield b
        b = b.next()


def block_aligned(cursor):
    # the one paragraph alignment every targeted block has, else None
    seen = {
        next(x for x in ALIGNS if b.blockFormat().alignment() & x)
        for b in _paragraphs(cursor)
    }
    return seen.pop() if len(seen) == 1 else None


def block_align(cursor, align):
    if align not in ALIGNS:
        raise ValueError(f"alignment {align!r}")
    f = QTextBlockFormat()
    f.setAlignment(align)
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    for b in list(_paragraphs(cursor)):
        QTextCursor(b).mergeBlockFormat(f)
    edit.endEditBlock()


def selected_todos(cursor):
    # the visible todo blocks the selection touches, in document order
    return [b for b in _paragraphs(cursor) if is_todo(b) and b.isVisible()]


CHAR_COLORS = {
    "bg": QTextFormat.Property.BackgroundBrush,
    "fg": QTextFormat.Property.ForegroundBrush,
}


ORIGINALS = {"bg": ORIG_BG, "fg": ORIG_FG}


def set_char_colour(f, key, colour):
    # the colour as written is kept; the display value is its mirror in dark mode
    colour = QColor(colour)
    if rdb_theme.dark():
        f.setProperty(ORIGINALS[key], colour.name(QColor.NameFormat.HexArgb))
        colour = rdb_theme.mirror(colour)
    (f.setBackground if key == "bg" else f.setForeground)(QBrush(colour))


def _recolour(doc, start, end, fn):
    # fn(format) edits a copy of each character format in start..end and says whether it changed
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    b = doc.findBlock(start)
    runs = []
    while b.isValid() and b.position() < end:
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            f = fr.charFormat()
            if not f.isImageFormat() and fn(f):
                runs.append((fr.position(), fr.position() + fr.length(), f))
            it += 1
        b = b.next()
    for a, z, f in runs:
        edit.setPosition(a)
        edit.setPosition(z, QTextCursor.MoveMode.KeepAnchor)
        edit.setCharFormat(f)
    edit.endEditBlock()
    return bool(runs)


def _mirror_format(f):
    # an explicit solid colour with no original yet is the colour as written: mirror it
    changed = False
    for key, prop in CHAR_COLORS.items():
        if f.hasProperty(prop) and not f.stringProperty(ORIGINALS[key]):
            brush = f.property(prop)
            if brush.style() == Qt.BrushStyle.SolidPattern:
                set_char_colour(f, key, brush.color())
                changed = True
    return changed


def _restore_format(f):
    changed = False
    for key, prop in CHAR_COLORS.items():
        if f.stringProperty(ORIGINALS[key]):
            colour = QColor(f.stringProperty(ORIGINALS[key]))
            f.clearProperty(ORIGINALS[key])
            (f.setBackground if key == "bg" else f.setForeground)(QBrush(colour))
            changed = True
    return changed


def _has_originals(doc):
    for b in iter_blocks(doc):
        it = b.begin()
        while not it.atEnd():
            f = it.fragment().charFormat()
            if any(f.stringProperty(p) for p in ORIGINALS.values()):
                return True
            it += 1
    return False


def mirror_range(doc, start, end):
    # dark mode: the explicit colours of start..end are drawn mirrored, the originals kept
    if rdb_theme.dark():
        _recolour(doc, start, end, _mirror_format)


def restored(doc):
    # the document as it is saved: a copy with every character colour as written
    out = doc.clone()
    # the highlighter's block states reach the html as -qt-user-state; a clone drops them
    for b, c in zip(iter_blocks(doc), iter_blocks(out)):
        c.setUserState(b.userState())
    _recolour(out, 0, out.characterCount(), _restore_format)
    return out


def fragment_html(frag):
    # the html of a selection, colours as written
    tmp = QTextDocument()
    QTextCursor(tmp).insertFragment(frag)
    if not _recolour(tmp, 0, tmp.characterCount(), _restore_format):
        return frag.toHtml()
    return QTextDocumentFragment(tmp).toHtml()


def char_clear(cursor, key):
    # drop the character-level background ("bg") or colour ("fg") of the selection, so an
    # item or block colour underneath shows; the property goes, no explicit white or black
    prop = CHAR_COLORS[key]
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    for s, e, f in list(_fragments(cursor)):
        if f.hasProperty(prop):
            f.clearProperty(prop)
            f.clearProperty(ORIGINALS[key])
            edit.setPosition(s)
            edit.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            edit.setCharFormat(f)
    edit.endEditBlock()


FONT_PROPS = tuple(
    getattr(QTextFormat.Property, n)
    for n in (
        "FontFamily FontFamilies FontStyleName FontPointSize FontPixelSize "
        "FontSizeAdjustment FontFixedPitch FontLetterSpacing FontLetterSpacingType "
        "FontWordSpacing FontStretch FontCapitalization FontKerning FontStyleHint "
        "FontStyleStrategy FontHintingPreference"
    ).split()
)


def char_normalize(cursor):
    # drop the pasted font family/size/spacing overrides of the selection so the document
    # default font shows; weight, italic, underline, strike, colours and links stay
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    for s, e, f in list(_fragments(cursor)):
        if any(f.hasProperty(p) for p in FONT_PROPS):
            for p in FONT_PROPS:
                f.clearProperty(p)
            edit.setPosition(s)
            edit.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            edit.setCharFormat(f)
    # an empty paragraph has no run; its own format sets the line height
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    b = cursor.document().findBlock(start)
    while b.isValid() and (
        b.position() < end or b.position() == end and b.length() == 1
    ):
        f = b.charFormat()
        if any(f.hasProperty(p) for p in FONT_PROPS):
            for p in FONT_PROPS:
                f.clearProperty(p)
            edit.setPosition(b.position())
            edit.setBlockCharFormat(f)
        b = b.next()
    edit.endEditBlock()


def char_color(cursor, key, colour):
    # set the character background/colour of the selection; "" clears it
    if not colour:
        char_clear(cursor, key)
        return
    f = QTextCharFormat()
    set_char_colour(f, key, colour)
    cursor.mergeCharFormat(f)


def uniform_bg(block):
    # the one character background every non-space fragment shares, else ""
    seen = set()
    it = block.begin()
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        if not fr.text().strip():
            continue
        f = fr.charFormat()
        if (
            not f.hasProperty(CHAR_COLORS["bg"])
            or f.background().style() == Qt.BrushStyle.NoBrush
        ):
            return ""
        seen.add(f.background().color().name())
    return seen.pop() if len(seen) == 1 else ""


def is_heading(block):
    return kind(block) == HEADING


def heading_level(block):
    return block.blockFormat().intProperty(PROPS["h"]) if is_heading(block) else 0


def is_checked(block):
    return is_todo(block) and bool(block.blockFormat().stringProperty(PROPS["d"]))


def owner_todo(block):
    b = block
    while b.isValid():
        if is_todo(b):
            return b
        if is_section(b) or is_heading(b) or is_divider(b):
            return None
        b = b.previous()
    return None


def item_blocks(todo):
    out = [todo]
    if is_divider(todo):
        return out
    if is_heading(todo):
        if is_folded(todo):
            # the blocks its fold hides move, copy and go with it
            lvl, b = heading_level(todo), todo.next()
            while b.isValid() and not is_section(b) and not 0 < heading_level(b) <= lvl:
                out.append(b)
                b = b.next()
        return out
    b = todo.next()
    while b.isValid() and kind(b) not in (TODO, SECTION, HEADING, DIVIDER):
        out.append(b)
        b = b.next()
    return out


def item_of(block):
    todo = owner_todo(block)
    return item_blocks(todo) if todo is not None else [block]


def unit_of(block):
    # what moves, copies and goes with the line: its item, or a folded heading with its group
    return item_blocks(block) if is_heading(block) else item_of(block)


def note_link(block):
    return block.blockFormat().stringProperty(PROPS["nt"])


def note_open(todo):
    return todo.blockFormat().boolProperty(PROPS["no"])


def set_note_open(todo, flag):
    if not is_todo(todo):
        raise ValueError("only a todo has a note")
    set_meta(todo, no=bool(flag))
    layout_block(todo)


def note_links(meta):
    return [r["nt"] for r in meta if r.get("nt")]


def is_note_pid(pid):
    return NOTE_PID.fullmatch(pid) is not None


def _taken(store):
    return (
        set(store.project_ids())
        | set(store.trash_ids())
        | set(store.board_ids())
        | set(store.contact_ids())
        | card_ids(store)
    )


def new_note_pid(store, taken=None):
    # unique across projects, boards, cards and the trash; a caller making several passes
    # its own set, which this adds to
    taken = _taken(store) if taken is None else taken
    while (pid := f"n_{secrets.token_hex(3)}") in taken:
        pass
    taken.add(pid)
    return pid


def plain_meta():
    return {**todo_meta(), "k": ""}


def create_note(store, taken=None, text=""):
    pid = new_note_pid(store, taken)
    doc = QTextDocument()
    prep_doc(doc)
    QTextCursor(doc).insertText(text)
    set_meta(doc.begin(), **plain_meta())
    layout_block(doc.begin())
    save_project(store, pid, doc)
    return pid


def duplicate_note(store, pid, taken=None):
    data = store.get_project(pid)
    new = new_note_pid(store, taken)
    store.put_project(new, data["html"], data["meta"], data["collapsed"])
    return new


def trash_project(store, pid):
    # a list goes to the trash with the notes of its items
    for nt in note_links(store.get_project(pid)["meta"]):
        if store.has_project(nt):
            trash_project(store, nt)
    store.delete_project(pid)
    PREVIEWS.pop(pid, None)


def excerpt(text, limit=60):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _preview(text):
    lines = text.replace("￼", "").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[:PREVIEW_LINES] or [""], max(len(lines) - PREVIEW_LINES, 0)


def load_preview(store, pid):
    PREVIEWS[pid] = _preview(html_text(store.get_project(pid)["html"]))


def preview_rows(block):
    # what a todo's open preview shows: its note's first lines, then the +N lines readout;
    # None while it is closed or has no note
    nt = note_link(block)
    if not (is_todo(block) and nt and note_open(block) and nt in PREVIEWS):
        return None
    lines, more = PREVIEWS[nt]
    return [*lines, f"+{more} line{'s' * (more != 1)}"] if more else list(lines)


def preview_font():
    return EDITOR_FONT


def note_pitch():
    return QFontMetrics(EDITOR_FONT).lineSpacing()


def panel_height(rows):
    # the yellow panel: NOTE_PAD inside above and below the rows
    return rows * note_pitch() + 2 * NOTE_PAD


def section_block(doc):
    return next((b for b in iter_blocks(doc) if is_section(b)), None)


def group_tail(doc, head=None):
    # the last block of the card's open items, or of the group under a heading, which runs to
    # the next heading of its level or above; invalid when Completed opens the list
    if head is None:
        sec = section_block(doc)
        return sec.previous() if sec is not None else doc.lastBlock()
    lvl, last, b = heading_level(head), head, head.next()
    while b.isValid() and not is_section(b) and not 0 < heading_level(b) <= lvl:
        last, b = b, b.next()
    return last


def bullet_for(level):
    return BULLETS[(level - 1) % len(BULLETS)]


def set_list_level(cursor, level, style=None):
    block = cursor.block()
    cur = block.textList()
    if style is None:
        numbered = cur is not None and cur.format().style() not in BULLETS
        style = cur.format().style() if numbered else bullet_for(level)
    b = block.previous()
    while b.isValid() and b.textList() is not None:
        lst = b.textList()
        indent = lst.format().indent()
        if indent == level and lst.format().style() == style:
            if lst.itemNumber(block) < 0:
                if cur is not None:
                    cur.remove(block)
                lst.add(block)
                # QTextList.remove folds the list indent into the block indent
                bf = block.blockFormat()
                bf.setIndent(gutter_steps(block))
                QTextCursor(block).setBlockFormat(bf)
            return
        if indent < level:
            break
        b = b.previous()
    f = QTextListFormat()
    f.setIndent(level)
    f.setStyle(style)
    cursor.createList(f)


def make_todo(block):
    lst = block.textList()
    if lst is not None:
        lst.remove(block)
        bf = block.blockFormat()
        bf.setIndent(gutter_steps(block))
        QTextCursor(block).setBlockFormat(bf)
    set_meta(block, k=TODO, c=get_meta(block)["c"] or today())
    layout_block(block)


def make_heading(block, level):
    if level not in (1, 2, 3):
        raise ValueError(f"heading level {level!r}")
    make_todo(block)
    set_meta(
        block,
        k=HEADING,
        h=level,
        d="",
        u=False,
        fg="",
        bg="",
        p="",
        hs=0,
        ud="",
        st="",
        hc="",
        ho="",
        hf="",
        **NO_TASK,
    )
    layout_block(block)


def make_divider(block):
    set_meta(block, c=today(), d="", u=False, fg="", bg="", k=DIVIDER, **NEW_ITEM)
    layout_block(block)


def make_note(cursor, level=1):
    set_meta(cursor.block(), k="", d="", u=False, p="", hs=0, ud="", st="")
    set_list_level(cursor, level)
    # a todo turned into a sub-bullet gives up the room its preview panel reserved
    layout_block(cursor.block())


def is_note_doc(doc):
    return bool(doc.property("rdb_note"))


def is_text_doc(doc):
    return bool(doc.property("rdb_text"))


def note_font(font):
    f = QFont(font)
    f.setFamily(NOTE_FAMILY)
    return f


def gutter_steps(block):
    # a Note card has no gutter: its blocks start at the page's left edge
    return 0 if is_text_doc(block.document()) else GUTTER_STEPS


def layout_block(block):
    f = block.blockFormat()
    steps = gutter_steps(block)
    top, bottom = (0, 0)
    if is_heading(block):
        top, bottom = HEAD_PAD[heading_level(block)]
    elif is_section(block):
        top, bottom = SEC_PAD
    elif is_divider(block):
        top, bottom = DIVIDER_PAD
    else:
        if f.stringProperty(CAPTION):
            top = CAPTION_PAD
        if (rows := preview_rows(block)) is not None:
            # the preview panel sits in the todo's bottom margin, NOTE_PAD clear of the text and the next block
            bottom = panel_height(len(rows)) + 2 * NOTE_PAD
    # pasted Word paragraphs bring their own spacing and hanging indent; items use none
    if (
        f.leftMargin(),
        f.indent(),
        f.lineHeight(),
        f.topMargin(),
        f.bottomMargin(),
        f.textIndent(),
    ) != (
        0,
        steps,
        LINE_HEIGHT,
        top,
        bottom,
        0,
    ):
        bf = QTextBlockFormat()
        bf.setLeftMargin(0)
        bf.setIndent(steps)
        bf.setTopMargin(top)
        bf.setBottomMargin(bottom)
        bf.setTextIndent(0)
        bf.setLineHeight(
            LINE_HEIGHT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        QTextCursor(block).mergeBlockFormat(bf)


def _reapply(doc, metas):
    if doc.blockCount() != len(metas):
        raise RuntimeError(f"block count {doc.blockCount()} != meta count {len(metas)}")
    for b, m in zip(list(iter_blocks(doc)), metas):
        if get_meta(b) != m:
            # a block that merged with a neighbour's format may carry its id or anchor
            set_meta(b, **{**NEW_ITEM, **m})
        if m["k"] in (TODO, SECTION) and b.textList() is not None:
            make_todo(b)
            set_meta(b, k=m["k"])
        layout_block(b)
    # Qt reports one changed range for a whole edit block, and a block outside it keeps no
    # highlight; the editor restyles the document when the edit ends
    doc.setProperty("rdb_restyle", True)


def _span(doc, first, last):
    # selection covering [first..last] plus exactly one paragraph separator
    c = QTextCursor(doc)
    end = last.position() + last.length() - 1
    if first.previous().isValid():
        c.setPosition(first.position() - 1)
        c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    else:
        c.setPosition(first.position())
        c.setPosition(end + 1, QTextCursor.MoveMode.KeepAnchor)
    return c


def tables(doc):
    # Qt also wraps loaded images in plain child frames, so only table-format frames count
    out = []

    def walk(frame):
        for f in frame.childFrames():
            if f.format().isTableFormat():
                out.append(f)
            walk(f)

    walk(doc.rootFrame())
    return out


def check_movable(doc):
    # table cells are blocks too; block-number bookkeeping in move_item cannot follow them
    if tables(doc):
        raise ValueError("items cannot be moved in a document that contains a table")


def _cell_text(cell):
    c = cell.firstCursorPosition()
    c.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
    return " ".join(c.selectedText().split())


def flatten_doc(doc):
    # each table becomes one tab-joined open item per row
    # the last table in document pre-order has no table inside it
    while ts := tables(doc):
        t = ts[-1]
        rows = []
        for r in range(t.rows()):
            cells = [t.cellAt(r, i) for i in range(t.columns())]
            rows.append(
                "\t".join(
                    _cell_text(x)
                    for i, x in enumerate(cells)
                    if (x.row(), x.column()) == (r, i)
                )
            )
        c = QTextCursor(doc)
        c.setPosition(t.firstPosition() - 1)
        c.setPosition(t.lastPosition() + 1, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        if c.block().text():
            c.insertBlock()
        c.insertText("\n".join(rows))
        # the new blocks inherit whatever item the table sat in
        for n in range(c.blockNumber() - len(rows) + 1, c.blockNumber() + 1):
            b = doc.findBlockByNumber(n)
            make_todo(b)
            set_meta(b, **todo_meta(), **NEW_ITEM)


def flatten_tables(src, markdown=False):
    tmp = QTextDocument()
    (tmp.setMarkdown if markdown else tmp.setHtml)(src)
    if not tables(tmp):
        return None
    flatten_doc(tmp)
    return tmp.toHtml()


def move_item(doc, blocks, before):
    check_movable(doc)
    nums = [b.blockNumber() for b in blocks]
    if before is not None and before.blockNumber() in nums + [nums[-1] + 1]:
        return blocks[0]
    if not blocks[0].previous().isValid():
        if not blocks[-1].next().isValid():
            return blocks[0]
        # the cut below needs a preceding block, so move what lies between item and target up instead
        stop = before.blockNumber() if before is not None else doc.blockCount()
        between = [doc.findBlockByNumber(i) for i in range(nums[-1] + 1, stop)]
        move_item(doc, between, doc.begin())
        return doc.findBlockByNumber(len(between))
    head = blocks[0].textList()
    head_list = (
        (head.format().indent(), head.format().style()) if head is not None else None
    )
    metas = [get_meta(b) for b in iter_blocks(doc)]
    moved = metas[nums[0] : nums[-1] + 1]
    rest = metas[: nums[0]] + metas[nums[-1] + 1 :]
    target = (
        len(rest)
        if before is None
        else before.blockNumber()
        - (len(nums) if before.blockNumber() > nums[-1] else 0)
    )
    # cut from the separator before the item: a fragment that starts at a block start
    # carries that block's list and would insert as an extra block
    c = _span(doc, blocks[0], blocks[-1])
    frag = c.selection()
    c.removeSelectedText()
    ins = QTextCursor(doc)
    if target == 0:
        ins.setPosition(0)
        ins.insertBlock(QTextBlockFormat(), QTextCharFormat())
        ins.setPosition(0)
        ins.insertFragment(frag)
        ins.setPosition(0)
        ins.deleteChar()
    else:
        ins.setPosition(doc.findBlockByNumber(target - 1).position())
        ins.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        ins.insertFragment(frag)
    _reapply(doc, rest[:target] + moved + rest[target:])
    first = doc.findBlockByNumber(target)
    # at the top the first block merges into the old top block's format and loses its list
    if head_list is not None and first.textList() is None:
        set_list_level(QTextCursor(first), *head_list)
    return first


def item_id(block):
    return _id(block)


def _group_spans(doc, heads):
    return [
        (h.blockNumber(), group_tail(doc, h).blockNumber() - h.blockNumber() + 1)
        for h in heads
    ]


def _place(doc, spans, nb):
    # spans: (first block number, size) of groups in document order; each lands directly above
    # block number nb (the block count for the end), so they gather in order. Returns the
    # number of the first group's heading.
    rest = list(spans)
    while rest:
        f, s = rest.pop(0)
        blocks = [doc.findBlockByNumber(n) for n in range(f, f + s)]
        move_item(
            doc, blocks, doc.findBlockByNumber(nb) if nb < doc.blockCount() else None
        )
        nb1 = nb - s if nb > f else nb
        rest = [(g - s + (s if g - s >= nb1 else 0), t) for g, t in rest]
        nb = nb1 + s
    return nb - sum(s for _, s in spans)


def move_groups(doc, heads, before):
    # headings with all they hold, gathered in document order above `before`; None when that
    # leaves them where they are or lands inside one of them
    spans = _group_spans(doc, heads)
    nb = doc.blockCount() if before is None else before.blockNumber()
    if any(f <= nb < f + s for f, s in spans):
        return None
    if spans[-1][0] + spans[-1][1] == nb and all(
        f2 == f1 + s1 for (f1, s1), (f2, _) in zip(spans, spans[1:])
    ):
        return None
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        first = doc.findBlockByNumber(_place(doc, spans, nb))
        group_completed(doc)
        refresh_captions(doc)
        return first
    finally:
        c.endEditBlock()


def step_groups(doc, heads, step):
    # H1 groups step as a run over one whole group; a set with gaps first gathers under its
    # topmost heading. None at the edge of the open items.
    spans = _group_spans(doc, heads)
    f0, total = spans[0][0], sum(s for _, s in spans)
    if step < 0:
        above = next(
            (
                b
                for b in map(doc.findBlockByNumber, range(f0 - 1, -1, -1))
                if heading_level(b) == 1
            ),
            None,
        )
        if above is None and f0:
            prev = doc.findBlockByNumber(f0 - 1)
            above = _fold_owner(prev, 3) or unit_head(prev)
        if above is None:
            return None
        nb = above.blockNumber()
    else:
        nxt = doc.findBlockByNumber(f0 + total)
        if f0 + total >= doc.blockCount() or heading_level(nxt) != 1:
            return None
        nb = group_tail(doc, nxt).blockNumber() + 1
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        if len(spans) > 1:
            _place(doc, spans[1:], f0 + spans[0][1])
        run, n = [], f0
        for _, s in spans:
            run.append((n, s))
            n += s
        first = doc.findBlockByNumber(_place(doc, run, nb))
        group_completed(doc)
        refresh_captions(doc)
        return first
    finally:
        c.endEditBlock()


def spot_hidden(doc, before, lvl):
    # a block of this level put before `before` would land inside a folded group
    prev = before.previous() if before is not None else doc.lastBlock()
    return _fold_owner(prev, lvl) is not None


def move_item_step(doc, block, step, last=None, keep=False):
    # last: the end of a selection; every item from block's through last's moves as one run
    # keep: items stay under their heading; only a move of a heading crosses headings
    sec = section_block(doc)
    above = sec is not None and block.blockNumber() < sec.blockNumber()
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        moved = (
            _step_item(doc, block, step, keep)
            if last is None
            else _step_run(doc, block, last, step, keep)
        )
        # a step in the open area may change the home category of what waits under Completed
        if moved is not None and above:
            group_completed(doc)
            refresh_captions(doc)
        return moved
    finally:
        c.endEditBlock()


def unit_head(block):
    # the item, heading or divider a block belongs to; None for free text outside items
    return block if is_heading(block) or is_divider(block) else owner_todo(block)


def _step_item(doc, block, step, keep):
    if is_section(block):
        return None
    todo = unit_head(block)
    if todo is None or todo.blockNumber() != block.blockNumber():
        # free text moves among its item's own blocks
        nb = block.previous() if step < 0 else block.next()
        if not nb.isValid() or kind(nb) in (TODO, SECTION, HEADING, DIVIDER):
            return None
        after = nb if step < 0 else nb.next()
        return move_item(doc, [block], after if after.isValid() else None)
    return _step_blocks(
        doc,
        todo,
        item_blocks(todo),
        min(heading_level(todo) or 3, 3),
        step,
        keep and not is_heading(todo),
    )


def _step_run(doc, first, last, step, keep):
    heads = []
    for b in (first, last):
        head = unit_head(b)
        if head is None or is_section(b):
            return None
        heads.append(head)
    blocks = []
    b = heads[0]
    end = item_blocks(heads[1])[-1].blockNumber()
    while b.isValid() and b.blockNumber() <= end:
        if is_section(b):
            return None
        blocks.append(b)
        b = b.next()
    lvl = min([heading_level(b) for b in blocks if is_heading(b)] + [3])
    return _step_blocks(
        doc,
        heads[0],
        blocks,
        lvl,
        step,
        keep and not any(is_heading(b) for b in blocks),
    )


def _step_blocks(doc, todo, blocks, lvl, step, stay):
    # stay: a step onto a heading is no step
    if step < 0:
        prev = todo.previous()
        if not prev.isValid():
            return None
        # the unit above: a folded group (from its heading), an item (from its todo) or a heading
        head = _fold_owner(prev, 3) or unit_head(prev)
        if head is None:
            return None
        # a spot inside a folded group would hide the moved blocks: step over that group too
        while (inside := _fold_owner(head.previous(), lvl)) is not None:
            head = inside
        if stay and is_heading(head):
            return None
        return move_item(doc, blocks, head)
    nxt = blocks[-1].next()
    if not nxt.isValid() or kind(nxt) not in (TODO, HEADING, DIVIDER):
        return None
    if stay and is_heading(nxt):
        return None
    after = item_blocks(nxt)[-1].next()
    while _fold_owner(after.previous() if after.isValid() else doc.lastBlock(), lvl):
        if not after.isValid() or kind(after) not in (TODO, HEADING, DIVIDER):
            return None
        after = item_blocks(after)[-1].next()
    return move_item(doc, blocks, after if after.isValid() else None)


def remove_item(doc, blocks):
    nums = [b.blockNumber() for b in blocks]
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        if nums[0] == 0 and nums[-1] == doc.blockCount() - 1:
            c.select(QTextCursor.SelectionType.Document)
            c.removeSelectedText()
            make_todo(doc.begin())
            # a note page empties to a plain line
            meta = plain_meta() if is_note_doc(doc) else todo_meta()
            set_meta(doc.begin(), **meta, **NEW_ITEM)
            layout_block(doc.begin())
            return
        metas = [get_meta(b) for b in iter_blocks(doc)]
        rest = metas[: nums[0]] + metas[nums[-1] + 1 :]
        _span(doc, blocks[0], blocks[-1]).removeSelectedText()
        _reapply(doc, rest)
        _drop_section_if_unused(doc)
        # what waited on the removed item has lost its anchor
        group_completed(doc)
        refresh_captions(doc)
    finally:
        c.endEditBlock()


def remove_items(doc, items):
    # items: block lists in document order; one edit, so one undo step
    gone = {b.blockNumber() for blocks in items for b in blocks}
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        # a lone Completed header must not outlive the last item
        if all(b.blockNumber() in gone or is_section(b) for b in iter_blocks(doc)):
            remove_item(doc, list(iter_blocks(doc)))
        else:
            for blocks in reversed(items):
                remove_item(doc, blocks)
    finally:
        c.endEditBlock()


def remove_block(doc, block):
    if doc.blockCount() == 1:
        set_meta(block, k="")
        return
    n = block.blockNumber()
    metas = [get_meta(b) for b in iter_blocks(doc)]
    _span(doc, block, block).removeSelectedText()
    _reapply(doc, metas[:n] + metas[n + 1 :])


def ensure_section(doc):
    sec = section_block(doc)
    if sec is not None:
        return sec
    c = QTextCursor(doc)
    c.movePosition(QTextCursor.MoveOperation.End)
    c.insertBlock(QTextBlockFormat(), QTextCharFormat())
    set_meta(c.block(), c="", d="", u=False, fg="", bg="", k=SECTION)
    layout_block(c.block())
    return c.block()


def is_collapsed(doc):
    return bool(doc.property("rdb_collapsed"))


def set_collapsed(doc, flag):
    doc.setProperty("rdb_collapsed", bool(flag))


# H1 and H2 fold; the folded headings are a set of item ids on the document, so a fold
# follows its heading through edits and moves and stays out of the undo stack
FOLDABLE = (1, 2)


def _folds(doc):
    return set(doc.property("rdb_folds") or ())


def is_folded(block):
    return heading_level(block) in FOLDABLE and _id(block) in _folds(block.document())


def set_folded(doc, block, flag):
    if heading_level(block) not in FOLDABLE:
        raise ValueError("only an H1 or H2 folds")
    if not _id(block):
        ensure_ids(doc)
    folds = _folds(doc)
    (folds.add if flag else folds.discard)(_id(block))
    doc.setProperty("rdb_folds", sorted(folds))


def fold_state(doc):
    # (block, hidden by a fold): a folded H1 hides down to the next H1, a folded H2 down to the
    # next H1 or H2; the Completed header ends both. A heading keeps its own state under a folded H1
    folds, h1, h2 = _folds(doc), False, False
    for b in iter_blocks(doc):
        lvl = heading_level(b)
        if is_section(b):
            h1 = h2 = hidden = False
        elif lvl == 1:
            hidden, h1, h2 = False, _id(b) in folds, False
        elif lvl == 2:
            hidden, h2 = h1, _id(b) in folds
        else:
            hidden = h1 or h2
        yield b, hidden


def fold_headings(doc):
    # every H1 and H2 above Completed
    out = []
    for b in iter_blocks(doc):
        if is_section(b):
            break
        if heading_level(b) in FOLDABLE:
            out.append(b)
    return out


def set_all_folded(doc, flag):
    for b in fold_headings(doc):
        set_folded(doc, b, flag)


def fold_heading(block):
    # the nearest H1 or H2 at or above a block, None in front of the first or under Completed
    while block.isValid() and not is_section(block):
        if heading_level(block) in FOLDABLE:
            return block
        block = block.previous()
    return None


def _fold_above(b, limit):
    # the headings whose group holds a block of this level placed right after b, nearest first
    while limit > 1 and b.isValid() and not is_section(b):
        if 0 < (lvl := heading_level(b)) < limit:
            yield b
            limit = lvl
        b = b.previous()


def _fold_owner(b, lvl):
    # the outermost folded heading above whose group a block of this level after b falls in
    return next((h for h in reversed(list(_fold_above(b, lvl))) if is_folded(h)), None)


def fold_owner(block):
    # the outermost folded heading that hides a block
    if is_section(block):
        return None
    return _fold_owner(block.previous(), heading_level(block) or 3)


def unfold_to(doc, block):
    # opens the folds above a block; the block's own fold is left as it is
    folds = _folds(doc)
    for h in _fold_above(block.previous(), heading_level(block) or 3):
        folds.discard(_id(h))
    doc.setProperty("rdb_folds", sorted(folds))


def open_to(doc, block):
    # opens what hides a block: the folds above it, and Completed when it sits under that header
    unfold_to(doc, block)
    sec = section_block(doc)
    if sec is not None and block.blockNumber() > sec.blockNumber():
        set_collapsed(doc, False)


def show_item(doc, block):
    open_to(doc, block)
    doc.setProperty("rdb_shown", _id(block))


def _open_view(doc, mode):
    # (block, shown before the folds): what the Completed collapse and the Hide mode leave
    if mode not in MODES:
        raise ValueError(f"mode {mode!r}")
    collapsed, after_section, hide_item = is_collapsed(doc), False, False
    shown = doc.property("rdb_shown")
    for b in iter_blocks(doc):
        k = kind(b)
        if is_section(b):
            after_section, visible = True, True
        elif mode == "hide":
            if k in (TODO, HEADING, DIVIDER):
                hide_item = is_checked(b) and _id(b) != shown
            visible = not hide_item
        else:
            visible = not (mode == "move" and after_section and collapsed)
        yield b, visible


def folded_away(doc):
    # the numbers of the blocks only a fold hides; the view mode alone would show them
    if not _folds(doc):
        return set()
    opened = {b.blockNumber(): v for b, v in _open_view(doc, doc.property("rdb_mode"))}
    return {
        b.blockNumber() for b, hid in fold_state(doc) if hid and opened[b.blockNumber()]
    }


def apply_view(doc, mode):
    doc.setProperty("rdb_mode", mode)
    for (b, visible), (_, folded) in zip(_open_view(doc, mode), fold_state(doc)):
        visible = visible and not folded
        if b.isVisible() != visible:
            b.setVisible(visible)
            doc.markContentsDirty(b.position(), b.length())


def _drop_section_if_unused(doc):
    sec = section_block(doc)
    if sec is not None and not any(is_checked(b) for b in iter_blocks(doc)):
        remove_block(doc, sec)


Unit = namedtuple("Unit", "id blocks sec")


def _id(block):
    return block.blockFormat().stringProperty(PROPS["i"])


def _anchor(block):
    return block.blockFormat().stringProperty(PROPS["a"])


def _units(doc):
    # every todo (with the blocks that follow it) and heading in document order; sec: after the Completed header;
    # an item with no id yet is named by its block number, good for reading only
    out, sec, notes = [], False, None
    for b in iter_blocks(doc):
        k = kind(b)
        if k == SECTION:
            sec, notes = True, None
        elif k == DIVIDER:
            notes = None
        elif k == HEADING:
            notes = None
            out.append(Unit(_id(b) or f"~{b.blockNumber()}", [b], sec))
        elif k == TODO:
            notes = [b]
            out.append(Unit(_id(b) or f"~{b.blockNumber()}", notes, sec))
        elif notes is not None:
            notes.append(b)
    return out


def done_items(doc):
    # the block lists of the checked items under the Completed header, in document order
    return [u.blocks for u in _units(doc) if u.sec and is_checked(u.blocks[0])]


def ensure_ids(doc, kinds=(TODO, HEADING)):
    items = [b for b in iter_blocks(doc) if kind(b) in (TODO, HEADING)]
    taken = {i for b in items if (i := _id(b))}
    seen = set()
    for b in items:
        i = _id(b)
        if kind(b) in kinds and (not i or i in seen):
            while (i := secrets.token_hex(3)) in taken:
                pass
            taken.add(i)
            set_meta(b, i=i)
        seen.add(i)


def mark_of(meta):
    # urgent and P1 are one thing: an item that is only urgent reads as P1
    return "p1" if meta["u"] else meta.get("p", "")


def marker(block):
    return mark_of(get_meta(block))


def mark_rank(mark):
    return PRIOS.index(mark) if mark in PRIOS else len(PRIOS)


def set_marker(block, mark):
    if mark not in MARKS:
        raise ValueError(f"marker {mark!r}")
    if not is_todo(block):
        raise ValueError("only a todo has a marker")
    since = ""
    if mark == "p1":
        # a P1 item keeps the day it became urgent when P1 is set again
        was = marker(block) == "p1"
        since = block.blockFormat().stringProperty(PROPS["ud"]) if was else today()
    set_meta(block, p=mark, u=mark == "p1", ud=since)


def status_of(block):
    return block.blockFormat().stringProperty(PROPS["st"])


def _done_status(key):
    return key in STATUS_BY_KEY and STATUS_BY_KEY[key]["done"]


def _first_done():
    return next((r["key"] for r in STATUS_LIST if r["done"]), "")


def set_status(doc, block, key, mode):
    # the status and the checkbox it implies are one undo step
    todo = owner_todo(block)
    if todo is None:
        raise ValueError("only a todo has a status")
    if key and key not in STATUS_BY_KEY:
        raise ValueError(f"status {key!r}")
    # refuse before writing: toggle_check raising after st is set would leave half an edit
    if mode == "move" and is_checked(todo) != _done_status(key):
        check_movable(doc)
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        set_meta(todo, st=key)
        if is_checked(todo) != _done_status(key):
            todo = toggle_check(doc, todo, mode)
            set_meta(todo, st=key)
    finally:
        c.endEditBlock()
    return todo


def is_sorted(doc):
    return bool(doc.property("rdb_sort"))


def set_sorted(doc, flag):
    doc.setProperty("rdb_sort", bool(flag))


def _hs(block):
    return block.blockFormat().intProperty(PROPS["hs"])


def _home_keys(units):
    # where each open todo sits in its home order: its home number, or, for an item made since,
    # right after the item above it in the same heading group
    keys, last, run = {}, 0, 0
    for u in units:
        if u.sec:
            break
        b = u.blocks[0]
        if is_heading(b):
            last = run = 0
        elif h := _hs(b):
            last, run = h, 0
            keys[u.id] = (h, 0)
        else:
            run += 1
            keys[u.id] = (last, run)
    return keys


def _groups(units):
    # the open area as [heading unit or None, [todo units]]: headings never move
    out = [[None, []]]
    for u in units:
        if u.sec:
            break
        if is_heading(u.blocks[0]):
            out.append([u, []])
        else:
            out[-1][1].append(u)
    return out


def _grouped(units, by_rank):
    # ids of the open units, each heading group in home order, or in rank then home order
    keys = _home_keys(units)

    def order(u):
        rank = mark_rank(marker(u.blocks[0])) if by_rank else 0
        return rank, keys[u.id]

    out = []
    for head, group in _groups(units):
        if head is not None:
            out.append(head.id)
        out += [u.id for u in sorted(group, key=order)]
    return out


def home_new(doc):
    # in a sorted list an open item with no home number (made, pasted or put back since) gets
    # one right after the item above it. Call it where the item is made: the item above is then
    # the one it was typed after, which the sorted view no longer says later
    ensure_ids(doc)
    units = _units(doc)
    if all(_hs(u.blocks[0]) for u in units if not u.sec and is_todo(u.blocks[0])):
        return
    by_id, n = {u.id: u for u in units}, 0
    for uid in _grouped(units, False):
        if is_todo(b := by_id[uid].blocks[0]):
            n += 1
            if _hs(b) != n:
                set_meta(b, hs=n)


def _arrange(doc, want, sec=False):
    # the units of the open area (or the Completed items) go into the order of ids in want
    units = _units(doc)
    for n, uid in enumerate(want):
        now = [u for u in units if u.sec == sec and (not sec or is_todo(u.blocks[0]))]
        if n < len(now) and now[n].id == uid:
            continue
        blocks = next(u.blocks for u in units if u.id == uid)
        end = None if sec else section_block(doc)
        move_item(doc, blocks, now[n].blocks[0] if n < len(now) else end)
        units = _units(doc)


def sort_open(doc):
    # open items by priority rank within each heading group, in their home order inside a rank;
    # every item carries its home number, so unsort_open puts them back
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        ensure_ids(doc)
        # every todo is numbered, done ones too, so a list with nothing open still reads as sorted;
        # numbers already there are the true homes and stay
        if not has_home(doc):
            n = 0
            for u in _units(doc):
                if is_todo(u.blocks[0]):
                    n += 1
                    set_meta(u.blocks[0], hs=n)
        set_sorted(doc, True)
        home_new(doc)
        _arrange(doc, _grouped(_units(doc), True))
    finally:
        c.endEditBlock()


def unsort_open(doc):
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        ensure_ids(doc)
        _arrange(doc, _grouped(_units(doc), False))
        for b in iter_blocks(doc):
            if _hs(b):
                set_meta(b, hs=0)
        set_sorted(doc, False)
    finally:
        c.endEditBlock()


def place_item(doc, block):
    # in a sorted list the item whose marker changed takes its place in its group; nothing else moves
    home_new(doc)
    units = _units(doc)
    keys, uid = _home_keys(units), _id(block)

    def order(u):
        return mark_rank(marker(u.blocks[0])), keys[u.id]

    for _, group in _groups(units):
        if any(u.id == uid for u in group):
            break
    else:
        return
    me = next(u for u in group if u.id == uid)
    rest = [u for u in group if u.id != uid]
    at = next((i for i, u in enumerate(rest) if order(u) > order(me)), len(rest))
    if [u.id for u in [*rest[:at], me, *rest[at:]]] == [u.id for u in group]:
        return
    if at < len(rest):
        before = rest[at].blocks[0]
    else:
        nxt = rest[-1].blocks[-1].next()
        before = nxt if nxt.isValid() else None
    move_item(doc, me.blocks, before)


def group_completed(doc):
    # the Completed items sit in the home order of the layout, so they fall into category groups
    ensure_ids(doc)
    units = _units(doc)
    waiting = [u.id for u in units if u.sec and is_todo(u.blocks[0])]
    if len(waiting) < 2:
        return
    pos = {uid: i for i, uid in enumerate(_order(units, is_sorted(doc)))}
    want = sorted(waiting, key=pos.__getitem__)
    if want != waiting:
        _arrange(doc, want, True)


def _completed_homes(doc, units):
    # {item id: ((H1 text, H2 text), nearest heading id) or None} for each item under Completed:
    # the heading group its anchors lead home to; None when it has no home
    by_id = {u.id: u for u in units}
    open_ids = {u.id for u in units if not u.sec}

    def homed(uid):
        # the anchors lead back to the open area
        seen = set()
        while uid not in seen:
            seen.add(uid)
            a = _anchor(by_id[uid].blocks[0])
            if a == TOP or a in open_ids:
                return True
            if a not in by_id:
                return False
            uid = a
        return False

    cat, h1, near, h2 = {}, "", "", ""
    for uid in _order(units, is_sorted(doc)):
        b = by_id[uid].blocks[0]
        if is_heading(b):
            lvl, text = heading_level(b), item_text(b)
            near = uid
            if lvl == 1:
                h1, h2 = text, ""
            elif lvl == 2:
                h2 = text
        elif by_id[uid].sec:
            cat[uid] = ((h1, h2), near) if homed(uid) else None
    return cat


def completed_captions(doc):
    # {block number: "H1 > H2"} for the first Completed item of each home category; an item
    # with no home has none. Display only
    if section_block(doc) is None:
        return {}
    units = _units(doc)
    cat = {uid: v and v[0] for uid, v in _completed_homes(doc, units).items()}
    out, prev = {}, None
    for u in units:
        if u.sec and is_todo(u.blocks[0]):
            now = cat[u.id]
            if now != prev and now and any(now):
                out[u.blocks[0].blockNumber()] = " › ".join(x for x in now if x)
            prev = now
    return out


def caption_diff(doc):
    caps = completed_captions(doc)
    return [
        (b, want)
        for b in iter_blocks(doc)
        if (want := caps.get(b.blockNumber(), ""))
        != b.blockFormat().stringProperty(CAPTION)
    ]


def apply_captions(changes):
    for b, want in changes:
        f = QTextBlockFormat()
        f.setProperty(CAPTION, want)
        QTextCursor(b).mergeBlockFormat(f)
        layout_block(b)


def refresh_captions(doc):
    apply_captions(caption_diff(doc))


def has_home(doc):
    # a sorted list: its open items carry home numbers
    return any(_hs(b) for b in iter_blocks(doc))


def _order(units, home=False):
    # ids in the in-place layout: the open area as it stands (in home order for a sorted list),
    # each item waiting under Completed right after its anchor, followed by its own dependants;
    # items whose anchor is gone or never recorded come last
    open_ids = _grouped(units, False) if home else [u.id for u in units if not u.sec]
    waiting = [u for u in units if u.sec and is_todo(u.blocks[0])]
    known = set(open_ids) | {u.id for u in waiting}
    kids, orphans = {}, []
    for u in waiting:
        a = _anchor(u.blocks[0])
        if a == TOP or a in known:
            kids.setdefault(a, []).append(u.id)
        else:
            orphans.append(u.id)
    out, placed = [], set()

    def grow(key):
        stack = kids.pop(key, [])[::-1]
        while stack:
            uid = stack.pop()
            if uid not in placed:
                placed.add(uid)
                out.append(uid)
                stack += kids.pop(uid, [])[::-1]

    grow(TOP)
    for uid in open_ids:
        placed.add(uid)
        out.append(uid)
        grow(uid)
    # what is left of the tree is a cycle of anchors, which no app edit produces
    for uid in [*orphans, *(u.id for u in waiting)]:
        if uid not in placed:
            placed.add(uid)
            out.append(uid)
            grow(uid)
    return out


def _record_homes(doc, ids):
    # the items about to leave their place note the item before them in the in-place layout
    order = _order(_units(doc), is_sorted(doc))
    before = dict(zip(order, [TOP, *order]))
    for u in _units(doc):
        if u.id in ids:
            set_meta(u.blocks[0], a=before[u.id])


def _restore(doc, ids):
    # the items of ids waiting under Completed go back to their place in the open area
    units, home = _units(doc), is_sorted(doc)
    open_ids = {u.id for u in units if not u.sec}
    _arrange(doc, [i for i in _order(units, home) if i in open_ids or i in ids])
    for u in _units(doc):
        if u.id in ids:
            set_meta(u.blocks[0], a="", hs=0)
    if home:
        # the layout is in home order now, so each item put back is numbered right after its anchor
        home_new(doc)
        _arrange(doc, _grouped(_units(doc), True))
    group_completed(doc)


def unit_block(doc, uid):
    return next(
        b for b in iter_blocks(doc) if kind(b) in (TODO, HEADING) and _id(b) == uid
    )


def unit_above(block):
    # the item or heading right above; None when the Completed header comes first
    b = block.previous()
    while b.isValid() and kind(b) not in (TODO, HEADING, SECTION, DIVIDER):
        b = b.previous()
    return b if b.isValid() and not is_section(b) else None


def has_stray(doc, mode):
    # an open item under Completed, or in section mode a checked one above it: reopening moves
    # it, which renumbers the blocks
    below = False
    for b in iter_blocks(doc):
        # one format read per block: this runs on every save
        f = b.blockFormat()
        k = f.stringProperty(PROPS["k"])
        if k == SECTION:
            below = True
        elif k == TODO and bool(f.stringProperty(PROPS["d"])) != below:
            if below or mode == "move":
                return True
    return False


def send_home(doc, todos, after):
    # open items made under Completed take the place they would have in place: chained one after
    # the other, right after `after`; None when there is nothing to follow
    ensure_ids(doc)
    prev = _id(after) if after is not None else ""
    ids = []
    for t in todos:
        set_meta(t, a=prev)
        prev = _id(t)
        ids.append(prev)
    _restore(doc, set(ids))
    _drop_section_if_unused(doc)
    return [unit_block(doc, uid) for uid in ids]


def paste_meta(meta):
    return {**NEW_ITEM, **meta, "i": "", "a": "", "hs": 0, "ti": "", "ta": ""}


def normalize(doc, mode):
    if mode not in MODES:
        raise ValueError(f"mode {mode!r}")
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        _normalize(doc, mode)
    finally:
        c.endEditBlock()
    apply_view(doc, mode)


def _normalize(doc, mode):
    for b in list(iter_blocks(doc)):
        layout_block(b)
        if is_todo(b) and b.textList() is not None:
            make_todo(b)
    ensure_ids(doc)
    ensure_task_ids(doc)
    units = _units(doc)
    waiting = [u for u in units if u.sec and is_todo(u.blocks[0])]
    if mode == "move":
        # ponytail: one move per item, O(items * blocks); fine for import and load-time repair
        # an unchecked item under Completed goes home; a checked one in the open area notes its place
        strays = {u.id for u in waiting if not is_checked(u.blocks[0])}
        leaving = {u.id for u in units if not u.sec and is_checked(u.blocks[0])}
        if strays:
            _restore(doc, strays)
        if leaving:
            _record_homes(doc, leaving)
        while any(is_checked(b) for b in iter_blocks(doc)):
            sec = ensure_section(doc)
            early = [
                b
                for b in iter_blocks(doc)
                if is_checked(b) and b.blockNumber() < sec.blockNumber()
            ]
            if not early:
                break
            # oldest first, each inserted right under the header, so the newest ends on top
            todo = min(early, key=lambda b: (get_meta(b)["d"], -b.blockNumber()))
            nxt = sec.next()
            move_item(doc, item_blocks(todo), nxt if nxt.isValid() else None)
        group_completed(doc)
        _drop_section_if_unused(doc)
    elif section_block(doc) is not None:
        if waiting:
            _restore(doc, {u.id for u in waiting})
        remove_block(doc, section_block(doc))
    refresh_captions(doc)


def toggle_check(doc, block, mode):
    todo = owner_todo(block)
    if todo is None:
        return None
    if mode == "move":
        check_movable(doc)
    checked = not is_checked(todo)
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        set_meta(todo, d=today() if checked else "")
        # the Done link: a checked item with a status is in a Done status, an unchecked one is not
        st = status_of(todo)
        if checked and st in STATUS_BY_KEY and not _done_status(st) and _first_done():
            set_meta(todo, st=_first_done())
        elif not checked and _done_status(st):
            set_meta(todo, st="")
        if mode == "move":
            ensure_ids(doc)
            uid = _id(todo)
            if checked:
                _record_homes(doc, {uid})
                sec = ensure_section(doc)
                nxt = sec.next()
                move_item(doc, item_blocks(todo), nxt if nxt.isValid() else None)
                group_completed(doc)
            else:
                sec = section_block(doc)
                if sec is not None and todo.blockNumber() > sec.blockNumber():
                    _restore(doc, {uid})
                _drop_section_if_unused(doc)
            refresh_captions(doc)
            todo = unit_block(doc, uid)
    finally:
        c.endEditBlock()
    apply_view(doc, mode)
    return todo


def toggle_checks(doc, todos, mode):
    # the open todos get checked; when every one is checked, all are unchecked. One edit, so one
    # undo step. Last first: each lands right under the Completed header, so they keep their order
    if mode == "move":
        check_movable(doc)
    ensure_ids(doc)
    ids = [_id(t) for t in todos]
    want = not all(is_checked(t) for t in todos)
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        for uid in reversed(ids):
            b = unit_block(doc, uid)
            if is_checked(b) != want:
                toggle_check(doc, b, mode)
    finally:
        c.endEditBlock()


def item_text(block):
    return block_plain(block).replace(" ", " ").strip()


def counts(doc):
    out = {"open": 0, "done": 0, "urgent": 0}
    for b in iter_blocks(doc):
        if is_todo(b) and item_text(b):
            if is_checked(b):
                out["done"] += 1
            else:
                out["open"] += 1
                out["urgent"] += get_meta(b)["u"]
    return out


def numbered(doc, flat=False):
    # the gutter numbering: visible todos count up, restarting at a heading or Completed; flat
    # numbers run on through headings (a timeline's After column names tasks by them)
    n, away = 0, folded_away(doc)
    for b in iter_blocks(doc):
        if not (b.isVisible() or b.blockNumber() in away):
            continue
        k = kind(b)
        if k == HEADING and flat:
            yield b, 0
            continue
        if k in (SECTION, HEADING):
            n = 0
        elif k == TODO:
            n += 1
        else:
            continue
        yield b, n


def preview_limit(settings):
    return None if settings["preview_fill"] else settings["preview_count"]


def card_summary(doc, limit=50, headings=False):
    s = counts(doc)
    num = {b.blockNumber(): n for b, n in numbered(doc)}
    open_todos = [
        b for b in iter_blocks(doc) if is_todo(b) and not is_checked(b) and item_text(b)
    ]
    rows = [(i, b, get_meta(b)) for i, b in enumerate(open_todos)]
    # the card draws as many as fit: by priority, then the newest (later in the document wins a tie)
    rows.sort(key=lambda r: (r[2]["c"], r[0]), reverse=True)
    rows.sort(key=lambda r: mark_rank(mark_of(r[2])))
    picks = rows[:limit]

    def item(r):
        b, meta = r[1], r[2]
        return {
            "text": item_text(b),
            "bg": meta["bg"],
            "fg": meta["fg"],
            "urgent": meta["u"],
            "p": mark_of(meta),
            "block": b.blockNumber(),
            # an open item hidden in a collapsed Completed has no gutter number
            "n": num.get(b.blockNumber()),
            "nt": note_link(b),
            "c": meta["c"],
        }

    if not headings:
        s["preview"] = [item(r) for r in picks]
        return s
    rank = {r[1].blockNumber(): k for k, r in enumerate(picks)}
    groups = [(None, [])]
    for b in iter_blocks(doc):
        if is_heading(b) and item_text(b):
            groups.append((b, []))
        elif is_section(b):
            groups.append((None, []))
        elif b.blockNumber() in rank:
            groups[-1][1].append(b.blockNumber())
    by_block = {r[1].blockNumber(): r for r in picks}
    s["preview"] = []
    for head, members in groups:
        if not members:
            continue
        if head is not None:
            s["preview"].append(
                {
                    "kind": "heading",
                    "level": heading_level(head),
                    "text": item_text(head),
                    "bg": "",
                    "fg": "",
                    "urgent": False,
                    "block": head.blockNumber(),
                    "n": None,
                }
            )
        s["preview"] += [item(by_block[bn]) for bn in sorted(members, key=rank.get)]
    return s


def kanban_items(doc):
    # one dict per todo, open ones in document order then the ones under Completed; group is
    # the id of the nearest heading above ("" above the first one, or for a done item whose
    # home is gone), sec whether the item sits under Completed
    ensure_ids(doc)
    units = _units(doc)
    homes = _completed_homes(doc, units)
    out, near = [], ""
    for u in units:
        b = u.blocks[0]
        if is_heading(b):
            if not u.sec:
                near = u.id
            continue
        if not is_todo(b) or not item_text(b):
            continue
        meta = get_meta(b)
        home = homes.get(u.id) if u.sec else None
        out.append(
            {
                "id": u.id,
                "block": b.blockNumber(),
                "text": item_text(b),
                "mark": mark_of(meta),
                "status": meta.get("st", ""),
                "checked": is_checked(b),
                "bg": meta["bg"],
                "fg": meta["fg"],
                "group": (home[1] if home else "") if u.sec else near,
                "sec": u.sec,
            }
        )
    return out


def kanban_heads(doc):
    # every heading of the open area in document order; hidden: inside a folded heading's group
    ensure_ids(doc)
    out = []
    for b in iter_blocks(doc):
        if is_section(b):
            break
        lvl = heading_level(b)
        if lvl:
            out.append(
                {
                    "id": _id(b),
                    "level": lvl,
                    "text": item_text(b),
                    "hc": h1_colour(b) if lvl == 1 else "",
                    "folded": is_folded(b),
                    "foldable": lvl in FOLDABLE,
                    "hidden": fold_owner(b) is not None,
                }
            )
    return out


def head_spots(doc, heads):
    # the blocks a set of headings may land directly above: a heading of their level or higher
    # outside their own groups and any fold, then the Completed header (None without one) for
    # the end of the open items
    lvl = min(heading_level(h) for h in heads)
    spans = [(h.blockNumber(), group_tail(doc, h).blockNumber()) for h in heads]
    out = []
    for b in iter_blocks(doc):
        if is_section(b):
            break
        if any(a <= b.blockNumber() <= z for a, z in spans):
            continue
        if 0 < heading_level(b) <= lvl and not spot_hidden(doc, b, lvl):
            out.append(b)
    sec = section_block(doc)
    if not spot_hidden(doc, sec, lvl):
        out.append(sec)
    return out


def kanban_spots(doc, uid):
    # the `before` ids an item or heading may land above on the board; None is the end of the
    # open area
    b = unit_block(doc, uid)
    if is_heading(b):
        return [
            None if x is None or is_section(x) else _id(x) for x in head_spots(doc, [b])
        ]
    own = {x.blockNumber() for x in item_blocks(b)}
    out = []
    for u in _units(doc):
        if u.sec:
            break
        first = u.blocks[0]
        if first.blockNumber() not in own and not spot_hidden(doc, first, 3):
            out.append(u.id)
    if not spot_hidden(doc, section_block(doc), 3):
        out.append(None)
    return out


def _board_unit(doc, uid):
    b = next(
        (x for x in iter_blocks(doc) if kind(x) in (TODO, HEADING) and _id(x) == uid),
        None,
    )
    if b is None:
        raise ValueError(f"unit {uid!r}")
    return b


def kanban_drop(doc, uid, key, before, mode):
    # a board drop as one undo step: the item takes the status `key` and lands directly above
    # the unit `before` (None: the end of the open area)
    check_movable(doc)
    if key and key not in STATUS_BY_KEY:
        raise ValueError(f"status {key!r}")
    item = _board_unit(doc, uid)
    if not is_todo(item):
        raise ValueError("only a todo moves to a status")
    if before is not None:
        target = _board_unit(doc, before)
        sec = section_block(doc)
        if sec is not None and target.blockNumber() > sec.blockNumber():
            raise ValueError("a spot under Completed")
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        sec = section_block(doc)
        if sec is not None and item.blockNumber() > sec.blockNumber():
            set_status(doc, item, "" if _done_status(key) else key, mode)
        above = _board_unit(doc, before) if before is not None else section_block(doc)
        move_item(doc, item_blocks(_board_unit(doc, uid)), above)
        group_completed(doc)
        refresh_captions(doc)
        return set_status(doc, _board_unit(doc, uid), key, mode)
    finally:
        c.endEditBlock()


def column_of(item):
    # the display rule: a configured status, else the first Done status for a checked item
    if item["status"] in STATUS_BY_KEY:
        return item["status"]
    if item["checked"]:
        return _first_done()
    return ""


def notepad_summary(doc, limit):
    rows = [
        {
            "text": item_text(b),
            "bg": "",
            "fg": "",
            "urgent": False,
            "p": "",
            "block": b.blockNumber(),
            "n": None,
        }
        for b in iter_blocks(doc)
        if item_text(b)
    ]
    return {"open": 0, "done": 0, "urgent": 0, "preview": rows[:limit]}


MONTHS = tuple("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())
DAY = datetime.timedelta(days=1)


def day_text(day):
    return f"{MONTHS[day.month - 1]} {day.day}"


def task_start(block):
    ts = block.blockFormat().stringProperty(PROPS["ts"])
    return datetime.date.fromisoformat(ts) if ts else None


def task_days(block):
    return block.blockFormat().intProperty(PROPS["tn"])


def task_end(block):
    # the day a dependent may start; a milestone ends where it starts
    start = task_start(block)
    return None if start is None else start + task_days(block) * DAY


def task_id(block):
    return block.blockFormat().stringProperty(PROPS["ti"])


def task_after(block):
    ta = block.blockFormat().stringProperty(PROPS["ta"])
    return [i for i in ta.split(",") if i]


def new_task_id(doc, text, taken=None):
    taken = {task_id(b) for b in iter_blocks(doc)} if taken is None else taken
    return make_id(text or "task", taken)


def start_task(block):
    # a todo made in a timeline starts where the nearest task above ends, else today
    b = block.previous()
    while b.isValid() and task_end(b) is None:
        b = b.previous()
    start = today() if not b.isValid() else task_end(b).isoformat()
    set_meta(
        block,
        ts=start,
        tn=1,
        tp=0,
        ti=new_task_id(block.document(), item_text(block)),
        ta="",
    )


def ensure_task_ids(doc):
    taken = {task_id(b) for b in iter_blocks(doc)}
    seen = set()
    for b in iter_blocks(doc):
        if task_start(b) is None:
            continue
        i = task_id(b)
        if not i or i in seen:
            i = new_task_id(doc, item_text(b), taken)
            taken.add(i)
            set_meta(b, ti=i)
        seen.add(i)


def timeline_rows(doc, skip_empty=False):
    # (block, row) for every heading and todo: text, level, start, days, pct, done, bar, fg. A heading's
    # row spans the dated tasks under it, up to the next heading of its level or above; a
    # milestone fills its day
    out, heads = [], []
    for b in iter_blocks(doc):
        if not (is_heading(b) or is_todo(b)) or (skip_empty and not item_text(b)):
            continue
        if is_heading(b):
            lvl = heading_level(b)
            while heads and heads[-1]["level"] >= lvl:
                heads.pop()
            row = dict(
                text=item_text(b),
                level=lvl,
                start=None,
                days=0,
                pct=0,
                done=False,
                bar="",
                fg="",
            )
            heads.append(row)
            out.append((b, row))
            continue
        done, start, days = is_checked(b), task_start(b), task_days(b)
        f = b.blockFormat()
        out.append(
            (
                b,
                dict(
                    text=item_text(b),
                    level=0,
                    start=start,
                    days=days,
                    pct=100 if done else f.intProperty(PROPS["tp"]),
                    done=done,
                    bar=f.stringProperty(PROPS["tb"]),
                    fg=f.stringProperty(PROPS["fg"]),
                ),
            )
        )
        if start is None:
            continue
        for h in heads:
            first, end = start, start + max(days, 1) * DAY
            if h["start"] is not None:
                first = min(first, h["start"])
                end = max(end, h["start"] + h["days"] * DAY)
            h["start"], h["days"] = first, (end - first).days
    return out


def timeline_summary(doc):
    s = counts(doc)
    s["preview"] = []
    folded = {b.blockNumber() for b, hidden in fold_state(doc) if hidden}
    s["bars"] = [
        r for b, r in timeline_rows(doc, True) if b.blockNumber() not in folded
    ]
    return s


def span_text(bars):
    dated = [r for r in bars if r["start"] is not None]
    if not dated:
        return ""
    first = min(r["start"] for r in dated)
    last = max(r["start"] + (max(r["days"], 1) - 1) * DAY for r in dated)
    return f"{day_text(first)} - {day_text(last)}"


def state_text(s, links=False):
    if links:
        return f"{s['open'] + s['done']} links"
    urgent = f"  {s['urgent']} urgent" if s["urgent"] else ""
    return f"{s['open']} open{urgent}  {s['done']} done"


def prep_doc(doc):
    doc.setDefaultFont(EDITOR_FONT)
    doc.setIndentWidth(INDENT_W)


BODY_STYLE = re.compile(r'<body style="[^"]*">')


def strip_body(html):
    # the body style pins the font the document had, which would freeze a later size change
    return BODY_STYLE.sub("<body>", html, count=1)


ROOT_OPEN = re.compile(
    r'<table style="-qt-table-type: root;[^"]*">\s*<tr>\s*<td style="border: none;">\s*'
)
ROOT_CLOSE = "</td></tr></table></body></html>"


def body_html(doc):
    # a mirrored document holds originals whatever the mode is now: the text saved is the
    # colours as written
    if _has_originals(doc):
        doc = restored(doc)
    html = strip_body(doc.toHtml())
    # a root frame with a margin of its own (the editor's tag gutter) is display only: Qt writes
    # it as a table around the text
    html, opened = ROOT_OPEN.subn("", html, count=1)
    if opened:
        if not html.endswith(ROOT_CLOSE):
            raise ValueError("unexpected root frame in the document html")
        html = html[: -len(ROOT_CLOSE)] + "</body></html>"
    return html


def html_text(html):
    doc = QTextDocument()
    doc.setHtml(html)
    return doc.toPlainText()


def image_order(doc):
    names = []
    for b in iter_blocks(doc):
        it = b.begin()
        while not it.atEnd():
            f = it.fragment().charFormat()
            if f.isImageFormat():
                names.append(f.toImageFormat().name())
            it += 1
    return names


def image_names(doc):
    return set(image_order(doc))


def item_images(todo):
    # (position, length, name) of every image in the blocks of the item
    out = []
    for b in item_blocks(todo):
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            it += 1
            f = fr.charFormat()
            if f.isImageFormat():
                out.append((fr.position(), fr.length(), f.toImageFormat().name()))
    return out


def attach_images(doc, store):
    # images live in the .rdb, so the document gets them as resources instead of by path
    for name in image_names(doc):
        doc.addResource(
            QTextDocument.ResourceType.ImageResource,
            QUrl(name),
            QImage.fromData(store.get_image(name)),
        )


def save_project(store, pid, doc):
    meta = [get_meta(b) for b in iter_blocks(doc)]
    for b, row in zip(iter_blocks(doc), meta):
        if is_folded(b):
            row["f"] = True
        # a link to a note document that is gone is cleared
        if row.get("nt") and not store.has_project(row["nt"]):
            del row["nt"]
            row.pop("no", None)
    store.put_project(pid, body_html(doc), meta, is_collapsed(doc))
    if is_note_pid(pid):
        PREVIEWS[pid] = _preview(doc.toPlainText())


def load_project(store, pid, doc):
    data = store.get_project(pid)
    # files saved with the body style pin the editor font of that day onto every character
    doc.setHtml(strip_body(data["html"]))
    # spans of the same pinning, and fonts pasted in, would override the List font settings
    if not is_text_doc(doc):
        whole = QTextCursor(doc)
        whole.select(QTextCursor.SelectionType.Document)
        char_normalize(whole)
    if doc.blockCount() != len(data["meta"]):
        raise ValueError(
            f"{store.path} project {pid}: {doc.blockCount()} blocks, {len(data['meta'])} meta rows"
        )
    folds = []
    for b, m in zip(list(iter_blocks(doc)), data["meta"]):
        if m.pop("f", False):
            folds.append(m["i"])
        set_meta(b, **m)
    doc.setProperty("rdb_folds", folds)
    # a table saved before pastes were flattened; its cells are blocks with meta rows of their own
    flatten_doc(doc)
    mirror_range(doc, 0, doc.characterCount())
    set_collapsed(doc, data["collapsed"])
    attach_images(doc, store)


def _note_document(store, blocks, taken):
    # a new note document holding the text of these blocks
    tmp = QTextDocument()
    prep_doc(tmp)
    c = QTextCursor(tmp)
    for i, b in enumerate(blocks):
        if i:
            c.insertBlock()
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            f = fr.charFormat()
            if f.isImageFormat():
                c.insertImage(f.toImageFormat())
            else:
                c.insertText(fr.text(), f)
            it += 1
        old = get_meta(b)
        nb = c.block()
        set_meta(
            nb, **{**plain_meta(), "c": old["c"], "fg": old["fg"], "bg": old["bg"]}
        )
        layout_block(nb)
    pid = new_note_pid(store, taken)
    save_project(store, pid, tmp)
    return pid


def migrate_notes(store, doc):
    # the inline note blocks of the old format move into a note document per todo; one that
    # follows no todo becomes a plain block. True when the list changed
    groups, strays, owner = {}, [], None
    for b in iter_blocks(doc):
        k = kind(b)
        if k == TODO:
            owner = b.blockNumber()
        elif k in (HEADING, SECTION, DIVIDER):
            owner = None
        elif k == LEGACY_NOTE:
            if owner is None:
                strays.append(b)
            else:
                groups.setdefault(owner, []).append(b.blockNumber())
    if not groups and not strays:
        return False
    taken = _taken(store)
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        for b in strays:
            set_meta(b, k="")
            layout_block(b)
        # from the bottom up, so the block numbers still to be used stay valid
        for n in sorted(groups, reverse=True):
            nums = groups[n]
            pid = _note_document(store, [doc.findBlockByNumber(x) for x in nums], taken)
            set_meta(doc.findBlockByNumber(n), nt=pid)
            for x in reversed(nums):
                remove_block(doc, doc.findBlockByNumber(x))
    finally:
        c.endEditBlock()
    return True


def resolve_notes(store, doc):
    # a link to a note document in the trash brings it back; one that is gone shows as no note
    # (the stored link is cleared with the next save); an open preview reads its lines
    for b in iter_blocks(doc):
        nt = note_link(b)
        if not nt:
            continue
        if not store.has_project(nt) and store.in_trash(nt):
            store.restore_project(nt)
        if not store.has_project(nt):
            set_meta(b, nt="", no=False)
        elif note_open(b):
            load_preview(store, nt)


def open_doc(store, pid, mode, text=False, readonly=False):
    # readonly loads the stored html as it is: no note migration, no restore from the trash,
    # no save, so the store is not written
    doc = QTextDocument()
    prep_doc(doc)
    doc.setProperty("rdb_note", is_note_pid(pid))
    # set before loading: a text page keeps the fonts its toolbar set
    doc.setProperty("rdb_text", text)
    moved = False
    if store.has_project(pid):
        load_project(store, pid, doc)
        if not readonly:
            moved = migrate_notes(store, doc)
            resolve_notes(store, doc)
    else:
        set_meta(doc.begin(), **todo_meta())
    # the home numbers say the list is sorted, and normalize orders Completed by home order
    set_sorted(doc, has_home(doc))
    normalize(doc, mode)
    if moved:
        # the note documents exist now: the list that names them must not be left as it was
        save_project(store, pid, doc)
    doc.clearUndoRedoStacks()
    doc.setModified(False)
    return doc


def plain_page(doc, fresh):
    # a text page: its blocks are not todos, and a fresh one starts with one plain block
    doc.setProperty("rdb_note", True)
    doc.setProperty("rdb_text", True)
    doc.setDefaultFont(note_font(EDITOR_FONT))
    modified = doc.isModified()
    if fresh:
        set_meta(doc.begin(), **plain_meta())
    for b in iter_blocks(doc):
        layout_block(b)
    doc.clearUndoRedoStacks()
    doc.setModified(modified)


def _url_fragments(doc, start, end):
    for b in iter_blocks(doc):
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            f = fr.charFormat()
            href, text = f.anchorHref(), fr.text().strip()
            if (
                f.isAnchor()
                and href
                and fr.position() < end
                and fr.position() + fr.length() > start
                and (text.startswith(("http://", "https://")) or text == href)
            ):
                yield fr.position(), fr.length(), f
            it += 1


def url_links(doc, start, end):
    hrefs = (f.anchorHref() for _, _, f in _url_fragments(doc, start, end))
    return list(dict.fromkeys(hrefs))


def _pad_offsets(text):
    # where each path chip starts in a path link's text, in the UTF-16 units of document positions
    out, off = [], 0
    for ch in text:
        if ch == FILE_PAD:
            out.append(off)
        off += 2 if ord(ch) > 0xFFFF else 1
    return out


def link_runs(block):
    # (start, end, href) of each link in a block; each path chip is a run of its own
    runs = []
    it = block.begin()
    while not it.atEnd():
        fr = it.fragment()
        f = fr.charFormat()
        it += 1
        href = f.anchorHref()
        if not (f.isAnchor() and href):
            continue
        a = fr.position()
        cuts = _pad_offsets(fr.text()) if is_path_link(href) else []
        marks = sorted({0, *cuts})
        for s, e in zip(marks, marks[1:] + [fr.length()]):
            if runs and runs[-1][1] == a + s and runs[-1][2] == href and s not in cuts:
                runs[-1] = (runs[-1][0], a + e, href)
            else:
                runs.append((a + s, a + e, href))
    return runs


def link_span(block, pos):
    # (start, end, href) of the run of one link, stored or detected, touching document position
    # pos, else None
    return next((r for r in block_links(block) if r[0] <= pos <= r[1]), None)


def detected_links(block):
    # (start, end, href) of each address in the text that no stored anchor overlaps, for item and
    # note rows only; display-time, nothing is written to the document
    if is_heading(block) or is_section(block) or is_divider(block):
        return []
    text, base, stored = block.text(), block.position(), link_runs(block)
    out = []
    for a, z, href in auto_links(text):
        s = base + _utf16(text[:a])
        e = s + _utf16(text[a:z])
        if not any(ss < e and s < se for ss, se, _ in stored):
            out.append((s, e, href))
    return out


def block_links(block):
    # (start, end, href) of every link in a block, stored or detected, by start
    return sorted(link_runs(block) + detected_links(block))


def chip_runs(block):
    # only a label starting with the pad is a chip: a pasted file: link can wrap whole sentences,
    # and a selection snaps to a chip's ends
    return [
        (s, e)
        for s, e, href in link_runs(block)
        if is_path_link(href) and run_text(block, s, e).startswith(FILE_PAD)
    ]


def chip_span(doc, pos):
    # (start, end) of the path chip holding the character at pos, else None
    return next((r for r in chip_runs(doc.findBlock(pos)) if r[0] <= pos < r[1]), None)


# a tag opens the text or follows a space or an opening bracket or quote, so a#b and a URL's
# #fragment are left alone; a name needs a letter, so #1 and #2024 stay plain numbers
TAG_RX = re.compile(r"(?<![^\s(\[{\"'])#(?=[\w-]*[^\W\d])\w+(?:-\w+)*")


def _utf16(s):
    return len(s.encode("utf-16-le")) // 2


def run_text(block, a, e):
    # the text of a (start, end) document-position run: positions count UTF-16 units
    u = block.text().encode("utf-16-le")
    p = block.position()
    return u[2 * (a - p) : 2 * (e - p)].decode("utf-16-le")


def tag_runs(block):
    # (start, end) document positions of each #tag in a block outside its links
    text, links = block.text(), block_links(block)
    out = []
    for mt in TAG_RX.finditer(text):
        a = block.position() + _utf16(text[: mt.start()])
        b = a + _utf16(mt.group())
        if not any(s < b and a < e for s, e, _ in links):
            out.append((a, b))
    return out


def tag_name_ok(name):
    return TAG_RX.fullmatch("#" + name) is not None


def fuzzy_tags(tags, partial):
    # prefix first, then substring, then scattered characters; tighter and shorter first
    out = []
    for t in tags:
        if t.startswith(partial):
            out.append((0, 0, len(t), t))
        elif partial in t:
            out.append((1, 0, len(t), t))
        else:
            at = t.find(partial[0])
            end = at
            for ch in partial:
                end = t.find(ch, end)
                if end < 0:
                    break
                end += 1
            else:
                out.append((2, end - at, len(t), t))
    return [r[3] for r in sorted(out)]


def rename_tag(doc, old, new):
    # #old becomes #new in every item outside links, as one edit; returns the count
    if not tag_name_ok(new):
        raise ValueError(f"{new!r}: not a tag name")
    runs = _tag_runs_named(doc, old)
    if not runs:
        return 0
    c = QTextCursor(doc)
    c.beginEditBlock()
    for a, e in reversed(runs):
        c.setPosition(a)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        c.insertText("#" + new)
    c.endEditBlock()
    return len(runs)


def _tag_runs_named(doc, old):
    key = old.lower()
    return [
        (a, e)
        for b in iter_blocks(doc)
        if not is_heading(b)
        for a, e in tag_runs(b)
        if run_text(b, a, e).lower() == key
    ]


def delete_tag(doc, old):
    # #old loses its # in every item and stays as text, as one edit; returns the count
    runs = _tag_runs_named(doc, "#" + old)
    if not runs:
        return 0
    c = QTextCursor(doc)
    c.beginEditBlock()
    for a, _ in reversed(runs):
        c.setPosition(a)
        c.setPosition(a + 1, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
    c.endEditBlock()
    return len(runs)


def retag(tags, old, new):
    # a card's tag names with old swapped for new (dropped when new is None), repeats folded
    out = []
    for t in tags:
        if t.lower() == old.lower():
            t = new
        if t is not None and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out


def _project_docs(store, live):
    # every project's document; the open one is the live document itself, so its edits keep
    # their undo
    for pid in store.project_ids():
        if live is not None and live[0] == pid:
            yield pid, live[1]
            continue
        doc = QTextDocument()
        prep_doc(doc)
        doc.setProperty("rdb_note", is_note_pid(pid))
        load_project(store, pid, doc)
        yield pid, doc


def _card_maps(store):
    return [(None, load_map(store))] + [
        (b, load_board(store, b)) for b in store.board_ids()
    ]


def tag_table(store, live=None):
    # (name, uses) of every tag in the database, one per case-insensitive name, sorted
    found = {}

    def add(name):
        row = found.setdefault(name.lower(), [name, 0])
        row[1] += 1

    for _, doc in _project_docs(store, live):
        for b in iter_blocks(doc):
            if not is_heading(b):
                for a, e in tag_runs(b):
                    add(run_text(b, a, e)[1:])
    for _, mp in _card_maps(store):
        for c in mp["cards"]:
            for t in c["tags"]:
                add(t)
    return [tuple(r) for r in sorted(found.values(), key=lambda r: r[0].lower())]


def _edit_tags(store, live, doc_fn, card_fn):
    changed = set()
    for pid, doc in _project_docs(store, live):
        if doc_fn(doc):
            save_project(store, pid, doc)
            changed.add(pid)
    for board, mp in _card_maps(store):
        before = [list(c["tags"]) for c in mp["cards"]]
        for c in mp["cards"]:
            c["tags"] = card_fn(c["tags"])
        if before != [c["tags"] for c in mp["cards"]]:
            if board is None:
                save_map(store, mp)
            else:
                save_board(store, board, mp)
    return changed


def rename_tag_all(store, live, old, new):
    # old becomes new in every project, note and card; onto an existing tag it takes that tag's
    # spelling, so the two merge. Returns the pids of the projects changed
    if not tag_name_ok(new):
        raise ValueError(f"{new!r}: not a tag name")
    if new.lower() != old.lower():
        new = dict((n.lower(), n) for n, _ in tag_table(store, live)).get(
            new.lower(), new
        )
    return _edit_tags(
        store,
        live,
        lambda doc: rename_tag(doc, "#" + old, new),
        lambda tags: retag(tags, old, new),
    )


def delete_tags_all(store, live, names):
    def strip(doc):
        return sum([delete_tag(doc, n) for n in names])

    def drop(tags):
        for n in names:
            tags = retag(tags, n, None)
        return tags

    return _edit_tags(store, live, strip, drop)


def tag_span(doc, pos):
    # (start, end) of the #tag holding the character at pos, else None
    return next((r for r in tag_runs(doc.findBlock(pos)) if r[0] <= pos < r[1]), None)


def lone_tag(block):
    # (start, end) of the only #tag in the item the block belongs to, over all its blocks
    if owner_todo(block) is None:
        return None
    runs = [r for b in item_of(block) for r in tag_runs(b)]
    return runs[0] if len(runs) == 1 else None


def tag_reveal(block, run, lone):
    # the caret range that shows a tag's '#'; a lone tag hides with one adjacent space, the
    # following one else the preceding one, and the range covers it
    a, b = run
    if run != lone:
        return a, b
    if b < block.position() + block.length() - 1 and run_text(block, b, b + 1) == " ":
        return a, b + 1
    if a > block.position() and run_text(block, a - 1, a) == " ":
        return a - 1, b
    return a, b


def tag_spots(block):
    # document positions where a dragged #tag may land: the word boundaries and ends of a visible
    # item or note line, never inside a link or chip
    if (
        not block.isVisible()
        or is_heading(block)
        or is_section(block)
        or is_divider(block)
    ):
        return []
    text, base = block.text(), block.position()
    links = block_links(block)
    out, pos = [], base
    for i in range(len(text) + 1):
        edge = i in (0, len(text)) or (text[i - 1] in " \t") != (text[i] in " \t")
        if edge and not any(s < pos < e for s, e, _ in links):
            out.append(pos)
        if i < len(text):
            pos += _utf16(text[i])
    return out


def _blanks(doc, a, b):
    # the extent of the spaces and tabs around a..b, kept inside its block
    block = doc.findBlock(a)
    lo, hi = block.position(), block.position() + block.length() - 1
    while a > lo and doc.characterAt(a - 1) in " \t":
        a -= 1
    while b < hi and doc.characterAt(b) in " \t":
        b += 1
    return a, b, lo, hi


def pill_runs(block):
    # (start, end) of each draggable pill in a block: #tags, path chips, image chips
    it, imgs = block.begin(), []
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        if fr.charFormat().isImageFormat():
            imgs += [
                (p, p + 1) for p in range(fr.position(), fr.position() + fr.length())
            ]
    return sorted(tag_runs(block) + chip_runs(block) + imgs)


def pill_home(doc, a, b):
    # (lo, hi, d0, d1): the landing spots that leave the pill at a..b where it is, and the
    # characters a move takes away, the pill and the one blank its neighbours do not close up on
    lft, r, lo, hi = _blanks(doc, a, b)
    if lft > lo and r < hi:
        d0, d1 = (lft + 1, r) if a > lft else (a, r - 1 if r > b else b)
    else:
        d0, d1 = lft, r
    return lft, r, d0, d1


def move_pill(doc, a, b, p):
    # the pill at a..b moves to landing spot p as one edit, with one space between it and its
    # neighbours and none at a line's start or end; returns where it now starts, None when p is
    # the place it already holds
    if (a, b) not in pill_runs(doc.findBlock(a)):
        raise ValueError(f"{a}..{b}: not a pill")
    if p not in tag_spots(doc.findBlock(p)):
        raise ValueError(f"{p}: not a landing spot")
    lft, r, d0, d1 = pill_home(doc, a, b)
    if lft <= p <= r:
        return None
    c = QTextCursor(doc)
    c.setPosition(a)
    c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
    frag = c.selection()
    c.setPosition(a + 1)
    # a chip's own format would make the spaces part of its link or image
    fmt = c.charFormat() if (a, b) in tag_runs(doc.findBlock(a)) else QTextCharFormat()
    c.beginEditBlock()
    try:
        c.setPosition(d0)
        c.setPosition(d1, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        if p >= d1:
            p -= d1 - d0
        lft, r, lo, hi = _blanks(doc, p, p)
        left, right = lft > lo, r < hi
        c.setPosition(lft)
        c.setPosition(r, QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        if left:
            c.insertText(" ", fmt)
        start = c.position()
        c.insertFragment(frag)
        if right:
            c.insertText(" ", fmt)
    finally:
        c.endEditBlock()
    return start


def chip_widen(doc, lo, hi):
    # the range lo..hi grown to take whole every chip it cuts into
    if lo == hi:
        return lo, hi
    first, last = chip_span(doc, lo), chip_span(doc, hi - 1)
    return first[0] if first else lo, last[1] if last else hi


def link_format(url):
    f = QTextCharFormat()
    f.setAnchor(True)
    f.setAnchorHref(url)
    f.setFontUnderline(True)
    f.setForeground(QGuiApplication.palette().link())
    return f


def append_link(doc, url, mode):
    c = QTextCursor(doc)
    c.movePosition(QTextCursor.MoveOperation.End)
    last = c.block()
    if not (is_todo(last) and not is_checked(last) and not item_text(last)):
        c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        set_meta(c.block(), **todo_meta())
    c.insertText(url, link_format(url))
    normalize(doc, mode)


def relabel_links(doc, url, title):
    title = " ".join(title.split())
    if not title or title == url:
        return 0
    hits = [
        h
        for h in _url_fragments(doc, 0, doc.characterCount())
        if h[2].anchorHref() == url
    ]
    c = QTextCursor(doc)
    c.beginEditBlock()
    try:
        # back to front: each replacement shifts only the positions after it
        for pos, n, fmt in reversed(hits):
            c.setPosition(pos)
            c.setPosition(pos + n, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(title, fmt)
    finally:
        c.endEditBlock()
    return len(hits)


# a path link is a plain anchor with a file: href; its label starts with FILE_PAD, the room the
# editor draws the bubble's folder or file glyph in
FILE_PAD = "\u00a0"
_URL = re.compile(r"https?://\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WWW = re.compile(r"www\.[\w-]+(?:\.[\w-]+)+\S*", re.I)
# a token starts the text or follows whitespace or an opening quote or bracket; an apostrophe
# after a letter is part of a name
_AUTO = re.compile(
    rf"(?<![^\s(\[{{\"'<])(?<!\w')(?:{_URL.pattern}|{_WWW.pattern}|{EMAIL.pattern})",
    re.I,
)
_AUTO_TAIL = ".,;:!?\"'>"
_AUTO_PAIRS = {")": "(", "]": "[", "}": "{"}
_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/]")


def auto_links(text):
    # (start, end, href) of each web address, www address and email address in text
    out = []
    for hit in _AUTO.finditer(text):
        tok = hit.group()
        while tok:
            if tok[-1] in _AUTO_TAIL or (
                tok[-1] in _AUTO_PAIRS
                and tok.count(tok[-1]) > tok.count(_AUTO_PAIRS[tok[-1]])
            ):
                tok = tok[:-1]
            else:
                break
        if re.fullmatch(_URL.pattern, tok, re.I):
            href = tok
        elif _WWW.fullmatch(tok):
            href = "https://" + tok
        elif EMAIL.fullmatch(tok):
            href = "mailto:" + tok
        else:
            continue
        out.append((hit.start(), hit.start() + len(tok), href))
    return out


def url_text(text):
    # the text when it is one http(s) URL and nothing else, else ""
    text = text.strip()
    return text if _URL.fullmatch(text) else ""


def path_href(text):
    # the file: URL of a local path or file URL written as one line, else ""
    text = text.strip()
    if len(text.splitlines()) != 1:
        return ""
    if text.lower().startswith("file:"):
        return text if QUrl(text).toLocalFile() else ""
    return QUrl.fromLocalFile(text).toString() if _PATH.match(text) else ""


def path_local(href):
    return QUrl(href).toLocalFile().replace("/", "\\")


def pasted_path_href(text):
    # a pasted line is a path only when all of it is: whitespace in the last component means
    # a sentence that starts with a path, unless that exact path exists
    href = path_href(text)
    local = path_local(href) if href else ""
    if re.search(r"\s", local.rsplit("\\", 1)[-1]) and not Path(local).exists():
        return ""
    return href


def path_label(local):
    return PureWindowsPath(local).name or local


def is_path_link(href):
    return href.lower().startswith("file:")


def frag_plain(fr):
    # the label of a path link without the room its glyph takes
    text = fr.text()
    if text.startswith(FILE_PAD) and is_path_link(fr.charFormat().anchorHref()):
        return text[len(FILE_PAD) :]
    return text


def block_plain(block):
    out, it = [], block.begin()
    while not it.atEnd():
        out.append(frag_plain(it.fragment()))
        it += 1
    return "".join(out)


def fragment_plain(frag):
    tmp = QTextDocument()
    QTextCursor(tmp).insertFragment(frag)
    return "\n".join(block_plain(b) for b in iter_blocks(tmp))


def path_open_url(href, folder):
    # what opening a path link asks the desktop for: the path, or with folder the one holding it
    local = Path(path_local(href))
    return QUrl.fromLocalFile(str(local.parent if folder else local))


def insert_link(cursor, label, url):
    # the link replaces the cursor's selection; a path becomes a file: link
    url = path_href(url) or url.strip()
    label = label.strip() or url
    cursor.insertText((FILE_PAD if is_path_link(url) else "") + label, link_format(url))
