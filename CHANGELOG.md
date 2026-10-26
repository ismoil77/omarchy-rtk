# Changelog

## 3.0.0

- **English and Russian.** `language`: `auto` follows the system locale. Every word moved out of the
  code into `Strings.js`; `stats.py` now prints no words at all (kinds and error codes instead), so
  the widget translates them. Russian uses «вы», plural forms and decimal commas.
- **Readable for low vision.** Text never goes below 13 px and scales with the new `textSize`
  setting; colours used for text are held to WCAG AA on the popup background; the chart readout
  wraps instead of being cut off.
- **No more workarounds.** The popup's pieces are real components (`kit/`) instead of inline
  types reaching into the widget; `stats.py` takes `--db` and `--cache` instead of module constants,
  honours `XDG_DATA_HOME` / `XDG_CACHE_HOME`, does its DST-safe date maths in one place and groups
  rows in SQL (a long history is no slower than a short one). Dropped the special case for one
  worktree manager: any `…/worktrees/<project>/<copy>` layout is understood.
- `database` setting for a non-default `history.db`; `animations` switch.
- The count column is sized from the data (a five-digit count no longer overlaps its name).
- Renamed `Panel.qml` → `Widget.qml`; the cache moved to `~/.cache/ismoil-rtk/paths-v6.json`.
- **Project grouping no longer depends on row order.** A deleted worktree is placed by its live
  siblings in the same run; a guess made from a path's shape is never remembered; a deleted
  `.claude/worktrees/<copy>` of a live repo keeps its own name; a deleted tree under a plain folder
  is one project instead of collapsing into its parent; a slow git answer is not cached; a folder
  outside git is looked at again after a day. (Cache is now `paths-v6.json`.)
- **Errors say what is wrong:** `busy` (waits, keeps the last numbers), `schema` (rtk changed),
  `unreadable`, `nodb`, `internal` (python missing or crashed). Today's total survives a failed
  full read, and the bar no longer keeps a number from yesterday when the database goes away.
- **Keyboard:** `↑`/`↓` (or `j`/`k`) pick a project, `m` shows more of them.
- The chart's quiet and picked columns are both 3:1 graphics; a translucent popup background is
  judged over the theme's background (kit 1.1.0).
- 93 automated tests (Python, Node, Qt's own engine), CI, README in two languages.

## 2.0.0

Counts like `rtk gain` 1.0, bar total while closed, full report while open, chart by hour or day,
projects grouped by repository, theme-coloured.
