# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read-only dashboard of Claude Code / megavibe sessions across machines. Python stdlib server plus a Svelte 5 UI. See `README.md` for setup and behaviour.

## Commands

- Run: `python3 observer.py` (127.0.0.1:4747, `PORT=` overrides). Restart after editing `observer.py` or `collector/snapshot.py`.
- Build UI: `cd ui && npm install && npm run build` (output `ui/dist`, served by the server). UI-only changes need a rebuild and reload, not a restart. Hot reload: `cd ui && npm run dev` with the server running (proxies `/api`).
- Tests: `python3 -m unittest discover -s tests`. One test: `python3 -m unittest tests.test_observer.<Class>.<test>`. Tests use a scratch `HOME`.
- Collector alone: `python3 collector/snapshot.py` (snapshot JSON) or `python3 collector/snapshot.py --detail <session id>`.
- No linter is configured.

## Architecture

- `observer.py` serves `ui/dist`, `/api/snapshot` (all hosts) and `/api/detail?host=&id=` (cached 30 s). Env overrides: `OBSERVER_HOSTS`, `OBSERVER_DIST`, `PORT`.
- `collector/snapshot.py` builds one host's snapshot from `~/.claude/sessions`, `~/.claude/projects`, `~/.megavibe`, `ps` and tmux.
- The local host calls the collector in-process under `LOCAL_LOCK` (it keeps per-run state in module globals, so one call at a time). Remote hosts get `collector/snapshot.py` piped to `python3 -` over `ssh -o BatchMode=yes`, so `observer.py` reads the file into `SCRIPT` at import. A failing host returns `{ok: false, error}` and never breaks the others.
- `hosts.json` is gitignored and optional (`hosts.example.json` is the template).

## Constraints

- The collector must stay stdlib-only and Python 3.6 compatible (it runs on remote machines with whatever `python3` exists). `observer.py` needs 3.7+.
- Never emit transcript text, only ids, model and tool names, and counts. The whole tool is read-only.
- Session ids are validated against `^[0-9A-Za-z-]{1,64}$` before reaching the filesystem or ssh. `ssh` host values starting with `-` are rejected (option injection).
- The server binds localhost only. Keep it that way.
