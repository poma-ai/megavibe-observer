#!/usr/bin/env python3
"""Read-only snapshot of Claude Code / megavibe sessions on this host, printed as JSON.

Stdlib only and Python 3.6 compatible so it can be piped to a remote `python3 -` over ssh.
Never emits transcript text: only ids, model names, tool names and counts.
"""
import glob, json, os, re, shutil, socket, subprocess, sys, time

HOME = os.path.expanduser("~")
TAIL = 400_000            # bytes of transcript tail read per session
STALE_AGENT_S = 6 * 3600  # orphans of crashed runs measured at 5+ days; only alive sessions are inspected anyway
WARN_GB, CRIT_GB = 15, 8  # same thresholds as megavibe's disk-watch
SHELLS = {"bash", "zsh", "sh", "dash", "fish"}
BG_MIN_AGE_S = int(os.environ.get("OBSERVER_BG_MIN_AGE_S", "20"))  # younger shells are the current foreground tool call
WAIT_LABEL = re.compile(r"^[A-Za-z][A-Za-z ]{0,39}$")
ISO = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]{8,12}Z$")
TOKEN_KEYS = (("input", "input_tokens"), ("output", "output_tokens"),
              ("cacheWrite", "cache_creation_input_tokens"), ("cacheRead", "cache_read_input_tokens"))
DETAIL_MAX_BYTES = 200_000_000   # a transcript or subagent log bigger than this is skipped in the detail view
SESSION_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
WARNINGS = []

# A non-interactive ssh shell often lacks the directories tmux and ps live in.
os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", "")] + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])


def warn(msg):
    if msg not in WARNINGS:
        WARNINGS.append(msg)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run(cmd):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=5).stdout
    except FileNotFoundError:
        return None
    except Exception:
        return ""


def etime_s(text):
    """ps etime ([[dd-]hh:]mm:ss) -> seconds, or None."""
    try:
        days, _, rest = text.rpartition("-")
        parts = [int(x) for x in rest.split(":")]
        secs = 0
        for x in parts:
            secs = secs * 60 + x
        return secs + (int(days) * 86400 if days else 0)
    except ValueError:
        return None


def process_table():
    """pid -> parent pid, pid -> executable basename, pid -> seconds running. Never reads arguments."""
    parent, comm, running = {}, {}, {}
    out = run(["ps", "-axo", "pid=,ppid=,etime=,comm="])
    if not out:
        warn("ps failed: tmux location and Codex jobs unavailable")
        return parent, comm, running
    for line in out.splitlines():
        p = line.split(None, 3)
        if len(p) == 4 and p[0].isdigit() and p[1].isdigit():
            pid = int(p[0])
            parent[pid], comm[pid] = int(p[1]), os.path.basename(p[3].strip())
            running[pid] = etime_s(p[2])
    return parent, comm, running


def tmux_panes():
    panes = {}
    out = run(["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}:#{window_name}"])
    if out is None:
        warn("tmux not found on PATH: where shows 'terminal'")
        return panes
    for line in out.splitlines():
        pid, _, loc = line.partition(" ")
        if pid.isdigit():
            panes[int(pid)] = loc
    return panes


def locate(pid, parent, panes):
    seen = 0
    while pid and seen < 40:
        if pid in panes:
            return "tmux " + panes[pid]
        pid = parent.get(pid, 0)
        seen += 1
    return "terminal"


def transcript_path(session_id):
    hits = glob.glob(os.path.join(HOME, ".claude/projects/*", session_id + ".jsonl"))
    return hits[0] if hits else None


def read_tail(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL))
        data = f.read()
    lines = data.split(b"\n")
    return lines[1:] if size > TAIL else lines


def inspect_transcript(path):
    out = {"model": None, "agents": [], "pendingTools": [], "lastWriteAgeS": None}
    if not path:
        return out
    out["lastWriteAgeS"] = int(time.time() - os.path.getmtime(path))
    last_msg, last_uses, results = None, {}, set()
    for raw in read_tail(path):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if d.get("type") == "assistant":
            model = m.get("model")
            if isinstance(model, str) and model and not model.startswith("<"):
                out["model"] = model
            # one reply is written as several lines sharing message.id: keep only the latest reply's tool calls
            if m.get("id") != last_msg:
                last_msg, last_uses = m.get("id"), {}
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("id"):
                    last_uses[b["id"]] = str(b.get("name", "?"))
        for b in blocks:
            if b.get("type") == "tool_result" and b.get("tool_use_id"):
                results.add(b["tool_use_id"])
    out["agents"] = running_agents(path)
    out["pendingTools"] = sorted({n for i, n in last_uses.items()
                                  if i not in results and n not in ("Agent", "Task", "Workflow")})
    return out


def running_agents(transcript):
    """Agents still running for a session: `finished` is absent from <session>/subagents/*.meta.json
    until the agent stops (593 finished vs 8 unfinished, all days old, measured 2026-11-23).
    The transcript's tool_use/tool_result pairing cannot be used: async agents return a result at launch."""
    out = []
    for m in glob.glob(os.path.join(transcript[:-len(".jsonl")], "subagents", "agent-*.meta.json")):
        try:
            with open(m) as f:
                meta = json.load(f)
            if not isinstance(meta, dict) or meta.get("finished"):
                continue
            log = m[:-len(".meta.json")] + ".jsonl"
            # the log appears a moment after the meta file: fall back to the meta file's own age
            stamp = os.path.getmtime(log if os.path.exists(log) else m)
            if time.time() - stamp > STALE_AGENT_S:
                continue
        except (OSError, ValueError):
            continue
        out.append({"type": meta.get("agentType"), "model": meta.get("model")})
    return out


def provider(model):
    if not model:
        return None
    m = model.lower()
    for key, name in (("claude", "claude"), ("gpt", "codex"), ("gemini", "gemini")):
        if key in m:
            return name
    return "unknown"


def disk():
    try:
        free = shutil.disk_usage(HOME).free / 1e9
    except OSError:
        warn("disk usage unavailable")
        return {"freeGB": None, "level": "unknown"}
    level = "critical" if free < CRIT_GB else "warn" if free < WARN_GB else "ok"
    return {"freeGB": round(free, 1), "level": level}


def read_launches():
    """~/.megavibe/launches/<pid>.json, written by the `megavibe` wrapper: {pid, launcher, parentSessionId, startedAt}."""
    out = {}
    for f in glob.glob(os.path.join(HOME, ".megavibe/launches/*.json")):
        try:
            with open(f) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and type(d.get("pid")) is int and isinstance(d.get("startedAt"), (int, float)):
            par, ppid = d.get("parentSessionId"), d.get("parentPid")
            out[d["pid"]] = {"startedAt": d["startedAt"],
                             "parentSessionId": par if isinstance(par, str) and SESSION_ID.match(par) else None,
                             "parentPid": ppid if type(ppid) is int else None}
    return out


def background_shells(pid, parent, comm, running):
    """Approximate count of background work: shells running directly under the session's claude process.
    Monitors and background Bash commands run that way (checked on one live session, 2026-11-23); a command
    started with `exec` and an MCP server launched through a shell are the known misses and false hits.
    Counts only; no names, commands or arguments."""
    return sum(1 for c, p in parent.items()
               if p == pid and comm.get(c) in SHELLS and (running.get(c) or 0) >= BG_MIN_AGE_S)


def codex_jobs(parent, comm, running, panes, by_pid, sessions):
    """Running `codex` processes (the native binary of `codex exec`) and the session each belongs to.
    Linked by process ancestry, or, for the context watcher that megavibe runs in tmux session mvw-<first 8
    characters of the session id>, by that name. Not linked when neither applies. Arguments are never read."""
    jobs = []
    for pid, name in comm.items():
        if name != "codex" or comm.get(parent.get(pid)) == "codex":
            continue
        link, via, x, hops = None, None, parent.get(pid), 0
        while x and x > 1 and hops < 40:
            if x in by_pid:
                link, via = by_pid[x], "process"
                break
            m = re.match(r"^mvw-([0-9a-f]{8})", panes.get(x, ""))
            if m:
                link = next((r["sessionId"] for r in sessions if r["sessionId"].startswith(m.group(1))), None)
                via = "watcher" if link else None
                break
            x, hops = parent.get(x), hops + 1
        jobs.append({"kind": "codex", "runningS": running.get(pid), "parentSessionId": link, "via": via})
    return jobs


def safe_name(v, default="?"):
    return re.sub(r"[^A-Za-z0-9_.:-]", "?", v)[:40] if isinstance(v, str) and v else default


def plan_usage():
    """Account-wide plan usage the megavibe statusline logs (5-hour and 7-day windows), or None."""
    path = os.path.join(HOME, ".megavibe/usage/claude.jsonl")
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            lines = f.read().split(b"\n")
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict) or not isinstance(d.get("t"), (int, float)):
            continue
        out = {"at": int(d["t"] * 1000)}
        for key, name in (("fh", "fiveHour"), ("sd", "sevenDay")):
            w = d.get(key)
            if isinstance(w, dict) and isinstance(w.get("p"), (int, float)):
                out[name] = {"pct": w["p"], "resetsAt": int(w["r"] * 1000) if isinstance(w.get("r"), (int, float)) else None}
        if "fiveHour" in out or "sevenDay" in out:
            return out
    return None


def aggregate(path):
    """Totals for one transcript: assistant messages (deduplicated by message.id: one reply is written as several
    lines with the same usage), tokens, models and tool calls. Counts and names only, never text."""
    msgs, tools_seen, first, last = {}, {}, None, None
    with open(path, "rb") as f:
        for raw in f:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp")
            if isinstance(ts, str) and ISO.match(ts):
                first, last = first or ts, ts
            m = d.get("message")
            if d.get("type") != "assistant" or not isinstance(m, dict):
                continue
            if m.get("id") and isinstance(m.get("usage"), dict):
                msgs[m["id"]] = (m.get("model"), m["usage"])
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                        tools_seen[b["id"]] = safe_name(b.get("name"))
    tokens, models, tools = {k: 0 for k, _ in TOKEN_KEYS}, {}, {}
    for model, u in msgs.values():
        for k, src in TOKEN_KEYS:
            v = u.get(src)
            tokens[k] += v if type(v) is int else 0
        if isinstance(model, str) and not model.startswith("<"):
            models[safe_name(model)] = models.get(safe_name(model), 0) + 1
    for n in tools_seen.values():
        tools[n] = tools.get(n, 0) + 1
    return {"messages": len(msgs), "tokens": tokens, "models": models, "tools": tools, "first": first, "last": last}


def detail(sid):
    """Everything the click-through panel shows for one session, computed on demand (a transcript can be large)."""
    out = {"host": socket.gethostname().split(".")[0], "sessionId": sid, "found": False, "truncated": False}
    path = transcript_path(sid) if SESSION_ID.match(sid) else None
    if not path or os.path.getsize(path) > DETAIL_MAX_BYTES:
        out["truncated"] = bool(path)
        return out
    main_agg = aggregate(path)
    out["found"] = True
    out["session"] = {"messages": main_agg["messages"], "tokens": main_agg["tokens"], "models": main_agg["models"],
                      "toolCalls": sum(main_agg["tools"].values()),
                      "tools": sorted(main_agg["tools"].items(), key=lambda kv: -kv[1])[:8],
                      "first": main_agg["first"], "last": main_agg["last"]}
    groups, total = {}, {"count": 0, "running": 0, "messages": 0, "tokens": {k: 0 for k, _ in TOKEN_KEYS}}
    for m in sorted(glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "agent-*.meta.json")))[:500]:
        try:
            with open(m) as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        log = m[:-len(".meta.json")] + ".jsonl"
        running = False
        try:
            if not meta.get("finished"):
                running = time.time() - os.path.getmtime(log if os.path.exists(log) else m) <= STALE_AGENT_S
        except OSError:
            pass
        g = groups.setdefault((safe_name(meta.get("agentType")), safe_name(meta.get("model"))),
                              {"count": 0, "running": 0, "messages": 0, "tokens": {k: 0 for k, _ in TOKEN_KEYS}})
        g["count"] += 1
        g["running"] += 1 if running else 0
        try:
            if os.path.exists(log) and os.path.getsize(log) <= DETAIL_MAX_BYTES:
                a = aggregate(log)
                g["messages"] += a["messages"]
                for k in g["tokens"]:
                    g["tokens"][k] += a["tokens"][k]
        except OSError:
            out["truncated"] = True
    rows = []
    for (typ, model), g in groups.items():
        rows.append(dict(g, type=typ, model=model))
        total["count"] += g["count"]; total["running"] += g["running"]; total["messages"] += g["messages"]
        for k in total["tokens"]:
            total["tokens"][k] += g["tokens"][k]
    out["agents"] = dict(total, byType=sorted(rows, key=lambda r: -r["count"]))
    out["projectSessions"] = len(glob.glob(os.path.join(os.path.dirname(path), "*.jsonl")))
    return out


def session_row(s, parent, panes):
    pid, sid = s.get("pid"), s.get("sessionId")
    if type(pid) is not int or pid <= 1 or not alive(pid):
        return None
    if not isinstance(sid, str) or not SESSION_ID.match(sid):
        return None
    t = inspect_transcript(transcript_path(sid))
    status = s.get("status")
    attention = None
    if s.get("waitingFor") or status == "waiting":
        # The registry reports every wait itself: status "waiting" with waitingFor "input needed"
        # (AskUserQuestion) or "permission prompt" (tool approval), both observed 2026-11-23.
        # A transcript heuristic for prompts was removed once this was confirmed: it false-alarmed.
        label = s.get("waitingFor")
        label = label if isinstance(label, str) and WAIT_LABEL.match(label) else "waiting for input"
        attention = {"kind": "waiting", "why": label}
    started = s.get("startedAt")
    return {
        "sessionId": sid, "pid": pid, "name": s.get("name"), "cwd": s.get("cwd"),
        "kind": s.get("kind"), "version": s.get("version"), "status": status,
        "startedAt": started if isinstance(started, (int, float)) else None, "updatedAt": s.get("updatedAt"),
        "where": locate(pid, parent, panes), "model": t["model"], "provider": provider(t["model"]),
        "agents": len(t["agents"]), "agentList": t["agents"], "attention": attention,
    }


def snapshot():
    """One snapshot of this host as a dict (the server calls this in-process; main() prints it for ssh)."""
    del WARNINGS[:]
    parent, comm, running = process_table()
    panes = tmux_panes()
    sessions, skipped = [], 0
    for f in glob.glob(os.path.join(HOME, ".claude/sessions/*.json")):
        try:
            with open(f) as fh:
                s = json.load(fh)
            row = session_row(s, parent, panes) if isinstance(s, dict) else None
        except Exception as e:  # one odd file must not hide the other sessions
            skipped += 1
            warn("session file skipped: " + type(e).__name__)
            continue
        if row:
            sessions.append(row)
    # Sub-sessions. Primary link: the launch record `megavibe` writes (works through tmux, ssh, anything), joined
    # by pid and accepted only if its start time matches the session's (pid reuse). Fallback: a session spawned
    # from another one's tool (e.g. `claude -p`) has that session's `claude` process as an ancestor.
    by_pid = {r["pid"]: r["sessionId"] for r in sessions}
    known = set(by_pid.values())
    by_start = {r["pid"]: r["startedAt"] for r in sessions}
    launches = read_launches()
    for r in sessions:
        rec = launches.get(r["pid"])
        matched = bool(rec) and r["startedAt"] is not None and abs(rec["startedAt"] * 1000 - r["startedAt"]) < 300_000
        r["launchedBy"] = "megavibe" if matched else None
        link = rec["parentSessionId"] if matched else None
        ppid = rec["parentPid"] if matched else None
        if ppid in by_pid and ppid != r["pid"] and (by_start.get(ppid) or 0) <= (r["startedAt"] or 0) + 5000:
            link = by_pid[ppid]   # the parent's session id changes on /clear; its pid does not. A parent started before its child: a reused pid did not
        elif link == r["sessionId"] or link not in known:
            link = None
        x, hops = parent.get(r["pid"]), 0
        while link is None and x and x > 1 and hops < 40:
            if x in by_pid and x != r["pid"]:
                link = by_pid[x]
            x, hops = parent.get(x), hops + 1
        r["parentSessionId"] = link
    for r in sessions:
        r["bgShells"] = background_shells(r["pid"], parent, comm, running)
    jobs = codex_jobs(parent, comm, running, panes, by_pid, sessions)
    sessions.sort(key=lambda x: x["startedAt"] or 0)
    return {"host": socket.gethostname().split(".")[0], "collectedAt": int(time.time() * 1000),
            "python": "%d.%d.%d" % sys.version_info[:3], "disk": disk(), "plan": plan_usage(), "sessions": sessions,
            "jobs": jobs, "warnings": list(WARNINGS)}


def main():
    json.dump(snapshot(), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--detail":
        json.dump(detail(sys.argv[2]), sys.stdout)
        sys.stdout.write("\n")
    else:
        main()
