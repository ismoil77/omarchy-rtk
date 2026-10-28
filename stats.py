#!/usr/bin/env python3
"""rtk usage summary for the Omarchy widget.

Reads rtk's history database read-only and prints one JSON object. It says
nothing in any human language: names that need words (temporary folders, the
home folder, the main copy of a repo) come out as a `kind` or a flag, and
errors as a code, so the widget can translate them.

    stats.py [--db PATH] [--cache PATH] bar            today's total only (the bar label)
    stats.py [--db PATH] [--cache PATH] today|week|all everything for the popup

Totals are counted the way `rtk gain` counts its headline: rows whose raw
output was longer than ~30k characters (7 500 tokens, Claude Code's Bash
output cap) are left out, and a row never saves less than zero. The widget's
numbers then match `rtk gain`.

Projects are grouped by git repository (the git common dir), so the
worktrees and copies of one repo are a single row with the copies listed
inside.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

# HEADLINE_INPUT_CAP_TOKENS in rtk (src/core/tracking.rs).
INPUT_CAP = 7500
# The subcommand matters for these ("git status" and "git diff" are different filters).
WITH_SUBCOMMAND = {"git", "glab", "gh", "pnpm", "npm", "bun", "docker", "cargo", "kubectl", "go", "uv"}
TOP_COMMANDS = 6
# Columns of the "all time" chart: the last 30 days.
ALL_DAYS = 30
PERIODS = ("today", "week", "all")

# What `rtk gain` counts in its headline.
COUNTED = "input_tokens <= %d" % INPUT_CAP
SAVED = "max(saved_tokens, 0)"

TMP = "/tmp"
# How long a read may wait for rtk to finish writing before the widget tries again later.
BUSY_TIMEOUT = 2


def data_home(env=None):
    env = os.environ if env is None else env
    return env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def cache_home(env=None):
    env = os.environ if env is None else env
    return env.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")


def default_db():
    return os.path.join(data_home(), "rtk", "history.db")


def default_cache():
    return os.path.join(cache_home(), "ismoil-rtk", "paths-v6.json")


# ---------- time ----------

def utc(moment):
    # The database stores UTC as ISO text, which compares correctly as a string.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def local_midnight(now, days_ago=0):
    """Local midnight `days_ago` days before the day of `now`, DST-correct."""
    day = now.date() - timedelta(days=days_ago)
    return datetime.combine(day, datetime.min.time()).astimezone()


def period_start(now, period):
    if period == "today":
        return local_midnight(now)
    if period == "week":
        return local_midnight(now, 6)  # seven calendar days including today
    return None


# ---------- projects ----------

def git(path, *args):
    """git's answer, "" when git says no, None when it could not be asked."""
    try:
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_of(path):
    """[repo path, name, copy, "repo"] for a folder inside git; None when it is
    not in git; UNKNOWN when git could not be asked (do not remember that)."""
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        return UNKNOWN
    top = git(path, "rev-parse", "--show-toplevel")
    if top is None:
        return UNKNOWN
    if not common or not top:
        return None
    repo = os.path.dirname(common.rstrip("/")) if common.endswith("/.git") else common
    copy = "" if os.path.realpath(top) == os.path.realpath(repo) else os.path.basename(top)
    return [repo, os.path.basename(repo), copy, "repo"]


UNKNOWN = object()
KINDS = ("repo", "folder", "tmp", "home")
# A folder outside git is looked at again after this long: it may have become a repo.
RECHECK_SECONDS = 24 * 3600


class Projects:
    """Maps a project path from the history to [id, name, copy, kind].

    kind is "repo", "folder" (outside git), "tmp" (agent sandboxes under /tmp,
    all one row) or "home" (what ran in the home folder itself, not in its
    subfolders). An empty copy is the main checkout. Answers that come from
    git or from other answers are cached on disk, so a deleted worktree still
    lands in its project; guesses made from a path's shape alone are not, so
    they are corrected as soon as a live copy shows where the repo is.
    """

    def __init__(self, cache_path, home=None, tmp=TMP, clock=time.time):
        self.cache_path = cache_path
        self.home = home or os.path.expanduser("~")
        self.tmp = tmp
        self.clock = clock
        self._tighten()
        self.cache = self._load()

    def _tighten(self):
        """Owner-only folder and file, also for what an older version left open:
        a bar that never opens its popup never saves, so this cannot wait for save()."""
        try:
            os.chmod(os.path.dirname(self.cache_path), 0o700)
            os.chmod(self.cache_path, 0o600)
        except OSError:
            pass

    def _load(self):
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # Entries that do not have the expected shape are dropped, not trusted.
        return {k: v for k, v in data.items() if self._shaped(v)}

    @staticmethod
    def _shaped(v):
        if not isinstance(v, list) or len(v) not in (4, 5) or not all(isinstance(x, str) for x in v[:4]):
            return False
        if v[3] not in KINDS:
            return False
        if len(v) == 5:
            try:
                float(v[4])
            except (TypeError, ValueError):
                return False
        return True

    def save(self):
        try:
            # The cache holds private project and worktree paths: owner-only,
            # whatever the umask and whatever an older version left behind.
            folder = os.path.dirname(self.cache_path)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            os.chmod(folder, 0o700)
            # Two bar instances (one per monitor) may save at the same time.
            tmp = "%s.%d.tmp" % (self.cache_path, os.getpid())
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)  # a stale tmp from an older run keeps its old mode otherwise
            with os.fdopen(fd, "w") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def of(self, path):
        known = self.cache.get(path)
        if known is not None and not self._stale(path, known):
            return known[:4]
        value, remember = self._resolve(path)
        if remember:
            self.cache[path] = value + ([str(int(self.clock()))] if value[3] == "folder" else [])
        return value

    def _stale(self, path, entry):
        if entry[3] != "folder" or len(entry) < 5:
            return False
        try:
            return self.clock() - float(entry[4]) > RECHECK_SECONDS and os.path.isdir(path)
        except (ValueError, TypeError):
            return True

    def _folder(self, path):
        if path == self.tmp or path.startswith(self.tmp + "/"):
            return [self.tmp, "", "", "tmp"]
        if path == self.home:
            return [self.home, "", "", "home"]
        return [path, os.path.basename(path.rstrip("/")) or path, "", "folder"]

    def _repo_at(self, path):
        """The repo whose root is `path`, from the cache or from git; None when unknown."""
        known = self.cache.get(path)
        if known is not None and known[3] == "repo":
            return known[:4]
        if os.path.isdir(path):
            found = repo_of(path)
            if found not in (None, UNKNOWN):
                return found
        return None

    def _resolve(self, path):
        """(value, remember): remember is False for an answer that is only a guess."""
        if os.path.isdir(path):
            found = repo_of(path)
            if found is UNKNOWN:
                return self._folder(path), False   # git was too slow; ask again next time
            return found or self._folder(path), True

        parts = path.rstrip("/").split("/")
        # <repo>/.claude/worktrees/<copy> (Claude Code): the copy is named by the
        # path itself, whether or not the repo is still there.
        if ".claude" in parts:
            i = parts.index(".claude")
            if len(parts) > i + 2 and parts[i + 1] == "worktrees":
                repo = "/".join(parts[:i])
                found = self._repo_at(repo)
                if found:
                    return [found[0], found[1], parts[i + 2], "repo"], True
                return [repo, os.path.basename(repo), parts[i + 2], "repo"], False
        # .../worktrees/<project>/<copy>: the repo lives elsewhere, and any other
        # copy of the project that is already known says where.
        if "worktrees" in parts:
            i = parts.index("worktrees")
            if len(parts) > i + 2:
                group = "/".join(parts[:i + 2]) + "/"
                for known, value in self.cache.items():
                    if known.startswith(group) and value[3] == "repo":
                        return [value[0], value[1], parts[i + 2], "repo"], True
                return [group.rstrip("/"), parts[i + 1], parts[i + 2], "repo"], False

        # Any other deleted path: a repo resolved while it was alive, or the
        # nearest live parent inside git. Under a live folder that is not in git,
        # the whole deleted tree is one project: its top folder.
        child = path
        probe = os.path.dirname(path)
        while probe not in ("", "/"):
            known = self.cache.get(probe)
            if known is not None and known[3] == "repo":
                return known[:4], True
            if os.path.isdir(probe):
                found = repo_of(probe)
                if found is UNKNOWN:
                    return self._folder(path), False
                return (found, True) if found else (self._folder(child), True)
            child = probe
            probe = os.path.dirname(probe)
        return self._folder(path), True


# ---------- counting ----------

def command_name(rtk_cmd):
    words = rtk_cmd.split()
    if words and words[0] == "rtk":
        words = words[1:]
    if not words:
        return rtk_cmd
    if words[0] in WITH_SUBCOMMAND and len(words) > 1 and not words[1].startswith("-"):
        return words[0] + " " + words[1]
    return words[0]


def bucket():
    return {"commands": 0, "input": 0, "output": 0, "saved": 0, "ms": 0}


def add(b, row):
    b["commands"] += row["n"]
    b["input"] += row["input"]
    b["output"] += row["output"]
    b["saved"] += row["saved"]
    b["ms"] += row["ms"]


def finish(b):
    b["pct"] = round(100.0 * b["saved"] / b["input"], 1) if b["input"] else 0.0
    b["avgMs"] = round(b.pop("ms") / b["commands"]) if b["commands"] else 0
    return b


def ranked(groups, limit, **extra):
    rows = [finish(dict(v, **{k: f(name) for k, f in extra.items()})) for name, v in groups.items()]
    rows.sort(key=lambda r: (r["saved"], r["commands"]), reverse=True)
    return rows[:limit]


def today_totals(db, now):
    row = db.execute(
        "select count(*), coalesce(sum(%s), 0), coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0)"
        " from commands where timestamp >= ? and %s" % (SAVED, COUNTED), (utc(local_midnight(now)),)).fetchone()
    return {"commands": row[0], "saved": row[1], "input": row[2], "output": row[3]}


def chart(db, now, period):
    """The chart's columns, with no gaps: the hours of today, or the days of the
    period. The first return value is the index of the current hour or day."""
    if period == "today":
        start = local_midnight(now)
        slots = [("%02d" % h, "%02d:00" % h) for h in range(24)]
        key = "strftime('%H', timestamp, 'localtime')"
        current = now.hour
    else:
        if period == "week":
            days = 7
        else:
            first = db.execute("select min(timestamp) from commands").fetchone()[0]
            first_day = (datetime.fromisoformat(first[:19]).replace(tzinfo=timezone.utc).astimezone().date()
                         if first else None)
            days = min(ALL_DAYS, (now.date() - first_day).days + 1) if first_day else 1
        start = local_midnight(now, days - 1)
        dates = [start.date() + timedelta(days=d) for d in range(days)]
        slots = [(d.isoformat(), d.isoformat()) for d in dates]
        key = "date(timestamp, 'localtime')"
        current = len(slots) - 1

    sums = {k: (n, s, i) for k, n, s, i in db.execute(
        "select %s, count(*), sum(%s), sum(input_tokens) from commands"
        " where timestamp >= ? and %s group by 1" % (key, SAVED, COUNTED), (utc(start),))}
    return current, [{"key": k, "label": label,
                      "commands": sums.get(k, (0, 0, 0))[0],
                      "saved": sums.get(k, (0, 0, 0))[1],
                      "input": sums.get(k, (0, 0, 0))[2]} for k, label in slots]


def hook_decisions(db, since):
    """({decision: count}, rtk version) from the hook log. The log is a side
    dish: if rtk changed its layout, the commands still count and the hook
    figures read zero. Any other error (busy, I/O) is still the caller's."""
    try:
        decisions = dict(db.execute(
            "select decision, count(*) from hook_decisions where timestamp >= ? group by 1", (since,)).fetchall())
        # The rtk that actually handles commands, read without running it.
        row = db.execute("select rtk_version from hook_decisions order by id desc limit 1").fetchone()
    except sqlite3.OperationalError as e:
        if error_code(e) != "schema":
            raise
        return {}, ""
    return decisions, row[0] if row else ""


def full(db, now, period, projects):
    start = period_start(now, period)
    since = utc(start) if start else ""

    # One row per (command, folder): a long history shrinks to a few thousand rows.
    rows = db.execute(
        "select rtk_cmd, project_path, count(*) as n, sum(input_tokens) as input, sum(output_tokens) as output,"
        " sum(%s) as saved, sum(coalesce(exec_time_ms, 0)) as ms, sum(saved_tokens < 0) as worse"
        " from commands where timestamp >= ? and %s group by rtk_cmd, project_path" % (SAVED, COUNTED),
        (since,)).fetchall()
    oversized = db.execute(
        "select count(*) from commands where timestamp >= ? and not (%s)" % COUNTED, (since,)).fetchone()[0]
    decisions, version = hook_decisions(db, since)

    # Live folders first: they teach the cache where each repo is, so a deleted
    # copy of it is placed correctly whatever order the rows come in.
    paths = sorted({row["project_path"] for row in rows if row["project_path"]}, key=lambda p: (not os.path.isdir(p), p))
    placed = {path: projects.of(path) for path in paths}

    total = bucket()
    commands = {}
    by_project = {}
    worse = 0
    for row in rows:
        command = command_name(row["rtk_cmd"])
        worse += row["worse"]

        add(total, row)
        add(commands.setdefault(command, bucket()), row)
        if not row["project_path"]:
            continue  # counted above; a command with no folder is not a project
        key, name, copy, kind = placed[row["project_path"]]
        p = by_project.setdefault(key, {"name": name, "kind": kind, "stats": bucket(), "copies": {}, "top": {}})
        add(p["stats"], row)
        add(p["copies"].setdefault(copy, bucket()), row)
        add(p["top"].setdefault(command, bucket()), row)
    projects.save()

    project_list = []
    for key, p in by_project.items():
        item = finish(p["stats"])
        item.update(id=key, name=p["name"], kind=p["kind"],
                    copies=ranked(p["copies"], None, name=lambda c: c, main=lambda c: c == ""),
                    top=ranked(p["top"], TOP_COMMANDS, cmd=lambda c: c))
        project_list.append(item)
    project_list.sort(key=lambda p: (p["saved"], p["commands"]), reverse=True)
    # Same-named repos in different places are told apart by the folder above them.
    for p in project_list:
        if p["kind"] not in ("repo", "folder"):
            continue
        twins = [q for q in project_list if q["kind"] in ("repo", "folder") and q["name"] == p["name"]]
        parent = os.path.basename(os.path.dirname(p["id"].rstrip("/")))
        p["label"] = "%s (%s)" % (p["name"], parent) if len(twins) > 1 and parent else p["name"]
    for p in project_list:
        p.setdefault("label", p["name"])

    # ask/allow: the hook rewrote the command to `rtk ...`; defer: rtk does not
    # know it; deny: it matched a deny rule in the permissions, so rtk left it.
    rewritten = decisions.get("allow", 0) + decisions.get("ask", 0)
    passed = decisions.get("defer", 0) + decisions.get("deny", 0)

    current, slots = chart(db, now, period)
    return {
        "version": version,
        "total": finish(total),
        "chart": slots,
        "chartNow": current,
        "hook": {"seen": rewritten + passed, "rewritten": rewritten, "passed": passed},
        "worse": worse,
        "oversized": oversized,
        "projects": project_list,
        "top": ranked(commands, TOP_COMMANDS, cmd=lambda c: c),
    }


def error_code(e):
    """Why the database could not be read: busy (try again), schema (rtk changed),
    or unreadable (not a database, I/O)."""
    text = str(e).lower()
    if "locked" in text or "busy" in text:
        return "busy"
    if text.startswith("no such") or "has no column" in text:
        return "schema"
    return "unreadable"


def summarize(db_path, cache_path, period, now=None):
    """The JSON object for `period` (or "bar"), as a dict. Today's total is
    kept whenever it could be read, even if the rest could not."""
    now = now or datetime.now().astimezone()
    result = {"ok": False, "period": period, "todayOk": False}
    if not os.path.exists(db_path):
        result["error"] = "nodb"
        return result
    try:
        db = sqlite3.connect("file:" + urllib.parse.quote(os.path.abspath(db_path)) + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT)
        db.row_factory = sqlite3.Row
        try:
            result["today"] = today_totals(db, now)
            result["todayOk"] = True
            if period != "bar":
                result.update(full(db, now, period, Projects(cache_path)))
        finally:
            db.close()
        result["ok"] = True
    except sqlite3.Error as e:
        # busy: the widget keeps the last good numbers; the other codes are
        # permanent until rtk or the file changes.
        result["error"] = error_code(e)
        result["detail"] = str(e)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("period", nargs="?", default="bar", choices=("bar",) + PERIODS)
    parser.add_argument("--db", default=default_db(), type=os.path.expanduser,
                        help="rtk history database (default: rtk's own location)")
    parser.add_argument("--cache", default=default_cache())
    args = parser.parse_args(argv)
    try:
        result = summarize(args.db, args.cache, args.period)
    except Exception as e:  # the widget needs an answer it can show, never a traceback
        result = {"ok": False, "period": args.period, "todayOk": False, "error": "internal", "detail": repr(e)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
