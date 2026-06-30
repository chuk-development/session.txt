#!/usr/bin/env python3
"""
Deterministic test for the shared scanner (ccsessions.py).

Builds a fake $HOME with one session per supported tool, then asserts scan_all()
finds them all with the right cwd/prompt and that resume_argv() emits the correct
relaunch command. No network, no real agent stores touched.
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

fails = 0


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


def write_jsonl(path, objs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


def build_home(home):
    # --- claude ---
    write_jsonl(os.path.join(home, ".claude/projects/proj/aaa.jsonl"), [
        {"cwd": "/repo/alpha", "gitBranch": "main"},
        {"type": "user", "message": {"role": "user", "content": "Fix the parser"}},
        {"type": "ai-title", "aiTitle": "Fix parser bug"},
    ])
    # --- codex ---
    write_jsonl(os.path.join(home, ".codex/sessions/2026/06/30/rollout-x-bbb.jsonl"), [
        {"type": "session_meta", "payload": {"id": "bbb", "cwd": "/repo/beta"}},
        {"type": "response_item", "payload": {"role": "user",
            "content": [{"type": "text", "text": "Refactor renderer"}]}},
    ])
    # --- pi ---
    write_jsonl(os.path.join(home, ".pi/agent/sessions/--repo-gamma--/ccc.jsonl"), [
        {"id": "ccc", "cwd": "/repo/gamma"},
        {"type": "message", "message": {"role": "user",
            "content": [{"type": "text", "text": "Add tests"}]}},
    ])
    # --- opencode (sqlite) ---
    db = os.path.join(home, ".local/share/opencode/opencode.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id TEXT, title TEXT, directory TEXT, "
                "time_created INTEGER, time_updated INTEGER)")
    con.execute("INSERT INTO session VALUES ('ddd','Build the API','/repo/delta',"
                "1000000,2000000)")
    con.commit()
    con.close()


def main():
    with tempfile.TemporaryDirectory() as home:
        build_home(home)
        os.environ["HOME"] = home
        # import AFTER HOME is set (module resolves ~ at import)
        import importlib
        import ccsessions
        importlib.reload(ccsessions)

        sessions = ccsessions.scan_all()
        by_tool = {s["tool"]: s for s in sessions}

        for tool in ("claude", "codex", "opencode", "pi"):
            check(tool in by_tool, f"{tool} session found")

        check(by_tool["claude"]["cwd"] == "/repo/alpha", "claude cwd")
        check(by_tool["claude"]["branch"] == "main", "claude branch")
        check(by_tool["claude"]["prompt"] == "Fix parser bug", "claude uses ai-title")
        check(by_tool["codex"]["prompt"] == "Refactor renderer", "codex prompt")
        check(by_tool["pi"]["prompt"] == "Add tests", "pi prompt")
        check(by_tool["opencode"]["prompt"] == "Build the API", "opencode title")
        check(by_tool["opencode"]["cwd"] == "/repo/delta", "opencode directory")

        # resume_argv tails (binary path varies per machine, check the suffix)
        cases = {
            "claude": ["--resume", "aaa"],
            "codex": ["resume", "bbb"],
            "opencode": ["--session", "ddd"],
            "pi": ["--session", "ccc"],
        }
        for tool, tail in cases.items():
            argv = ccsessions.resume_argv(by_tool[tool])
            check(argv[1:] == tail, f"{tool} resume_argv = {tail}")

    print()
    if fails:
        print(f"FAILED: {fails} assertion(s)")
        sys.exit(1)
    print("all scanner assertions passed")


if __name__ == "__main__":
    main()
