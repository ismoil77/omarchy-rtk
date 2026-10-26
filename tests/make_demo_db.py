#!/usr/bin/env python3
"""Writes a small, made-up rtk history for screenshots and manual checks.

    make_demo_db.py OUT_DIR

Creates OUT_DIR/history.db and a few empty git repos with worktrees under
OUT_DIR/code. The commands are spread over the last 40 days with a
working-hours rhythm, under invented projects, so nothing private ends up in a
preview. Point the widget at it with the `database` setting.
"""
import subprocess
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import stats  # noqa: E402

SCHEMA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_stats.py")).read().split('SCHEMA = """')[1].split('"""')[0]

# (repo, worktree or "", weight)
PROJECTS = [
    ("acme-api", "", 5), ("acme-api", "search", 3), ("acme-api", "billing", 2),
    ("web-shop", "", 4), ("web-shop", "checkout", 2), ("dotfiles", "", 1),
    ("", "/tmp/agent-7f3a", 1),
]
COMMANDS = [
    ("rtk grep todo src", 3000, 1900), ("rtk ls -la", 1200, 400), ("rtk read src/main.rs", 2500, 2300),
    ("rtk git status", 900, 120), ("rtk git diff HEAD", 3500, 2900), ("rtk cargo test", 6000, 400),
    ("rtk npm run build", 5200, 300), ("rtk git log -n 20", 1800, 700),
]


def make_repos(root):
    """Real folders, so stats.py groups the worktrees the way it does for real ones."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    paths = {}
    for repo, tree, _ in PROJECTS:
        if not repo:
            paths[(repo, tree)] = tree
            continue
        main = os.path.join(root, "code", repo)
        if not os.path.isdir(main):
            os.makedirs(main)
            subprocess.run(["git", "init", "-q", main], check=True, env=env)
            subprocess.run(["git", "-C", main, "-c", "user.name=demo", "-c", "user.email=d@d", "commit", "-q",
                            "--allow-empty", "-m", "init"], check=True, env=env)
        if tree:
            where = os.path.join(main, ".claude", "worktrees", tree)
            if not os.path.isdir(where):
                subprocess.run(["git", "-C", main, "worktree", "add", "-q", where], check=True, env=env)
            paths[(repo, tree)] = where
        else:
            paths[(repo, tree)] = main
    return paths


def main(root):
    rng = random.Random(7)
    os.makedirs(root, exist_ok=True)
    paths = make_repos(root)
    out = os.path.join(root, "history.db")
    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    now = datetime.now().astimezone()
    rows = []
    for days_ago in range(40, -1, -1):
        day = datetime.combine(now.date() - timedelta(days=days_ago), datetime.min.time()).astimezone()
        busy = 0 if day.weekday() >= 5 and rng.random() < 0.7 else rng.randint(20, 90)
        for _ in range(busy):
            hour = min(23, max(7, int(rng.gauss(14, 3.2))))
            when = day + timedelta(hours=hour, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
            if when > now:
                continue
            repo, tree, _ = rng.choices(PROJECTS, [w for _, _, w in PROJECTS])[0]
            path = paths[(repo, tree)]
            cmd, inp, out_tokens = rng.choice(COMMANDS)
            inp = int(inp * rng.uniform(0.5, 1.5))
            out_tokens = int(min(inp, out_tokens * rng.uniform(0.6, 1.4)))
            roll = rng.random()
            if roll < 0.04:    # rtk printed more than the raw command
                cmd, inp, out_tokens = "rtk git add -A", 40, 55
            elif roll < 0.06:  # over the 30k-character cap: left out of the totals
                inp, out_tokens = 9000, 900
            rows.append((stats.utc(when), cmd, cmd, inp, out_tokens, inp - out_tokens, 0, rng.randint(2, 900), path))
    db.executemany("insert into commands (timestamp, original_cmd, rtk_cmd, input_tokens, output_tokens,"
                   " saved_tokens, savings_pct, exec_time_ms, project_path) values (?,?,?,?,?,?,?,?,?)", rows)
    hooks = [(r[0], "s", "t", r[8], "cmd", rng.choice(["allow", "allow", "ask", "defer", "defer", "deny"]), None, "1.0.0")
             for r in rows for _ in (0, 1)]
    db.executemany("insert into hook_decisions (timestamp, session_id, tool_use_id, project_path, raw_cmd, decision,"
                   " rewritten_cmd, rtk_version) values (?,?,?,?,?,?,?,?)", hooks)
    db.commit()
    db.close()


if __name__ == "__main__":
    main(sys.argv[1])
