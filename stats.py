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
from datetime import datetime, time, timedelta, timezone

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


def data_home(env=None):
    env = os.environ if env is None else env
    return env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def cache_home(env=None):
    env = os.environ if env is None else env
    return env.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")


def default_db():
    return os.path.join(data_home(), "rtk", "history.db")


def default_cache():
    return os.path.join(cache_home(), "ismoil-rtk", "paths-v4.json")


# ---------- time ----------

def utc(moment):
    # The database stores UTC as ISO text, which compares correctly as a string.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def local_midnight(now, days_ago=0):
    """Local midnight `days_ago` days before the day of `now`, DST-correct."""
    day = now.date() - timedelta(days=days_ago)
    return datetime.combine(day, time()).astimezone()


def period_start(now, period):
    if period == "today":
        return local_midnight(now)
    if period == "week":
        return local_midnight(now, 6)  # seven calendar days including today
    return None


# ---------- projects ----------

def git(path, *args):
    try:
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_of(path):
    """[repo path, name, copy, "repo"] for a folder inside git, else None."""
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    top = git(path, "rev-parse", "--show-toplevel")
    if not common or not top:
        return None
    repo = os.path.dirname(common.rstrip("/")) if common.endswith("/.git") else common
    copy = "" if os.path.realpath(top) == os.path.realpath(repo) else os.path.basename(top)
    return [repo, os.path.basename(repo), copy, "repo"]


class Projects:
    """Maps a project path from the history to [id, name, copy, kind].

    kind is "repo", "folder" (outside git), "tmp" (agent sandboxes under /tmp,
    all one row) or "home" (what ran in the home folder itself, not in its
    subfolders). An empty copy is the main checkout. Answers are cached on
    disk so a deleted worktree still lands in its project.
    """

    def __init__(self, cache_path, home=None, tmp=TMP):
        self.cache_path = cache_path
        self.home = home or os.path.expanduser("~")
        self.tmp = tmp
        self.cache = self._load()

    def _load(self):
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            # Two bar instances (one per monitor) may save at the same time.
            tmp = "%s.%d.tmp" % (self.cache_path, os.getpid())
            with open(tmp, "w") as f:
                json.dump(self.cache, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def of(self, path):
        if path not in self.cache:
            self.cache[path] = self._resolve(path)
        return self.cache[path]

    def _folder(self, path):
        if path == self.tmp or path.startswith(self.tmp + "/"):
            return [self.tmp, "", "", "tmp"]
        if path == self.home:
            return [self.home, "", "", "home"]
        return [path, os.path.basename(path.rstrip("/")) or path, "", "folder"]

    def _resolve(self, path):
        if os.path.isdir(path):
            return repo_of(path) or self._folder(path)

        # The path is gone. A parent resolved while it was alive, or the
        # nearest live parent inside git, takes the subfolders of a deleted worktree.
        probe = os.path.dirname(path)
        while probe not in ("", "/"):
            if probe in self.cache:
                return self.cache[probe]
            if os.path.isdir(probe):
                found = repo_of(probe)
                if found:
                    return found
                break
            probe = os.path.dirname(probe)

        parts = path.rstrip("/").split("/")
        # <repo>/.claude/worktrees/<copy> (Claude Code)
        if ".claude" in parts:
            i = parts.index(".claude")
            if len(parts) > i + 2 and parts[i + 1] == "worktrees":
                repo = "/".join(parts[:i])
                return [repo, os.path.basename(repo), parts[i + 2], "repo"]
        # .../worktrees/<project>/<copy>: the repo lives elsewhere, and any
        # other copy of the project already in the cache knows where.
        if "worktrees" in parts:
            i = parts.index("worktrees")
            if len(parts) > i + 2:
                group = "/".join(parts[:i + 2]) + "/"
                for known, value in self.cache.items():
                    if known.startswith(group):
                        return [value[0], value[1], parts[i + 2], value[3]]
                return [group.rstrip("/"), parts[i + 1], parts[i + 2], "repo"]
        return self._folder(path)


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
    decisions = dict(db.execute(
        "select decision, count(*) from hook_decisions where timestamp >= ? group by 1", (since,)).fetchall())

    total = bucket()
    commands = {}
    by_project = {}
    worse = 0
    for row in rows:
        key, name, copy, kind = projects.of(row["project_path"] or "")
        command = command_name(row["rtk_cmd"])
        worse += row["worse"]

        add(total, row)
        add(commands.setdefault(command, bucket()), row)
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

    # The rtk that actually handles commands, read without running it.
    version = db.execute("select rtk_version from hook_decisions order by id desc limit 1").fetchone()

    current, slots = chart(db, now, period)
    return {
        "version": version[0] if version else "",
        "total": finish(total),
        "chart": slots,
        "chartNow": current,
        "hook": {"seen": rewritten + passed, "rewritten": rewritten, "passed": passed},
        "worse": worse,
        "oversized": oversized,
        "projects": project_list,
        "top": ranked(commands, TOP_COMMANDS, cmd=lambda c: c),
    }


def summarize(db_path, cache_path, period, now=None):
    """The JSON object for `period` (or "bar"), as a dict."""
    now = now or datetime.now().astimezone()
    result = {"ok": False, "period": period}
    if not os.path.exists(db_path):
        result["error"] = "nodb"
        return result
    try:
        db = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            result["today"] = today_totals(db, now)
            if period != "bar":
                result.update(full(db, now, period, Projects(cache_path)))
        finally:
            db.close()
        result["ok"] = True
    except sqlite3.Error as e:
        # The database is busy being written, or its schema changed: the
        # widget keeps the last good numbers.
        result["error"] = "busy"
        result["detail"] = str(e)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("period", nargs="?", default="bar", choices=("bar",) + PERIODS)
    parser.add_argument("--db", default=default_db(), type=os.path.expanduser,
                        help="rtk history database (default: rtk's own location)")
    parser.add_argument("--cache", default=default_cache())
    args = parser.parse_args(argv)
    print(json.dumps(summarize(args.db, args.cache, args.period), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
