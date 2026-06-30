#!/usr/bin/env python3
"""
ccsessions — shared session scanner for the session.txt tool family.

One brain, three surfaces:
  - resume          per-folder, reads ./session.txt              (shell)
  - ccs             global fzf/menu picker across every repo     (this module)
  - cc-session-gui  global graphical picker                      (this module)

It enumerates coding-agent sessions from each tool's *native* store on disk and
returns unified records, so the pickers cover the same agents the per-folder
`resume` does — not just Claude.

Coverage (global pickers need a recoverable working directory):
  claude    ~/.claude/projects/*/*.jsonl                  cwd + branch + ai-title
  codex     ~/.codex/sessions/**/rollout-*.jsonl          cwd + first prompt
  opencode  ~/.local/share/opencode/opencode.db (sqlite)  directory + title
  pi        ~/.pi/agent/sessions/<enc>/*.jsonl            cwd + first prompt

cursor-agent / agent store sessions under md5(cwd) with no path on disk, so they
cannot be mapped back to a directory globally — they stay per-folder (run
`resume` inside the folder). Everything here is pure stdlib.
"""

import json
import os
import shutil
import sqlite3
import time

HOME = os.path.expanduser("~")
MAX_LINES = 120  # how deep into a jsonl transcript to look for cwd/branch/prompt

# Every tool this scanner knows about, in display order.
TOOLS = ("claude", "codex", "opencode", "pi")


# --------------------------------------------------------------------------- util
def rel_time(ts):
    d = time.time() - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    if d < 86400 * 30:
        return f"{int(d // 86400)}d ago"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
            if isinstance(part, str):
                return part
    return ""


def _clean_prompt(txt):
    """First-prompt preview: skip injected/system noise, collapse whitespace."""
    txt = (txt or "").strip()
    if not txt or txt.startswith("<") or txt.startswith("Caveat:"):
        return ""
    return " ".join(txt.split())[:200]


def _rec(tool, sid, cwd, mtime, branch="-", prompt=""):
    if not sid or not cwd:
        return None
    return {
        "tool": tool,
        "id": sid,
        "cwd": cwd,
        "repo": os.path.basename(cwd.rstrip("/")) or cwd,
        "branch": branch or "-",
        "prompt": prompt or "(no prompt)",
        "mtime": mtime,
    }


# ------------------------------------------------------------------------- claude
def _scan_claude():
    root = os.path.join(HOME, ".claude", "projects")
    out = []
    if not os.path.isdir(root):
        return out
    for d in os.listdir(root):
        pdir = os.path.join(root, d)
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, fn)
            sid = fn[:-6]
            cwd = branch = prompt = title = None
            try:
                mtime = os.path.getmtime(path)
                with open(path, "r", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i > MAX_LINES:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if cwd is None and obj.get("cwd"):
                            cwd = obj["cwd"]
                        if branch is None and obj.get("gitBranch"):
                            branch = obj["gitBranch"]
                        if title is None and obj.get("type") == "ai-title":
                            title = (obj.get("aiTitle") or "").strip() or None
                        if prompt is None and obj.get("type") == "user" and not obj.get("isMeta"):
                            msg = obj.get("message") or {}
                            if msg.get("role") == "user":
                                prompt = _clean_prompt(_extract_text(msg.get("content"))) or None
            except Exception:
                continue
            r = _rec("claude", sid, cwd, mtime, branch, title or prompt)
            if r:
                out.append(r)
    return out


# -------------------------------------------------------------------------- codex
def _scan_codex():
    root = os.path.join(HOME, ".codex", "sessions")
    out = []
    if not os.path.isdir(root):
        return out
    # layout: sessions/YYYY/MM/DD/rollout-*-<id>.jsonl
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                continue
            path = os.path.join(dirpath, fn)
            sid = cwd = prompt = None
            try:
                mtime = os.path.getmtime(path)
                with open(path, "r", errors="ignore") as f:
                    for i, line in enumerate(f):
                        if i > MAX_LINES:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if obj.get("type") == "session_meta":
                            p = obj.get("payload") or {}
                            sid = sid or p.get("id")
                            cwd = cwd or p.get("cwd")
                        if prompt is None and obj.get("type") == "response_item":
                            p = obj.get("payload") or {}
                            if p.get("role") == "user":
                                for part in p.get("content") or []:
                                    prompt = _clean_prompt(
                                        part.get("text") if isinstance(part, dict) else part
                                    ) or None
                                    if prompt:
                                        break
                        if sid and cwd and prompt:
                            break
            except Exception:
                continue
            r = _rec("codex", sid, cwd, mtime, "-", prompt)
            if r:
                out.append(r)
    return out


# ----------------------------------------------------------------------- opencode
def _scan_opencode():
    db = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
    out = []
    if not os.path.isfile(db):
        return out
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, title, directory, "
            "max(time_updated, time_created) AS ts FROM session"
        ).fetchall()
        con.close()
    except Exception:
        return out
    for row in rows:
        cwd = row["directory"]
        if not cwd:
            continue
        # opencode stores epoch milliseconds
        try:
            mtime = float(row["ts"]) / 1000.0
        except (TypeError, ValueError):
            mtime = 0.0
        r = _rec("opencode", row["id"], cwd, mtime, "-", _clean_prompt(row["title"]))
        if r:
            out.append(r)
    return out


# ------------------------------------------------------------------------------ pi
def _scan_pi():
    root = os.path.join(HOME, ".pi", "agent", "sessions")
    out = []
    if not os.path.isdir(root):
        return out
    for d in os.listdir(root):
        ddir = os.path.join(root, d)
        if not os.path.isdir(ddir):
            continue
        for fn in os.listdir(ddir):
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(ddir, fn)
            sid = cwd = prompt = None
            try:
                mtime = os.path.getmtime(path)
                with open(path, "r", errors="ignore") as f:
                    first = f.readline().strip()
                    if first:
                        try:
                            head = json.loads(first)
                            sid = head.get("id")
                            cwd = head.get("cwd")
                        except Exception:
                            pass
                    for i, line in enumerate(f):
                        if i > MAX_LINES:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        msg = obj.get("message") or {}
                        if obj.get("type") == "message" and msg.get("role") == "user":
                            for part in msg.get("content") or []:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    prompt = _clean_prompt(part.get("text")) or None
                                    if prompt:
                                        break
                        if prompt:
                            break
            except Exception:
                continue
            r = _rec("pi", sid, cwd, mtime, "-", prompt)
            if r:
                out.append(r)
    return out


_SCANNERS = {
    "claude": _scan_claude,
    "codex": _scan_codex,
    "opencode": _scan_opencode,
    "pi": _scan_pi,
}


# --------------------------------------------------------------------------- public
def scan_all(tools=None):
    """Return every session record across the requested tools, newest first."""
    out = []
    for tool in (tools or TOOLS):
        scan = _SCANNERS.get(tool)
        if not scan:
            continue
        try:
            out.extend(scan())
        except Exception:
            continue
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out


# resume command per tool: argv-after-binary. The binary is resolved at call time.
_RESUME = {
    "claude": lambda sid: ["--resume", sid],
    "codex": lambda sid: ["resume", sid],
    "opencode": lambda sid: ["--session", sid],
    "pi": lambda sid: ["--session", sid],
}


def resume_bin(tool):
    """Absolute path to a tool's binary, with a ~/.local/bin fallback."""
    return shutil.which(tool) or os.path.join(HOME, ".local", "bin", tool)


def resume_argv(session):
    """Full argv to relaunch a session record (run it in session['cwd'])."""
    tool = session["tool"]
    tail = _RESUME.get(tool, _RESUME["claude"])(session["id"])
    return [resume_bin(tool), *tail]
