from collections import namedtuple

# Grammar of a chord: modifiers joined by "+" ahead of one key, in the order Ctrl, Alt, Shift
# (Qt's spelling of the key: Esc, Del, PgUp, PgDown, Enter, Space, Tab, Left ...). A space puts
# several chords side by side: in a Vim section they are typed in turn, anywhere else they are
# separate keys. A key may be a pseudo key: Arrows, HJKL, or a digit range such as 0-4.
# sources: the modules whose key handling the section's rows come from; the sandbox test checks
# each chord against them and against the live bindings.
Section = namedtuple("Section", "title sources rows")

ARROWS = ("Left", "Right", "Up", "Down")
PSEUDO = {"Arrows": ARROWS, "HJKL": ("H", "J", "K", "L")}
MODS = ("Ctrl", "Alt", "Shift")


def keys(key):
    if key in PSEUDO:
        return PSEUDO[key]
    if len(key) == 3 and key[1] == "-" and key[0].isdigit() and key[2].isdigit():
        return tuple(str(d) for d in range(int(key[0]), int(key[2]) + 1))
    return (key,)


def parts(chord):
    # [(modifiers, key)] for each chord of a space-separated group, pseudo keys left whole
    out = []
    for c in chord.split(" "):
        bits = c.split("+")
        # a trailing "+" would be the key itself
        if len(bits) > 1 and bits[-1] == "":
            raise ValueError(f"chord {chord!r} ends in +")
        mods = tuple(b for b in bits[:-1] if b in MODS)
        if mods != tuple(bits[:-1]):
            raise ValueError(f"chord {chord!r} has an unknown modifier")
        out.append((mods, bits[-1]))
    return out


def singles(chord):
    # every single-key chord a chord stands for, as "Ctrl+Shift+Left"
    return ["+".join((*mods, k)) for mods, key in parts(chord) for k in keys(key)]


SECTIONS = (
    Section(
        "Global",
        ("rdb.py",),
        (
            ("Find", ("Ctrl+P", "Ctrl+Space")),
            ("Find today's items", ("Ctrl+T",)),
            ("Quick add", ("Ctrl+Q",)),
            ("Quick add to holding", ("Ctrl+Shift+Q",)),
            ("Back", ("Alt+Left", "Ctrl+Left")),
            ("Forward", ("Alt+Right", "Ctrl+Right")),
            ("Go to map root", ("Ctrl+H",)),
            ("Claude bar", ("Ctrl+Shift+Space",)),
            ("Triage", ("Ctrl+Shift+T",)),
            ("Find in page", ("Ctrl+F",)),
            ("Next match", ("F3",)),
            ("Previous match", ("Shift+F3",)),
            ("Web view beside", ("Ctrl+Shift+Right",)),
            ("Web view below", ("Ctrl+Shift+Down",)),
            ("File menu", ("Alt+F",)),
            ("New file", ("Ctrl+Shift+N",)),
            ("Open file", ("Ctrl+O",)),
            ("Save as", ("Ctrl+Shift+S",)),
            ("Settings", ("Ctrl+,",)),
            ("Keyboard shortcuts", ("F1",)),
        ),
    ),
    Section(
        "Map / Boards",
        ("rdb_map.py", "rdb.py"),
        (
            ("Select neighbour", ("Arrows", "HJKL")),
            ("Move cards one cell", ("Ctrl+Arrows", "Shift+HJKL")),
            ("Open, edit or insert", ("Enter",)),
            ("Rename card", ("F2",)),
            ("New text block", ("T",)),
            ("Quick add", ("Q",)),
            ("New card", ("Ctrl+N",)),
            ("Group cards", ("Ctrl+G",)),
            ("Fit all", ("F",)),
            ("Style menu", ("`",)),
            ("Cut", ("Ctrl+X",)),
            ("Copy", ("Ctrl+C",)),
            ("Paste", ("Ctrl+V",)),
            ("Delete selection", ("Del",)),
            ("Undo", ("Ctrl+Z",)),
            ("Redo", ("Ctrl+Y", "Ctrl+Shift+Z")),
            ("Up one board", ("Esc",)),
            ("Back", ("Backspace",)),
        ),
    ),
    Section(
        "Text blocks",
        ("rdb_map.py",),
        (
            ("Bold", ("Ctrl+B",)),
            ("Italic", ("Ctrl+I",)),
            ("Underline", ("Ctrl+U",)),
            ("Centre", ("Ctrl+E",)),
            ("Align left", ("Ctrl+L",)),
            ("Align right", ("Ctrl+R",)),
            ("Justify", ("Ctrl+J",)),
            ("Style menu", ("`",)),
            ("Finish text", ("Esc", "Ctrl+Enter")),
        ),
    ),
    Section(
        "Editor: items",
        ("rdb_editor.py",),
        (
            ("New item", ("Enter",)),
            ("New item below", ("Ctrl+N",)),
            ("Insert divider", ("Ctrl+Shift+-",)),
            ("Line break in item", ("Shift+Enter", "Ctrl+Enter")),
            ("Toggle done", ("Ctrl+D", "Ctrl+-")),
            ("Cut item", ("Ctrl+X",)),
            ("Copy item", ("Ctrl+C",)),
            ("Toggle completed", ("Ctrl+Shift+D",)),
            ("Kanban view", ("Ctrl+Shift+K",)),
            ("Edit link", ("Ctrl+K",)),
            ("Open note page", ("Ctrl+=",)),
            ("Toggle note preview (normal mode)", ("=",)),
            ("Item colours", ("`",)),
            ("Set mark P1 to P4", ("Alt+1-4",)),
            ("Clear mark", ("Alt+0",)),
            ("Indent", ("Tab",)),
            ("Outdent", ("Shift+Tab",)),
            ("Undo", ("Ctrl+Z",)),
            ("Redo", ("Ctrl+Y",)),
            ("Back to map", ("Esc",)),
        ),
    ),
    Section(
        "Editor: formatting",
        ("rdb_editor.py",),
        (
            ("Bold", ("Ctrl+B",)),
            ("Italic", ("Ctrl+I",)),
            ("Underline", ("Ctrl+U",)),
            ("Grey text", ("Ctrl+Shift+G",)),
            ("Red text", ("Ctrl+Shift+R",)),
            ("Cyan highlight", ("Ctrl+4",)),
            ("Numbered list", ("Ctrl+Shift+N",)),
            ("Bulleted list", ("Ctrl+Shift+L",)),
        ),
    ),
    Section(
        "Editor: headings and folds",
        ("rdb_editor.py",),
        (
            ("Normal text", ("Ctrl+0",)),
            ("Heading 1 to 3", ("Ctrl+1-3",)),
            ("Fold heading", ("Ctrl+Enter",)),
            ("Fold all headings", ("Ctrl+Shift+Enter",)),
        ),
    ),
    Section(
        "Editor: moving",
        ("rdb_editor.py",),
        (
            ("Move item up", ("Ctrl+Up",)),
            ("Move item down", ("Ctrl+Down",)),
        ),
    ),
    Section(
        "Timeline",
        ("rdb_timeline.py", "rdb_editor.py"),
        (
            ("Move bar earlier", ("Shift+Left", "Ctrl+Alt+Left")),
            ("Move bar later", ("Shift+Right", "Ctrl+Alt+Right")),
            ("Shorten bar", ("Ctrl+Left", "Ctrl+Alt+Shift+Left")),
            ("Lengthen bar", ("Ctrl+Right", "Ctrl+Alt+Shift+Right")),
            ("Edit fields", ("F2",)),
            ("Scroll to today", ("T",)),
            ("Copy as EMF", ("Ctrl+Shift+C",)),
            ("Open calendar", ("F4", "Alt+Down")),
        ),
    ),
    Section(
        "Finder (Ctrl+Space)",
        ("rdb_finder.py",),
        (
            ("Open result", ("Enter",)),
            ("Next pane", ("Tab",)),
            ("Previous pane", ("Shift+Tab",)),
            ("Move selection", ("Up Down",)),
            ("First or last", ("Home End",)),
            ("Scroll page", ("PgUp PgDown",)),
            ("Whole word", ("Alt+W",)),
            ("Completed", ("Alt+C",)),
            ("List from results", ("Ctrl+Shift+N",)),
            ("Close", ("Esc",)),
        ),
    ),
    Section(
        "Quick add",
        ("rdb_quick.py",),
        (
            ("Add item", ("Enter",)),
            ("Move selection", ("Up Down",)),
            ("Collapse or expand", ("Left Right",)),
            ("Scroll page", ("PgUp PgDown",)),
            ("First or last", ("Home End",)),
            ("Set mark", ("0-4",)),
            ("Toggle urgent", ("U",)),
            ("Open calendar", ("F4", "Alt+Down")),
        ),
    ),
    Section(
        "Contacts",
        ("rdb_contacts.py",),
        (
            ("Edit cell", ("Enter",)),
            ("New contact", ("Ctrl+N",)),
            ("Delete rows", ("Del",)),
            ("Filter", ("Ctrl+F",)),
            ("Undo", ("Ctrl+Z",)),
            ("Redo", ("Ctrl+Y", "Ctrl+Shift+Z")),
            ("Back to map", ("Esc",)),
        ),
    ),
    Section(
        "Links",
        ("rdb_links.py", "rdb_editor.py"),
        (
            ("Open link", ("Enter",)),
            ("Toggle cards view", ("Ctrl+Shift+V",)),
            ("Delete links", ("Del",)),
            ("Filter", ("Ctrl+L",)),
            ("Undo", ("Ctrl+Z",)),
            ("Redo", ("Ctrl+Y",)),
            ("Back to map", ("Esc",)),
        ),
    ),
    Section(
        "Triage",
        ("rdb_triage.py",),
        (
            ("Next item", ("Space", "Right")),
            ("Previous item", ("Left",)),
            ("Move to list", ("M",)),
            ("Make urgent", ("U",)),
            ("Pick date", ("D",)),
            ("Delete item", ("X",)),
            ("Undo", ("Ctrl+Z",)),
            ("Redo", ("Ctrl+Y", "Ctrl+Shift+Z")),
            ("Leave triage", ("Esc",)),
        ),
    ),
    Section(
        "Brain dump",
        ("rdb_dump.py",),
        (
            ("Paste clipboard", ("Ctrl+Shift+V",)),
            ("Snip screen", ("Ctrl+Shift+S",)),
            ("Close", ("Esc",)),
        ),
    ),
    Section(
        "Popups",
        ("rdb_editor.py",),
        (
            ("Move", ("Arrows", "HJKL", "Tab")),
            ("Move back", ("Shift+Tab",)),
            ("Apply", ("Enter", "Space")),
            ("Move to list", ("M",)),
            ("Bold", ("B",)),
            ("Italic", ("I",)),
            ("Underline", ("U",)),
            ("Strike", ("S",)),
            ("Normalise", ("N",)),
            ("Heading level", ("0-3",)),
            ("Set mark", ("0-4",)),
            ("Set status", ("0-9",)),
            ("Close", ("Esc",)),
        ),
    ),
    Section(
        "Find bar",
        ("rdb_findbar.py",),
        (
            ("Next match", ("Enter",)),
            ("Previous match", ("Shift+Enter",)),
            ("Close", ("Esc",)),
        ),
    ),
    Section(
        "Claude bar",
        ("rdb_claude.py",),
        (
            ("Send", ("Enter",)),
            ("Close", ("Esc",)),
        ),
    ),
    Section(
        "Vim: motions",
        ("rdb_vim.py",),
        (
            ("Left", ("h", "Left")),
            ("Right", ("l", "Right")),
            ("Down", ("j", "Down")),
            ("Up", ("k", "Up")),
            ("Word forward", ("w",)),
            ("WORD forward", ("W",)),
            ("Word end", ("e",)),
            ("WORD end", ("E",)),
            ("Word back", ("b",)),
            ("WORD back", ("B",)),
            ("Previous word end", ("g e",)),
            ("Previous WORD end", ("g E",)),
            ("Line start", ("0", "Home")),
            ("First non-blank", ("^",)),
            ("Line end", ("$", "End")),
            ("Last non-blank", ("g _",)),
            ("First line", ("g g",)),
            ("Last line", ("G",)),
            ("Find char", ("f",)),
            ("Find char back", ("F",)),
            ("Till char", ("t",)),
            ("Till char back", ("T",)),
            ("Repeat find", (";",)),
            ("Repeat find reversed", (",",)),
            ("Matching bracket", ("%",)),
            ("Paragraph back", ("{",)),
            ("Paragraph forward", ("}",)),
            ("Screen top", ("H",)),
            ("Screen middle", ("M",)),
            ("Screen bottom", ("L",)),
            ("Jump to mark", ("'",)),
            ("Next match", ("n",)),
            ("Previous match", ("N",)),
            ("Search word forward", ("*",)),
            ("Search word back", ("#",)),
            ("Half page down", ("Ctrl+D",)),
            ("Page down", ("Ctrl+F", "PgDown")),
            ("Page up", ("Ctrl+B", "PgUp")),
            ("Search forward", ("/",)),
            ("Search backward", ("?",)),
        ),
    ),
    Section(
        "Vim: commands",
        ("rdb_vim.py",),
        (
            ("Normal mode", ("Esc",)),
            ("Insert", ("i",)),
            ("Append", ("a",)),
            ("Insert at line start", ("I",)),
            ("Append at line end", ("A",)),
            ("Open line below", ("o",)),
            ("Open line above", ("O",)),
            ("Replace mode", ("R",)),
            ("Replace char", ("r",)),
            ("Insert at last spot", ("g i",)),
            ("Delete char", ("x",)),
            ("Delete char before", ("X",)),
            ("Substitute char", ("s",)),
            ("Substitute line", ("S",)),
            ("Delete to line end", ("D",)),
            ("Change to line end", ("C",)),
            ("Yank line", ("Y",)),
            ("Paste after", ("p",)),
            ("Paste before", ("P",)),
            ("Join lines", ("J",)),
            ("Toggle case", ("~",)),
            ("Undo", ("u",)),
            ("Redo", ("Ctrl+R",)),
            ("Repeat change", (".",)),
            ("Set mark", ("m",)),
            ("Visual", ("v",)),
            ("Visual line", ("V",)),
            ("Reselect", ("g v",)),
            ("Toggle fold", ("z a",)),
            ("Open fold", ("z o",)),
            ("Close fold", ("z c",)),
            ("Leave editor", ("Z Z", "Z Q")),
            ("Ex command", (":",)),
        ),
    ),
    Section(
        "Vim: operators",
        ("rdb_vim.py",),
        (
            ("Delete", ("d",)),
            ("Change", ("c",)),
            ("Yank", ("y",)),
            ("Indent", (">",)),
            ("Outdent", ("<",)),
            ("Toggle case", ("g ~",)),
            ("Lowercase", ("g u",)),
            ("Uppercase", ("g U",)),
        ),
    ),
    Section(
        "Vim: text objects",
        ("rdb_vim.py",),
        (
            ("Inner word", ("i w",)),
            ("Around word", ("a w",)),
            ("Inner WORD", ("i W",)),
            ("Around WORD", ("a W",)),
            ("Inner sentence", ("i s",)),
            ("Around sentence", ("a s",)),
            ("Inner paragraph", ("i p",)),
            ("Around paragraph", ("a p",)),
            ("Inner quotes", ('i "', "i '", "i `")),
            ("Around quotes", ('a "', "a '", "a `")),
            ("Inner parentheses", ("i (", "i )", "i b")),
            ("Around parentheses", ("a (", "a )", "a b")),
            ("Inner brackets", ("i [", "i ]")),
            ("Around brackets", ("a [", "a ]")),
            ("Inner braces", ("i {", "i }", "i B")),
            ("Around braces", ("a {", "a }", "a B")),
            ("Inner angles", ("i <", "i >")),
            ("Around angles", ("a <", "a >")),
            ("Inner tag", ("i t",)),
            ("Around tag", ("a t",)),
        ),
    ),
    Section(
        "Vim: visual",
        ("rdb_vim.py",),
        (
            ("Delete selection", ("d", "x")),
            ("Delete lines", ("X", "D")),
            ("Change selection", ("c", "s")),
            ("Change lines", ("S", "R", "C")),
            ("Yank selection", ("y",)),
            ("Yank lines", ("Y",)),
            ("Indent", (">",)),
            ("Outdent", ("<",)),
            ("Toggle case", ("~", "g ~")),
            ("Lowercase", ("u", "g u")),
            ("Uppercase", ("U", "g U")),
            ("Join lines", ("J",)),
            ("Paste over", ("p", "P")),
            ("Replace chars", ("r",)),
            ("Other end", ("o", "O")),
            ("Visual or line", ("v", "V")),
            ("Set mark", ("m",)),
        ),
    ),
)


def is_vim(section):
    return section.title.startswith("Vim")
