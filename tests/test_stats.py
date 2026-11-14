import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import stats  # noqa: E402

SCHEMA = """
create table commands (
    id integer primary key, timestamp text not null, original_cmd text not null, rtk_cmd text not null,
    input_tokens integer not null, output_tokens integer not null, saved_tokens integer not null,
    savings_pct real not null, exec_time_ms integer default 0, project_path text default '');
create table hook_decisions (
    id integer primary key, timestamp text not null, session_id text not null, tool_use_id text not null,
    project_path text default '', raw_cmd text not null, decision text not null, rewritten_cmd text,
    rtk_version text not null);
"""

CYRILLIC = re.compile("[А-Яа-яЁё]")


def set_tz(name):
    os.environ["TZ"] = name
    time.tzset()


def rtk_stamp(when):
    """A timestamp as rtk writes it: UTC, nanoseconds, an explicit offset."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".027487078+00:00"


def local(*args):
    """A local moment, as an aware datetime."""
    return datetime(*args).astimezone()


class Base(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        set_tz("Asia/Dushanbe")  # UTC+5, no DST
        self._git_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        # Keep the user's own git hooks and identity out of the fixtures.
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.dir = tempfile.mkdtemp(prefix="rtk-test-")
        self.db_path = os.path.join(self.dir, "history.db")
        self.cache_path = os.path.join(self.dir, "cache", "paths.json")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.commit()
        db.close()
        self.now = local(2026, 10, 26, 15, 30)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        for key, value in self._git_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    # -- fixtures
    def add(self, when, rtk_cmd="rtk grep x", inp=1000, out=400, path="", ms=10, saved=None):
        """One command at the local moment `when`."""
        stamp = rtk_stamp(when)
        saved = inp - out if saved is None else saved
        db = sqlite3.connect(self.db_path)
        db.execute("insert into commands (timestamp, original_cmd, rtk_cmd, input_tokens, output_tokens,"
                   " saved_tokens, savings_pct, exec_time_ms, project_path) values (?,?,?,?,?,?,?,?,?)",
                   (stamp, "orig", rtk_cmd, inp, out, saved, 0, ms, path))
        db.commit()
        db.close()

    def hook(self, when, decision, version="1.0.0"):
        db = sqlite3.connect(self.db_path)
        db.execute("insert into hook_decisions (timestamp, session_id, tool_use_id, raw_cmd, decision, rtk_version)"
                   " values (?,?,?,?,?,?)", (rtk_stamp(when), "s", "t", "cmd", decision, version))
        db.commit()
        db.close()

    def run_stats(self, period, now=None):
        return stats.summarize(self.db_path, self.cache_path, period, now or self.now)

    def git(self, path, *args):
        subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def repo(self, name, parent=None):
        path = os.path.join(parent or self.dir, name)
        os.makedirs(path)
        subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)
        self.git(path, "commit", "-q", "--allow-empty", "-m", "init")
        return path


class Headline(Base):
    """The totals follow `rtk gain`'s headline rules."""

    def test_counts_rows_and_sums_tokens(self):
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        self.add(local(2026, 10, 26, 10), inp=500, out=100)
        r = self.run_stats("today")
        self.assertTrue(r["ok"])
        t = r["total"]
        self.assertEqual((t["commands"], t["input"], t["output"], t["saved"]), (2, 1500, 500, 1000))
        self.assertEqual(t["pct"], 66.7)

    def test_oversized_rows_are_left_out_and_counted(self):
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        self.add(local(2026, 10, 26, 9, 5), inp=7500, out=0)    # exactly the cap: counted
        self.add(local(2026, 10, 26, 9, 6), inp=7501, out=10)   # over it: not
        r = self.run_stats("today")
        self.assertEqual(r["total"]["commands"], 2)
        self.assertEqual(r["total"]["input"], 8500)
        self.assertEqual(r["oversized"], 1)

    def test_a_longer_output_counts_as_zero_saved(self):
        self.add(local(2026, 10, 26, 9), inp=100, out=150, saved=-50)
        self.add(local(2026, 10, 26, 9, 1), inp=1000, out=400)
        r = self.run_stats("today")
        self.assertEqual(r["total"]["saved"], 600)
        self.assertEqual(r["total"]["commands"], 2)
        self.assertEqual(r["worse"], 1)

    def test_average_time(self):
        self.add(local(2026, 10, 26, 9), ms=100)
        self.add(local(2026, 10, 26, 9, 1), ms=300)
        self.assertEqual(self.run_stats("today")["total"]["avgMs"], 200)

    def test_empty_history(self):
        r = self.run_stats("all")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"]["commands"], 0)
        self.assertEqual(r["total"]["pct"], 0.0)
        self.assertEqual(r["total"]["avgMs"], 0)
        self.assertEqual(r["projects"], [])
        self.assertEqual(r["top"], [])
        self.assertEqual(r["version"], "")

    def test_bar_mode_is_only_today(self):
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        r = self.run_stats("bar")
        self.assertTrue(r["ok"])
        self.assertEqual(r["today"], {"commands": 1, "saved": 600, "input": 1000, "output": 400})
        self.assertNotIn("chart", r)
        self.assertNotIn("projects", r)

    def test_today_matches_the_popup_total(self):
        self.add(local(2026, 10, 26, 9))
        self.add(local(2026, 10, 25, 9))
        self.assertEqual(self.run_stats("today")["total"]["saved"], self.run_stats("today")["today"]["saved"])


class Periods(Base):
    def test_today_starts_at_local_midnight(self):
        self.add(local(2026, 10, 25, 23, 30), path="a")   # yesterday
        self.add(local(2026, 10, 26, 0, 30), path="a")    # today, but the previous day in UTC
        r = self.run_stats("today")
        self.assertEqual(r["total"]["commands"], 1)
        self.assertEqual(r["today"]["commands"], 1)

    def test_week_is_seven_calendar_days(self):
        self.add(local(2026, 10, 19, 12))   # 7 days ago: out
        self.add(local(2026, 10, 20, 0, 5))  # 6 days ago: in
        self.add(local(2026, 10, 26, 12))
        self.assertEqual(self.run_stats("week")["total"]["commands"], 2)

    def test_all_keeps_everything(self):
        self.add(local(2025, 1, 1, 12))
        self.add(local(2026, 10, 26, 12))
        self.assertEqual(self.run_stats("all")["total"]["commands"], 2)

    def test_hook_decisions_are_split_by_period(self):
        for d in ("allow", "ask", "defer", "deny", "defer"):
            self.hook(local(2026, 10, 26, 9), d)
        self.hook(local(2026, 10, 20, 9), "allow")
        r = self.run_stats("today")
        self.assertEqual(r["hook"], {"seen": 5, "rewritten": 2, "passed": 3})
        self.assertEqual(self.run_stats("all")["hook"]["seen"], 6)

    def test_version_is_the_latest_hook_call(self):
        self.hook(local(2026, 10, 25, 9), "allow", "0.51.0")
        self.hook(local(2026, 10, 26, 9), "allow", "1.0.0")
        self.assertEqual(self.run_stats("today")["version"], "1.0.0")

    def test_midnight_across_a_dst_change(self):
        set_tz("Europe/Berlin")  # clocks went back on 2026-10-25
        now = local(2026, 10, 26, 12)
        day = stats.local_midnight(now, 1)
        self.assertEqual((day.year, day.month, day.day, day.hour), (2026, 10, 25, 0))
        self.assertEqual(stats.utc(day), "2026-10-24T22:00:00")           # still summer time at 00:00
        self.assertEqual(stats.utc(stats.local_midnight(now)), "2026-10-25T23:00:00")  # winter time


class Chart(Base):
    def test_today_has_24_hours_and_lights_the_current_one(self):
        self.add(local(2026, 10, 26, 9, 10), inp=1000, out=400)
        self.add(local(2026, 10, 26, 9, 50), inp=1000, out=400)
        self.add(local(2026, 10, 26, 15, 5), inp=200, out=100)
        r = self.run_stats("today")
        self.assertEqual(len(r["chart"]), 24)
        self.assertEqual(r["chartNow"], 15)
        by_hour = {s["key"]: s for s in r["chart"]}
        self.assertEqual(by_hour["09"]["saved"], 1200)
        self.assertEqual(by_hour["09"]["commands"], 2)
        self.assertEqual(by_hour["15"]["saved"], 100)
        self.assertEqual(by_hour["03"]["commands"], 0)
        self.assertEqual(r["chart"][0]["label"], "00:00")

    def test_week_has_seven_days_ending_today(self):
        self.add(local(2026, 10, 24, 12), inp=100, out=50)
        r = self.run_stats("week")
        keys = [s["key"] for s in r["chart"]]
        self.assertEqual(keys, ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"])
        self.assertEqual(r["chartNow"], 6)
        self.assertEqual(r["chart"][4]["saved"], 50)

    def test_all_shows_at_most_30_days(self):
        self.add(local(2025, 1, 1, 12))
        self.add(local(2026, 10, 26, 12))
        r = self.run_stats("all")
        self.assertEqual(len(r["chart"]), 30)
        self.assertEqual(r["chart"][-1]["key"], "2026-10-26")

    def test_all_starts_at_the_first_day_when_history_is_short(self):
        self.add(local(2026, 10, 23, 12))
        r = self.run_stats("all")
        self.assertEqual([s["key"] for s in r["chart"]], ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"])

    def test_all_without_history_is_one_column(self):
        self.assertEqual(len(self.run_stats("all")["chart"]), 1)

    def test_chart_leaves_out_oversized_rows(self):
        self.add(local(2026, 10, 26, 9), inp=9000, out=10)
        self.assertEqual(self.run_stats("today")["chart"][9]["commands"], 0)


class Commands(unittest.TestCase):
    def test_command_name(self):
        n = stats.command_name
        self.assertEqual(n("rtk grep foo ."), "grep")
        self.assertEqual(n("rtk git status"), "git status")
        self.assertEqual(n("rtk git diff HEAD"), "git diff")
        self.assertEqual(n("rtk git -C x status"), "git")
        self.assertEqual(n("rtk cargo clippy --all"), "cargo clippy")
        self.assertEqual(n("rtk ls"), "ls")
        self.assertEqual(n("ls -la"), "ls")
        self.assertEqual(n("rtk"), "rtk")
        self.assertEqual(n(""), "")


class TopCommands(Base):
    def test_top_is_ranked_by_saved_and_capped(self):
        for i in range(8):
            self.add(local(2026, 10, 26, 9), rtk_cmd="rtk tool%d x" % i, inp=1000, out=1000 - 100 * (i + 1))
        self.add(local(2026, 10, 26, 9), rtk_cmd="rtk git status", inp=1000, out=0)
        self.add(local(2026, 10, 26, 9), rtk_cmd="rtk git diff", inp=1000, out=900)
        top = self.run_stats("today")["top"]
        self.assertEqual(len(top), stats.TOP_COMMANDS)
        self.assertEqual(top[0]["cmd"], "git status")
        self.assertEqual([t["saved"] for t in top], sorted((t["saved"] for t in top), reverse=True))

    def test_same_command_in_different_folders_is_one_row(self):
        self.add(local(2026, 10, 26, 9), rtk_cmd="rtk grep a", path=self.home)
        self.add(local(2026, 10, 26, 9), rtk_cmd="rtk grep b", path=self.dir)
        top = self.run_stats("today")["top"]
        self.assertEqual([(t["cmd"], t["commands"]) for t in top], [("grep", 2)])


class ProjectGrouping(Base):
    def projects(self, **kw):
        return stats.Projects(self.cache_path, home=self.home, **kw)

    def test_worktrees_and_copies_are_one_project(self):
        main = self.repo("app")
        wt = os.path.join(self.dir, "app-lane2")
        self.git(main, "worktree", "add", "-q", wt)
        sub = os.path.join(main, "src")
        os.makedirs(sub)
        self.add(local(2026, 10, 26, 9), path=main, inp=1000, out=0)
        self.add(local(2026, 10, 26, 9), path=sub, inp=1000, out=500)
        self.add(local(2026, 10, 26, 9), path=wt, inp=1000, out=900)
        r = self.run_stats("today")
        self.assertEqual(len(r["projects"]), 1)
        p = r["projects"][0]
        self.assertEqual((p["name"], p["kind"], p["commands"], p["saved"]), ("app", "repo", 3, 1600))
        copies = {c["name"]: c for c in p["copies"]}
        self.assertEqual(set(copies), {"", "app-lane2"})
        self.assertTrue(copies[""]["main"])
        self.assertFalse(copies["app-lane2"]["main"])
        self.assertEqual(copies[""]["commands"], 2)

    def test_projects_are_ranked_by_saved(self):
        a, b = self.repo("a"), self.repo("b")
        self.add(local(2026, 10, 26, 9), path=a, inp=1000, out=900)
        self.add(local(2026, 10, 26, 9), path=b, inp=1000, out=100)
        self.assertEqual([p["name"] for p in self.run_stats("today")["projects"]], ["b", "a"])

    def test_project_top_commands_and_selection_data(self):
        a = self.repo("a")
        self.add(local(2026, 10, 26, 9), path=a, rtk_cmd="rtk git status")
        self.add(local(2026, 10, 26, 9), path=a, rtk_cmd="rtk grep x")
        p = self.run_stats("today")["projects"][0]
        self.assertEqual(sorted(t["cmd"] for t in p["top"]), ["git status", "grep"])
        self.assertEqual(p["id"], a)

    def test_same_named_repos_are_told_apart_by_the_folder_above(self):
        parents = [os.path.join(self.dir, name) for name in ("work", "play")]
        for parent in parents:
            os.makedirs(parent)
        one, two = (self.repo("app", parent=parent) for parent in parents)
        self.add(local(2026, 10, 26, 9), path=one)
        self.add(local(2026, 10, 26, 9), path=two)
        labels = sorted(p["label"] for p in self.run_stats("today")["projects"])
        self.assertEqual(labels, ["app (play)", "app (work)"])

    def test_a_lone_repo_keeps_its_plain_name(self):
        a = self.repo("solo")
        self.add(local(2026, 10, 26, 9), path=a)
        p = self.run_stats("today")["projects"][0]
        self.assertEqual((p["name"], p["label"]), ("solo", "solo"))

    def test_folder_outside_git(self):
        plain = os.path.join(self.dir, "notes")
        os.makedirs(plain)
        found = self.projects(tmp="/nonexistent-tmp").of(plain)
        self.assertEqual(found, [plain, "notes", "", "folder"])

    def test_home_is_its_own_row_without_its_subfolders(self):
        sub = os.path.join(self.home, "docs")
        os.makedirs(sub)
        p = self.projects(tmp="/nonexistent-tmp")
        self.assertEqual(p.of(self.home), [self.home, "", "", "home"])
        self.assertEqual(p.of(sub), [sub, "docs", "", "folder"])

    def test_temp_sandboxes_are_one_row(self):
        tmp = os.path.join(self.dir, "tmp")
        a, b = os.path.join(tmp, "agent-1"), os.path.join(tmp, "agent-2")
        os.makedirs(a)
        os.makedirs(b)
        p = self.projects(tmp=tmp)
        self.assertEqual(p.of(a), [tmp, "", "", "tmp"])
        self.assertEqual(p.of(b), p.of(a))
        self.assertEqual(p.of(a + "/gone/deeper"), p.of(a))

    def test_a_command_with_no_folder_counts_but_is_not_a_project(self):
        self.add(local(2026, 10, 26, 9), path="")
        self.add(local(2026, 10, 26, 10), path=self.repo("app"))
        r = self.run_stats("today")
        self.assertEqual(r["total"]["commands"], 2)
        self.assertEqual([p["name"] for p in r["projects"]], ["app"])

    def test_damaged_entries_never_crash_the_stale_check(self):
        os.makedirs(os.path.dirname(self.cache_path))
        folder = os.path.join(self.dir, "plain")
        os.makedirs(folder)
        with open(self.cache_path, "w") as f:
            json.dump({folder: [folder, "plain", "", "folder", None],
                       "/a": ["/a", "a", "", "bogus"],
                       "/b": ["/b", "b", "", "folder", "soon"]}, f)
        p = self.projects(tmp="/nonexistent-tmp")
        self.assertEqual(list(p.cache), [])
        self.assertEqual(p.of(folder)[3], "folder")

    def test_a_stale_tmp_and_an_open_cache_are_tightened_without_saving(self):
        os.makedirs(os.path.dirname(self.cache_path))
        os.chmod(os.path.dirname(self.cache_path), 0o755)
        with open(self.cache_path, "w") as f:
            f.write("{}")
        os.chmod(self.cache_path, 0o644)
        self.projects()  # a bar that never opens its popup never saves
        self.assertEqual(os.stat(self.cache_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.cache_path)).st_mode & 0o777, 0o700)
        stale = "%s.%d.tmp" % (self.cache_path, os.getpid())
        with open(stale, "w") as f:
            f.write("old")
        os.chmod(stale, 0o644)
        p = self.projects()
        p.of(self.repo("app"))
        p.save()
        self.assertFalse(os.path.exists(stale))
        self.assertEqual(os.stat(self.cache_path).st_mode & 0o777, 0o600)

    def test_the_bar_alone_tightens_what_an_older_version_left_open(self):
        """The popup never opens: `bar` runs only, Projects is never built."""
        folder = os.path.dirname(self.cache_path)
        os.makedirs(folder)
        os.chmod(folder, 0o755)
        stale = "%s.%d.tmp" % (self.cache_path, 4242)
        for path in (self.cache_path, stale):
            with open(path, "w") as f:
                f.write("{}")
            os.chmod(path, 0o644)
        for db in (self.db_path, os.path.join(self.dir, "missing.db")):
            os.chmod(folder, 0o755)
            os.chmod(self.cache_path, 0o644)
            os.chmod(stale, 0o644)
            stats.summarize(db, self.cache_path, "bar", self.now)
            self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700, db)
            self.assertEqual(os.stat(self.cache_path).st_mode & 0o777, 0o600, db)
            self.assertEqual(os.stat(stale).st_mode & 0o777, 0o600, db)

    def test_the_bar_total_is_reused_until_the_database_changes(self):
        """Two bars ask at once: the second gets the first one's answer, owner-only."""
        calls = []
        real = stats.summarize
        def counting(*a, **k):
            calls.append(a[2])
            return real(*a, **k)
        with mock.patch.object(stats, "summarize", counting):
            first = stats.cached_bar(self.db_path, self.cache_path)
            again = stats.cached_bar(self.db_path, self.cache_path)
            self.assertEqual(first, again)
            self.assertEqual(calls, ["bar"])
            cache = os.path.join(os.path.dirname(self.cache_path), "bar-v1.json")
            self.assertEqual(os.stat(cache).st_mode & 0o777, 0o600)
            later = os.stat(self.db_path).st_mtime_ns + 10**9
            os.utime(self.db_path, ns=(later, later))
            stats.cached_bar(self.db_path, self.cache_path)
            self.assertEqual(calls, ["bar", "bar"])

    def test_project_paths_never_reach_a_command_line(self):
        """Other local accounts can read argv (/proc/PID/cmdline), not the cwd."""
        repo = self.repo("secret-project")
        seen = []
        real = subprocess.run
        def watch(cmd, *a, **k):
            seen.append((cmd, k.get("cwd")))
            return real(cmd, *a, **k)
        with mock.patch.object(stats.subprocess, "run", watch):
            self.assertIsNotNone(stats.git(repo, "rev-parse", "--show-toplevel"))
            self.assertEqual(stats.git(os.path.join(repo, "gone"), "rev-parse", "--show-toplevel"), "")
        self.assertTrue(seen)
        for cmd, cwd in seen:
            self.assertFalse(any("secret-project" in str(part) for part in cmd), cmd)
            self.assertEqual(cwd, repo)

    def test_a_custom_database_comes_by_environment_not_argv(self):
        with mock.patch.dict(os.environ, {"ISMOIL_RTK_DB": self.db_path}), mock.patch("sys.stdout", io.StringIO()) as out:
            stats.main(["bar", "--cache", self.cache_path])
        self.assertTrue(json.loads(out.getvalue())["ok"])

    def test_a_deleted_worktree_stays_in_its_project(self):
        main = self.repo("app")
        wt = os.path.join(self.dir, "app-lane9")
        self.git(main, "worktree", "add", "-q", wt)
        sub = os.path.join(wt, "src")
        os.makedirs(sub)
        before = self.projects()
        self.assertEqual(before.of(wt)[2], "app-lane9")
        before.save()
        shutil.rmtree(wt)
        after = self.projects()  # a new run reads the cache from disk
        self.assertEqual(after.of(wt), [main, "app", "app-lane9", "repo"])
        self.assertEqual(after.of(sub)[:2], [main, "app"])

    def test_the_cache_is_private_to_the_owner(self):
        """It holds project and worktree names: no other local user may read them,
        even under umask 022 and over a folder or file an older version left open."""
        os.makedirs(os.path.dirname(self.cache_path))
        os.chmod(os.path.dirname(self.cache_path), 0o755)
        with open(self.cache_path, "w") as f:
            f.write("{}")
        os.chmod(self.cache_path, 0o644)
        old = os.umask(0o022)
        try:
            p = self.projects()
            p.of(self.repo("app"))
            p.save()
        finally:
            os.umask(old)
        self.assertEqual(os.stat(self.cache_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(self.cache_path)).st_mode & 0o777, 0o700)

    def test_a_deleted_folder_inside_a_live_repo(self):
        main = self.repo("app")
        gone = os.path.join(main, "build", "out")
        found = self.projects().of(gone)
        self.assertEqual(found[:2], [main, "app"])

    def test_claude_code_worktrees_of_a_deleted_checkout(self):
        path = "/nonexistent-root/app/.claude/worktrees/feature-x"
        self.assertEqual(self.projects().of(path), ["/nonexistent-root/app", "app", "feature-x", "repo"])
        self.assertEqual(self.projects().of(path + "/src")[:3], ["/nonexistent-root/app", "app", "feature-x"])

    def test_worktrees_grouped_by_project_follow_a_known_copy(self):
        main = self.repo("app")
        group = os.path.join(self.dir, "wt-root", "worktrees", "app")
        live = os.path.join(group, "one")
        os.makedirs(group)
        self.git(main, "worktree", "add", "-q", live)
        p = self.projects()
        self.assertEqual(p.of(live)[:2], [main, "app"])
        self.assertEqual(p.of(os.path.join(group, "two")), [main, "app", "two", "repo"])

    def test_worktrees_grouped_by_project_with_no_known_copy(self):
        path = "/nonexistent-root/worktrees/proj/copy-a/src"
        self.assertEqual(self.projects().of(path), ["/nonexistent-root/worktrees/proj", "proj", "copy-a", "repo"])

    def test_a_deleted_copy_does_not_depend_on_the_order_of_the_rows(self):
        # The history names the gone copy first in every ordering; it must still
        # end up in the repo its live sibling belongs to, in the very same run.
        main = self.repo("app")
        group = os.path.join(self.dir, "wt-root", "worktrees", "app")
        os.makedirs(group)
        live = os.path.join(group, "zzz-live")
        self.git(main, "worktree", "add", "-q", live)
        gone = os.path.join(group, "aaa-gone", "src")
        self.add(local(2026, 10, 26, 9), path=gone)
        self.add(local(2026, 10, 26, 9), path=live)
        r = self.run_stats("today")
        self.assertEqual(len(r["projects"]), 1)
        self.assertEqual({c["name"] for c in r["projects"][0]["copies"]}, {"aaa-gone", "zzz-live"})
        self.assertEqual(r["projects"][0]["id"], main)

    def test_a_guess_from_a_path_is_not_remembered(self):
        main = self.repo("app")
        group = os.path.join(self.dir, "wt-root", "worktrees", "app")
        os.makedirs(group)
        live = os.path.join(group, "live")
        self.git(main, "worktree", "add", "-q", live)
        gone = os.path.join(group, "gone")
        p = self.projects()
        guess = p.of(gone)                       # nothing known yet: a guess from the layout
        self.assertEqual(guess[0], group)
        self.assertNotIn(gone, p.cache)
        p.of(live)
        self.assertEqual(p.of(gone)[:3], [main, "app", "gone"])   # corrected once a sibling is known
        self.assertIn(gone, p.cache)

    def test_a_deleted_claude_worktree_of_a_live_repo_keeps_its_own_name(self):
        main = self.repo("app")
        os.makedirs(os.path.join(main, ".claude", "worktrees"))
        gone = os.path.join(main, ".claude", "worktrees", "gone-copy")
        self.assertEqual(self.projects().of(gone), [main, "app", "gone-copy", "repo"])
        self.assertEqual(self.projects().of(gone + "/src/lib")[2], "gone-copy")

    def test_a_cached_main_checkout_does_not_swallow_its_deleted_copies(self):
        main = self.repo("app")
        p = self.projects()
        p.of(main)  # the main checkout is known, as it is after the first run
        gone = os.path.join(main, ".claude", "worktrees", "feature")
        self.assertEqual(p.of(gone)[2], "feature")

    def test_when_git_is_too_slow_the_answer_is_not_remembered(self):
        app = self.repo("app")
        original = stats.git
        stats.git = lambda *a: None   # timeout or no git
        try:
            p = self.projects(tmp="/nonexistent-tmp")
            self.assertEqual(p.of(app)[3], "folder")
            self.assertNotIn(app, p.cache)
        finally:
            stats.git = original
        self.assertEqual(self.projects().of(app)[:2], [app, "app"])

    def test_a_folder_that_becomes_a_repo_is_looked_at_again_after_a_day(self):
        folder = os.path.join(self.dir, "later")
        os.makedirs(folder)
        now = [1_000_000.0]
        p = self.projects(tmp="/nonexistent-tmp", clock=lambda: now[0])
        self.assertEqual(p.of(folder)[3], "folder")
        subprocess.run(["git", "init", "-q", folder], check=True, capture_output=True)
        self.git(folder, "commit", "-q", "--allow-empty", "-m", "init")
        self.assertEqual(p.of(folder)[3], "folder")           # remembered, same day
        now[0] += stats.RECHECK_SECONDS + 1
        self.assertEqual(p.of(folder)[3], "repo")

    def test_a_deleted_tree_under_a_plain_folder_is_one_project(self):
        parent = os.path.join(self.dir, "Projects")
        os.makedirs(parent)
        p = self.projects(tmp="/nonexistent-tmp")
        p.of(parent)  # the live folder itself is in the history too
        top = os.path.join(parent, "gone-app")
        for sub in ("", "/apps/web", "/crates/core/src"):
            self.assertEqual(p.of(top + sub), [top, "gone-app", "", "folder"], sub)
        self.assertEqual(p.of(parent)[1], "Projects")

    def test_damaged_cache_entries_are_dropped(self):
        os.makedirs(os.path.dirname(self.cache_path))
        good = ["/a", "a", "", "repo"]
        json.dump({"/good": good, "/short": ["/a", "a"], "/num": [1, 2, 3, 4], "/str": "x", "/null": None},
                  open(self.cache_path, "w"))
        self.assertEqual(self.projects().cache, {"/good": good})

    def test_cache_round_trip_and_garbage(self):
        p = self.projects(tmp="/nonexistent-tmp")
        p.of(self.home)
        p.save()
        self.assertEqual(json.load(open(self.cache_path))[self.home][3], "home")
        open(self.cache_path, "w").write("{not json")
        self.assertEqual(self.projects().cache, {})
        open(self.cache_path, "w").write("[1, 2]")
        self.assertEqual(self.projects().cache, {})
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.cache_path)) if f.endswith(".tmp")], [])

    def test_cache_in_an_unwritable_place_is_not_fatal(self):
        p = stats.Projects("/proc/nope/cache.json", home=self.home)
        p.of(self.home)
        p.save()


class Failures(Base):
    def test_no_database(self):
        r = stats.summarize(os.path.join(self.dir, "missing.db"), self.cache_path, "today", self.now)
        self.assertIn(r["installed"], (True, False))
        self.assertEqual((r["ok"], r["error"], r["todayOk"]), (False, "nodb", False))

    def test_a_file_that_is_not_a_database_is_unreadable(self):
        open(self.db_path, "wb").write(b"this is not sqlite" * 100)
        r = self.run_stats("today")
        self.assertEqual((r["ok"], r["error"]), (False, "unreadable"))
        self.assertTrue(r["detail"])

    def test_a_directory_is_unreadable(self):
        r = stats.summarize(self.dir, self.cache_path, "today", self.now)
        self.assertEqual(r["error"], "unreadable")

    def test_a_locked_database_is_busy_and_worth_retrying(self):
        self.add(local(2026, 10, 26, 9))
        stats.BUSY_TIMEOUT, saved = 0.05, stats.BUSY_TIMEOUT
        locker = sqlite3.connect(self.db_path, isolation_level=None)
        locker.execute("begin exclusive")
        try:
            r = self.run_stats("today")
        finally:
            locker.execute("rollback")
            locker.close()
            stats.BUSY_TIMEOUT = saved
        self.assertEqual((r["ok"], r["error"]), (False, "busy"))

    def test_a_schema_change_is_its_own_code_and_today_survives(self):
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        db = sqlite3.connect(self.db_path)
        db.execute("alter table commands drop column exec_time_ms")
        db.commit()
        db.close()
        r = self.run_stats("week")
        self.assertEqual((r["ok"], r["error"]), (False, "schema"))
        self.assertTrue(r["todayOk"])
        self.assertEqual(r["today"]["saved"], 600)

    def test_a_changed_hook_log_does_not_take_the_commands_down(self):
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        db = sqlite3.connect(self.db_path)
        db.execute("drop table hook_decisions")
        db.commit()
        db.close()
        r = self.run_stats("week")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"]["saved"], 600)
        self.assertEqual(r["hook"], {"seen": 0, "rewritten": 0, "passed": 0})
        self.assertEqual(r["version"], "")

    def test_a_database_path_with_uri_characters_opens(self):
        odd = os.path.join(self.dir, "a#b?c%d")
        os.makedirs(odd)
        moved = os.path.join(odd, "history.db")
        shutil.copy(self.db_path, moved)
        self.db_path = moved
        self.add(local(2026, 10, 26, 9), inp=1000, out=400)
        r = self.run_stats("today")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["total"]["saved"], 600)

    def test_an_older_schema_without_project_path_is_a_schema_error(self):
        db = sqlite3.connect(self.db_path)
        db.execute("alter table commands drop column project_path")
        db.commit()
        db.close()
        r = self.run_stats("all")
        self.assertEqual(r["error"], "schema")
        self.assertTrue(r["todayOk"])

    def test_any_other_failure_still_prints_json(self):
        def boom(*a, **k):
            raise RuntimeError("boom")
        original = stats.summarize
        stats.summarize = boom
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                stats.main(["--db", self.db_path, "week"])
        finally:
            stats.summarize = original
        r = json.loads(buf.getvalue())
        self.assertEqual((r["ok"], r["error"], r["period"]), (False, "internal", "week"))
        self.assertIn("boom", r["detail"])

    def test_error_codes(self):
        e = sqlite3.OperationalError
        self.assertEqual(stats.error_code(e("database is locked")), "busy")
        self.assertEqual(stats.error_code(e("no such column: project_path")), "schema")
        self.assertEqual(stats.error_code(e("no such table: commands")), "schema")
        self.assertEqual(stats.error_code(e("disk I/O error")), "unreadable")
        self.assertEqual(stats.error_code(sqlite3.DatabaseError("file is not a database")), "unreadable")

    def test_the_database_is_never_written(self):
        self.add(local(2026, 10, 26, 9))
        before = os.stat(self.db_path)
        with open(self.db_path, "rb") as f:
            content = f.read()
        self.run_stats("all")
        self.run_stats("bar")
        after = os.stat(self.db_path)
        self.assertEqual((before.st_mtime_ns, before.st_size), (after.st_mtime_ns, after.st_size))
        with open(self.db_path, "rb") as f:
            self.assertEqual(f.read(), content)

    def test_output_has_no_human_words(self):
        self.add(local(2026, 10, 26, 9), path="/tmp/agent")
        self.add(local(2026, 10, 26, 9), path=os.path.expanduser("~"))
        out = json.dumps(self.run_stats("all"), ensure_ascii=False)
        self.assertIsNone(CYRILLIC.search(out))
        out = json.dumps(stats.summarize(os.path.join(self.dir, "missing.db"), "x", "all", self.now), ensure_ascii=False)
        self.assertIsNone(CYRILLIC.search(out))


class Cli(Base):
    def call(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            stats.main(list(argv))
        return json.loads(buf.getvalue())

    def test_prints_one_json_object(self):
        self.add(datetime.now().astimezone())
        r = self.call("--db", self.db_path, "--cache", self.cache_path, "week")
        self.assertTrue(r["ok"])
        self.assertEqual(r["period"], "week")
        self.assertEqual(r["total"]["commands"], 1)

    def test_default_period_is_bar(self):
        self.assertEqual(self.call("--db", self.db_path, "--cache", self.cache_path)["period"], "bar")

    def test_unknown_period_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            stats.main(["--db", self.db_path, "yesterday"])

    def test_runs_as_a_script_with_python_isolated(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stats.py")
        out = subprocess.run([sys.executable, "-I", script, "--db", os.path.join(self.dir, "none.db"), "bar"],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out)["error"], "nodb")

    def test_default_paths_follow_xdg(self):
        self.assertEqual(stats.data_home({"XDG_DATA_HOME": "/d"}), "/d")
        self.assertEqual(stats.cache_home({"XDG_CACHE_HOME": "/c"}), "/c")
        self.assertTrue(stats.data_home({}).endswith(os.path.join(".local", "share")))
        self.assertTrue(stats.cache_home({}).endswith(".cache"))
        self.assertTrue(stats.default_db().endswith(os.path.join("rtk", "history.db")))


if __name__ == "__main__":
    unittest.main()
