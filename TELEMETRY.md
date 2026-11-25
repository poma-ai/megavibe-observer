# Telemetry: Claude Code and Codex into Grafana

The observer shows what runs now. Telemetry answers history questions: tool latency over time, latency and usage per model, how often and why work is blocked, how much you have to intervene. It is a separate stack and does not change the observer's read-only, stdlib-only constraints.

## Stack

`grafana/otel-lgtm`: one container with an OTLP receiver, Prometheus (metrics), Loki (log events), Tempo (traces) and Grafana.

Why this one:
- Both clients export OTLP natively, so no custom receiver and no protobuf parsing in Python.
- Latency, permission decisions and prompts arrive as log events with a `duration_ms` or a decision attribute. Loki holds those; a metrics-only stack cannot.
- One command, a local data volume, nothing else to wire up.

Why not the alternatives:
- A stdlib receiver in `observer.py` makes the server accept writes and forces remote hosts to push, which breaks the pull model.
- A bare OpenTelemetry Collector with a file exporter keeps the observer read-only but gives no query or dashboard layer.
- SigNoz or ClickHouse add operating cost with no benefit for one person's machines. Revisit only if LogQL queries get slow over long ranges.

Limits:
- No backfill. Data starts when you enable it, and only sessions started afterwards export.
- Nothing authenticates. Keep every port on `127.0.0.1`.
- Claude events carry `user_email`, account and organization ids. Fine on localhost; strip them in a collector before sending anywhere else.

## Run

Use `telemetry/metrics.sh start|stop|status|remove`, or the status bar (see below). It runs the container `megavibe-metrics` with:
- Grafana on `127.0.0.1:47430`, OTLP gRPC on `127.0.0.1:47317`, OTLP HTTP on `127.0.0.1:47318`. Override with `METRICS_GRAFANA_PORT`, `METRICS_OTLP_GRPC_PORT`, `METRICS_OTLP_HTTP_PORT` (the plugin reads `METRICS_GRAFANA_PORT` too; the web UI link has the port compiled in, `metricsUrl` in `ui/src/App.svelte`).
- All data in `telemetry/data/` (gitignored). Wipe history by stopping the container and deleting that folder.
- The dashboard provisioned from `telemetry/dashboard.json` through `telemetry/provisioning/dashboards.yaml`, so a fresh data folder still has it. `allowUiUpdates` is on, but edits made in the UI are lost on recreate; change the JSON instead.

Grafana login is admin / admin on first start, change it. `docker stop` keeps the container; `remove` deletes the container, not `telemetry/data/`. After changing ports, run `remove` then `start`, and update the endpoint in both client configs below.

Status bar (SwiftBar and Argos menus, not Waybar): "Start metrics" when Grafana's port is closed, "Open Metrics" and "Stop metrics" when it answers. The web UI header has a "Metrics" link that opens the dashboard in a new tab. Both are shown regardless of whether the stack is up in the UI, so the link can lead to a dead page.

## Dashboard

`telemetry/dashboard.json` (uid `megavibe-agents`, 46 panels, `host` and `session` variables). Loaded automatically by `metrics.sh start`.

Sessions: `OTEL_METRICS_INCLUDE_SESSION_ID=true` adds `session_id` (the Claude session uuid, the same id the observer UI lists) as a label on every metric and as an attribute on every log event. Despite its name the flag also controls log events; with `false` neither carries it (verified 2026-11-25 with `claude -p --settings` overrides, because the `env` block in `~/.claude/settings.json` beats a shell variable). The `session` variable reads `label_values(claude_code_session_count_total, session_id)` and filters every Claude panel; `All` matches events without a `session_id`, so history from before the flag stays in the totals. One table panel (Sessions: id, prompts, tool calls, cost) lists every session in the range and skips events without an id. It replaced three bar-gauge panels, which rendered one unlabeled bar for all sessions in Grafana 13.3 even though the query returned one series per session. The id is the raw uuid; match it to a running session with `~/.claude/sessions/<pid>.json` (`sessionId`, `name`, `cwd`). Each session adds one time series per metric, which is fine on one machine and worth stripping before sending data anywhere shared. Only sessions started after the flag exports it.

Cost: the three cost panels (Cost stat, Cost by model, and the Cost column of the Sessions table) sum `cost_usd` over `api_request` log events in Loki, not the `claude_code_cost_usage_USD_total` counter. With `session_id` off, concurrent Claude processes wrote one shared series whose stored value zigzagged between their separate running totals, and `increase()` counted every drop as a reset (measured 2026-11-25: 7-day cost $48.6 from Prometheus against $3.4 from events). The values are estimates from list prices, not subscription billing. The Tokens by model and type panel also reads the per-event token fields on `api_request` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`), one target per type. Only the active-time panel still reads a Prometheus counter, so its history before the `session_id` change is inflated the same way.

Blocked calls: the Blocked row has a table ("Blocked calls: one row per rejected call") with one row per rejected `tool_decision`: time, session, tool, `source` (`hook`, `config` or `user`), permission mode, the command binary, the full command, the note Claude gave for the call (`description`), and `skill_name` or `file_path` for other tools. It reads the Loki log frame, extracts the structured metadata from its `labels` field and then the JSON in `tool_parameters` (two `extractFields` transformations, then `organize` with an explicit column list, because otherwise every metadata field becomes a column). Command text needs `OTEL_LOG_TOOL_DETAILS=1` and exists only for sessions started after it was set; the reason text of a hook block is not exported, only that a hook blocked it. Verified 2026-11-25 with a `pkill` call blocked by `block-dangerous-bash.sh`. `OTEL_LOG_TOOL_DETAILS=1` puts the parameters of every tool call (Bash commands included, secrets in them too) into local Loki; this machine has it on in `~/.claude/settings.json`. `pattern` is a LogQL keyword, so extract that field under another label name. Every former bar-gauge panel (tool time, model time, rejected, asked, interruptions, skills) is now a table, because Grafana 13.3 drew one unlabeled bar for multi-series bar gauges.

Skills: the two skill panels in the Claude row (bar of totals, timeline) read `tool_decision` events with `tool_name="Skill"` and extract `skill_name` from the `tool_parameters` attribute. That attribute exists only when the client sets `OTEL_LOG_TOOL_DETAILS=1`, which also puts other tool parameters (for example Bash commands) into the log events. Verified live 2026-11-25 with one `claude -p` run (skill `catchup`, refused by a permission prompt, so the decision event is verified and the `tool_result` shape is not). Without the flag the panels stay empty and the other panels still show a combined `Skill` entry.

Rows and what they are for:
- Overview: prompts, tool calls, rejects, reject rate, cost, API errors for the selected range.
- Latency: tool p95 over time, API p50 and p95 per model, total seconds by tool and by model (what actually costs wall-clock), slowest single calls, MCP connect time.
- Models: requests, cost and tokens per model, cache hit ratio, output tokens per second, effort mix.
- Blocked: decisions by outcome and source, rejects by tool and source (why), permissions you were asked for, hand-accepted interruptions (candidates for an allow rule), permission mode mix.
- Your input: prompts over time, average prompt length, tool calls per prompt (how long the agent runs unattended), tool failures, API errors by status, active time.
- Codex: API latency, prompts, API errors, startup phases, plus tool latency and decisions.

Reading "why blocked": `tool_decision.source` is `config` when a rule matched (or no allow rule exists in non-interactive mode), `user_temporary` or `user_permanent` when you were asked, `hook` when a hook decided. Prompt text is never exported, so "why did I type this" is answered by proxy: hand-approved permissions, prompt length, and tool calls per prompt.

Panels that are empty until the event occurs: permissions asked of you, tool failures, API errors, Codex tool latency and decisions.

## Claude Code

`~/.claude/settings.json`, `env` block (already applied on this machine):

```json
"CLAUDE_CODE_ENABLE_TELEMETRY": "1",
"OTEL_METRICS_EXPORTER": "otlp",
"OTEL_LOGS_EXPORTER": "otlp",
"OTEL_TRACES_EXPORTER": "otlp",
"CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:47318",
"OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
"OTEL_METRICS_INCLUDE_SESSION_ID": "true",
"OTEL_METRIC_EXPORT_INTERVAL": "30000",
"OTEL_LOGS_EXPORT_INTERVAL": "5000",
"OTEL_SERVICE_NAME": "claude-code",
"OTEL_RESOURCE_ATTRIBUTES": "host.name=<this machine>"
```

Set a different `host.name` on each machine; the dashboard's `host` variable splits on it. Leave the content flags unset so nothing sensitive leaves the process: `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_RAW_API_BODIES`. Observed with them unset: prompt and response text arrive as `<REDACTED>`.

Observed events (service `claude-code`): `api_request` (model, duration_ms, tokens, cost_usd, effort), `tool_decision` (tool_name, decision, source, permission_mode), `tool_result` (tool_name, success, duration_ms), `user_prompt` (prompt_length), `api_error`, `mcp_server_connection`. Metrics: `claude_code_token_usage_tokens_total`, `claude_code_cost_usage_USD_total`, `claude_code_session_count_total`, `claude_code_active_time_seconds_total`. The trace exporter is enabled but the dashboard does not use traces.

## Codex

`~/.codex/config.toml` (created on this machine; the file did not exist before):

```toml
[otel]
environment = "local"
log_user_prompt = false
exporter = { otlp-http = { endpoint = "http://127.0.0.1:47318/v1/logs", protocol = "binary" } }
trace_exporter = { otlp-http = { endpoint = "http://127.0.0.1:47318/v1/traces", protocol = "binary" } }
metrics_exporter = { otlp-http = { endpoint = "http://127.0.0.1:47318/v1/metrics", protocol = "binary" } }
```

Endpoints are full signal paths, unlike Claude's base endpoint. Codex 0.172.0 started without rejecting any of the three exporter keys (log events arrived; I did not check Tempo or Prometheus for Codex data). Observed events (service `codex_exec`, host from the resource): `codex.conversation_starts`, `codex.user_prompt`, `codex.api_request` (model, duration_ms, error_message), `codex.startup_phase`, `codex.websocket_connect`, `codex.auth_recovery`. No Codex metrics appeared in Prometheus.

Not yet observed: `codex.tool_result` and `codex.tool_decision`. Codex was not logged in here, so every run stopped at an HTTP 401 before any tool ran. After `codex login`, run one real `codex exec` and check that those two panels fill; if the event or field names differ, fix them in the Codex row.

## Verification

Done on 2026-11-25 against a live container:
- Real `claude -p` runs (allowed and rejected tool calls, two models) produced every Claude event and metric listed above.
- A real `codex exec` produced the Codex events listed above.
- Every one of the original 42 panels' queries ran against the live Loki and Prometheus with no errors; the panels listed as empty returned no data because those events did not occur.

Not done: no visual check of the rendered layout in a browser, no multi-host run, and no tool events from Codex.

## Details that matter when editing queries

- In Loki only `service_name` is a stream label. All other attributes (`event_name`, `tool_name`, `model`, `host_name`, `duration_ms`) are structured metadata, so filter them in the pipeline: `{service_name="claude-code"} | event_name="tool_result" | host_name=~"$host"`, and use `| unwrap duration_ms` and `by (tool_name)` after that.
- The `host` variable reads `label_values(host_name)` from Prometheus, so a host appears only once it has exported a metric (Claude does; Codex does not).
- Remote hosts: `ssh -R 47318:127.0.0.1:47318 <host>` plus the same env there. Do not bind 47318 to the LAN.
