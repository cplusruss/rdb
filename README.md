# rdb

A keyboard-first outliner, to-do list and project map for Windows. Everything lives in one `.rdb` file, and the app is a single portable `rdb.exe`. There is no installer, no account and no cloud.
## Why use it

- **One place for everything.** Projects sit as cards on a zoomable map. Each card opens into a to-do list, a note, or a board of more cards, nested as deep as you like.
- **Fast capture.** Quick add (`Ctrl+Q`) drops an item into any list without leaving what you are doing. Screen snips land in an inbox, ready to attach to an item.
- **Find anything in a keystroke.** `Ctrl+P` searches every list, card and note in the file at once. `Ctrl+F` searches the page you are on, with filters for hashtags, priorities and markers.
- **See work the way you think about it.** Show the same items as a list, as a Kanban board by status (`Ctrl+Shift+K`) or on a timeline.
- **Built for the keyboard.** Every common action has a shortcut. There is an optional Vim mode, and `F1` lists all the shortcuts.
- **Your data stays yours.** It is one SQLite-backed file you can copy, back up or sync with any tool. Settings live in `rdb.ini` beside the exe.

## Features

- **Map:** cards, groups and text blocks on a board you can zoom and pan, with boards nested inside cards. Undo and redo everywhere.
- **Lists:**
  - headings with folds, indenting, and done or completed toggles;
  - `#hashtags` shown as pills, priorities P1 to P4, and configurable statuses with icons;
  - notes on items, links, images and dates.
- **Kanban:** a board view of any list, grouped by status. You can rename and add columns on the board itself, and search it with `Ctrl+F`.
- **Timeline:** dated items on a header grid.
- **Finder and find bar:** a whole-file search ranked by relevance, plus an in-page find with isolate, tag and priority filters.
- **Contacts:** a contact list you can attach to cards, with vCard and CSV import and export.
- **Reminders:** an urgent-item banner and page, with end-of-day reminders.
- **Snips:** screen clipping (`F4` by default) into an inbox folder.
- **Claude integration (optional):** a prompt bar (`Ctrl+Shift+Space`) that works on your items. It needs the [Claude Code](https://claude.com/claude-code) CLI installed and signed in.
- **Export:** Word (`.docx`), plain text and Markdown.
- **Look:** Classic, Win11 and Win11 Compact themes, with light and dark modes and configurable fonts and colours.

## Install

1. Download `rdb.exe` from the [latest release](https://github.com/cplusruss/rdb/releases/latest).
2. Put it in any folder you can write to, for example `C:\Tools\rdb\`.
3. Run it.

On first start rdb creates `rdb.ini` (your settings) and `rdb.rdb` (your data) beside the exe. To open another file, use **File > Open** (`Ctrl+O`).

**Already have settings?** Put your existing `rdb.ini` in the same folder as `rdb.exe` before the first start, and rdb uses it as is. An older `%APPDATA%\rdb\rdb.ini` is copied there automatically on first start when no ini is present yet.

## Update

1. In rdb, choose **File > Check for Updates...**.
2. If a newer release exists, click **Update**.

rdb downloads the new `rdb.exe`, checks its SHA-256 against the release, swaps it in and restarts with your file open. Only `rdb.exe` is replaced: `rdb.ini` and your `.rdb` files are never touched.

To update by hand instead, close rdb and replace `rdb.exe` with the one from the latest release.

## Run from source

Requires Python 3.13.

```powershell
pip install -r requirements.txt
python src\rdb.py
```

`install_rdb.bat` installs the requirements and puts a desktop shortcut to the source version. When run from source, settings go in `src\rdb.ini`, and Check for Updates only reports the latest version.

## Build and release

`build_exe.bat` builds the single-file `dist\rdb.exe` with PyInstaller.

To publish a release:

1. Bump `VERSION` in `src\rdb_version.py`.
2. Commit and push.
3. Run the release workflow:

```powershell
gh workflow run release.yml --repo cplusruss/rdb
```

GitHub Actions builds `rdb.exe` on Windows and publishes it as release `v<VERSION>`.
