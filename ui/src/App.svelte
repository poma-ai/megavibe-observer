<script>
  // Grafana from telemetry/metrics.sh; same port as METRICS_GRAFANA_PORT there. The host follows the page so remote access works through the same name.
  const metricsUrl = `http://${location.hostname}:47430/d/megavibe-agents`;
  let playing = $state(false);
  // x%, seconds per rush, start delay: each line carries a flash that rushes down it, then rests.
  const bolts = [[4,9,0],[13,12,5],[27,10,2],[36,14,8],[47,8,3.5],[58,13,6],[66,11,1],[74,15,10],[85,9.5,4.5],[95,12.5,7]];
  let hosts = $state([]);
  let error = $state(null);
  let fetchedAt = $state(null);
  let now = $state(Date.now());

  // Click-through detail: one session's totals, parsed on demand by the collector.
  let selected = $state(null);
  let detail = $state(null);
  let detailErr = $state(null);
  let detailLoading = $state(false);
  async function openDetail(s) {
    selected = { host: s.host, sessionId: s.sessionId };
    detail = null; detailErr = null; detailLoading = true;
    try {
      const r = await fetch(`/api/detail?host=${encodeURIComponent(s.host)}&id=${encodeURIComponent(s.sessionId)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      if (selected?.sessionId === s.sessionId) detail = d;
    } catch (e) { detailErr = String(e); }
    finally { if (selected?.sessionId === s.sessionId) detailLoading = false; }
  }
  let termMsg = $state('');
  let termBusy = $state(false);
  async function openTerminal(s) {
    if (termBusy) return;
    termBusy = true; termMsg = '';
    try {
      const r = await fetch('/api/open-terminal', { method: 'POST', headers: { 'content-type': 'application/json', 'x-observer': '1' }, body: JSON.stringify({ host: s.host, id: s.sessionId }) });
      const j = await r.json().catch(() => ({}));
      termMsg = r.ok ? '' : (j.message ?? `failed (${r.status})`);
    } catch (e) { termMsg = String(e); }
    finally { termBusy = false; }
  }
  const closeDetail = () => { selected = null; detail = null; detailErr = null; detailLoading = false; };
  const compact = (n) => new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n ?? 0);
  const when = (iso) => (iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
  const resets = (ms) => (ms ? new Date(ms).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '');
  const tokenRows = [['output', 'Output'], ['input', 'Input (fresh)'], ['cacheWrite', 'Cache written'], ['cacheRead', 'Cache read']];

  const sessions = $derived(hosts.flatMap((h) => h.sessions.map((s) => ({ ...s, host: h.name, diskLevel: h.disk?.level }))));
  const needsAttention = (s) => s.attention || s.diskLevel === 'critical';
  const rank = (s) => (needsAttention(s) ? 0 : s.status === 'busy' ? 1 : 2);
  const jobsOf = (s) => (hosts.find((h) => h.name === s.host)?.jobs ?? []).filter((j) => j.parentSessionId === s.sessionId);
  const dur = (sec) => (sec == null ? '' : sec < 60 ? `${sec}s` : sec < 3600 ? `${Math.floor(sec / 60)}m` : `${Math.floor(sec / 3600)}h`);
  const kidsOf = (s) => sessions.filter((k) => k.host === s.host && k.parentSessionId === s.sessionId);
  const roots = $derived(sessions.filter((s) => !s.parentSessionId || !sessions.some((p) => p.host === s.host && p.sessionId === s.parentSessionId)));
  // Sub-sessions that share a working directory (e.g. the dozens of headless workers of one amass sweep) show as one row.
  const groupsOf = (s) => {
    const m = new Map();
    for (const k of kidsOf(s)) {
      const key = k.cwd ?? k.sessionId;
      if (!m.has(key)) m.set(key, []);
      m.get(key).push(k);
    }
    return [...m.values()].map((list) => ({
      first: list[0], n: list.length,
      busy: list.filter((k) => k.status === 'busy').length,
      attn: list.find((k) => k.attention),
      label: list.length > 1 ? (list[0].cwd ?? '').split('/').filter(Boolean).pop() || list[0].name : list[0].name,
    }));
  };
  const attnDeep = (s) => needsAttention(s) || kidsOf(s).some(needsAttention);
  const rankDeep = (s) => (attnDeep(s) ? 0 : s.status === 'busy' || kidsOf(s).some((k) => k.status === 'busy') ? 1 : 2);
  const sorted = $derived([...roots].sort((a, b) => rankDeep(a) - rankDeep(b)));
  const totals = $derived({
    sessions: roots.length,
    subs: sessions.length - roots.length,
    busy: sessions.filter((s) => s.status === 'busy').length,
    agents: sessions.reduce((n, s) => n + (s.agents ?? 0), 0),
    attention: sessions.filter(needsAttention).length,
  });

  // History tab: past sessions from the transcripts, fetched when the tab opens (the live poll stays small).
  let tab = $state('live');
  let hist = $state([]);
  let histErr = $state(null);
  let histLoading = $state(false);
  let showTmp = $state(false);
  let histFilter = $state('');
  async function loadHistory() {
    histLoading = true;
    try {
      const r = await fetch('/api/history' + (showTmp ? '?tmp=1' : ''));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      hist = (await r.json()).hosts;
      histErr = null;
    } catch (e) { histErr = String(e); }
    finally { histLoading = false; }
  }
  $effect(() => { if (tab === 'history') { showTmp; loadHistory(); } });
  const liveIds = $derived(new Set(sessions.map((s) => s.host + ' ' + s.sessionId)));
  const histRows = $derived(
    hist.flatMap((h) => (h.history ?? []).map((r) => ({ ...r, host: h.name })))
      .filter((r) => !histFilter || (r.cwd ?? r.project).toLowerCase().includes(histFilter.toLowerCase()))
      .sort((a, b) => b.lastMs - a.lastMs)
  );
  // One card per project (host + working directory): session names are unique per run (`<project>-<n>`), so the
  // project is the stable thing that sessions share.
  const histGroups = $derived.by(() => {
    const m = new Map();
    for (const r of histRows) {
      const cwd = r.cwd ?? r.project;
      const key = r.host + ' ' + cwd;
      if (!m.has(key)) m.set(key, { key, cwd, host: r.host, name: cwd.split('/').filter(Boolean).pop() || cwd, rows: [], live: 0, firstMs: Infinity });
      const g = m.get(key);
      g.rows.push(r);
      if (liveIds.has(r.host + ' ' + r.sessionId)) g.live++;
      g.firstMs = Math.min(g.firstMs, r.first ? Date.parse(r.first) : r.lastMs);
    }
    return [...m.values()];
  });
  let group = $state(null);
  const groupSel = $derived(histGroups.find((g) => g.key === group));
  const hiddenTmp = $derived(hist.reduce((n, h) => n + (h.hiddenTmp ?? 0), 0));
  const span = (r) => {
    const a = r.first ? Date.parse(r.first) : null;
    return a ? dur(Math.max(0, Math.round((r.lastMs - a) / 1000))) : '–';
  };
  const stamp = (ms) => new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const mb = (b) => (b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB');

  let polling = $state(false);
  async function pollNow() { polling = true; await poll(); polling = false; }
  const ago = (ms) => { const m = Math.max(0, Math.round((now - ms) / 60000)); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : Math.round(m / 60) + ' h ago'; };

  async function poll() {
    try {
      const r = await fetch('/api/snapshot');
      hosts = (await r.json()).hosts;
      fetchedAt = Date.now();
      error = null;
    } catch (e) { error = String(e); }
  }
  $effect(() => {
    poll();
    const a = setInterval(poll, 5000);
    const b = setInterval(() => (now = Date.now()), 1000);
    return () => { clearInterval(a); clearInterval(b); };
  });

  const age = (ms) => {
    if (!ms) return '–';
    const s = Math.max(0, Math.floor((now - ms) / 1000));
    return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  };
  const short = (p) => (p ?? '').replace(/^\/Users\/[^/]+/, '~');
</script>

{#if playing}
  <div class="gears" aria-hidden="true">
    {#each bolts as [x, secs, delay]}
      <i class="bolt" style="left:{x}%;--d:{secs}s;--w:{delay}s"></i>
    {/each}
  </div>
{/if}

<main>
  <header>
    <div>
      <h1>Megavibe Observer</h1>
      <p class="sub">{fetchedAt ? 'updated ' + age(fetchedAt) + ' ago' : 'connecting…'}</p>
    </div>
    <div class="actions">
      <a class="metrics" href={metricsUrl} target="_blank" rel="noopener noreferrer">Metrics ↗</a>
      <button class="metrics play" class:on={playing} onclick={() => (playing = !playing)} aria-pressed={playing} aria-label={playing ? 'Stop flashes' : 'Play flashes'} title={playing ? 'Stop' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
    </div>
  </header>

  {#if error}<p class="banner">Server unreachable: {error}</p>{/if}

  <div class="toolbar">
    <nav class="tabs" aria-label="View">
      <button class:on={tab === 'live'} onclick={() => (tab = 'live')}>Live</button>
      <button class:on={tab === 'history'} onclick={() => (tab = 'history')}>History</button>
    </nav>
    {#if tab === 'live'}
    <section class="hosts">
      {#each hosts as h (h.name)}
        <div class="chip" class:down={!h.ok}>
          <span class="dot {h.ok ? h.disk.level : 'critical'}"></span>
          <b>{h.name}</b>
          {#if h.ok}
            <span class="mut">{h.sessions.length} sessions · {h.disk.freeGB} GB free{(h.jobs ?? []).length ? ` · ${h.jobs.length} codex job${h.jobs.length > 1 ? 's' : ''}` : ''}</span>
          {:else}
            <span class="crit">unreachable: {h.error}</span>
          {/if}
        </div>
      {/each}
    </section>
    {/if}
  </div>

  {#if tab === 'history'}
    <section class="hist">
      <div class="hbar">
        <input type="search" placeholder="Filter by project path" bind:value={histFilter} aria-label="Filter history" />
        <label><input type="checkbox" bind:checked={showTmp} /> show temp-dir sessions{!showTmp && hiddenTmp ? ` (${hiddenTmp} hidden)` : ''}</label>
      </div>
      {#if histErr}<p class="err">Could not load history: {histErr}</p>{/if}
      {#each hist.filter((h) => !h.ok) as h}<p class="err">{h.name}: {h.error}</p>{/each}
      {#if histLoading && !hist.length}<p class="mut">Reading transcripts…</p>{/if}
      <div class="grid">
        {#each histGroups as g (g.key)}
          <article class="card" role="button" tabindex="0" onclick={() => (group = g.key)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); group = g.key; } }}>
            <div class="top">
              <h2 title={g.cwd}>{g.name}</h2>
              {#if g.live}<span class="badge busy">{g.live} live</span>{/if}
            </div>
            <p class="path" title={g.cwd}>{short(g.cwd)}</p>
            <dl>
              <div><dt>Host</dt><dd>{g.host}</dd></div>
              <div><dt>Sessions</dt><dd>{g.rows.length}</dd></div>
              <div><dt>Last activity</dt><dd>{stamp(g.rows[0].lastMs)}</dd></div>
              <div><dt>First seen</dt><dd>{stamp(g.firstMs)}</dd></div>
            </dl>
          </article>
        {:else}
          {#if !histLoading}<p class="empty">No past sessions.</p>{/if}
        {/each}
      </div>
      {#if hist.some((h) => h.capped)}<p class="note">Showing the most recent sessions per host only.</p>{/if}
    </section>
  {:else}
  <section class="stats">
    <div class="stat"><span class="n">{totals.sessions}</span><span class="l">sessions{totals.subs ? ` (+${totals.subs} sub)` : ''}</span></div>
    <div class="stat busy"><span class="n">{totals.busy}</span><span class="l">busy</span></div>
    <div class="stat"><span class="n">{totals.agents}</span><span class="l">agents running</span></div>
    <div class="stat" class:alert={totals.attention}><span class="n">{totals.attention}</span><span class="l">need attention</span></div>
  </section>

  {#each hosts.filter((h) => h.ok && (h.plan || h.codexPlan)) as h (h.name)}
    <section class="usage">
      <div class="uhead">
        <h3>Plan usage <span class="mut">(account-wide{hosts.length > 1 ? ', ' + h.name : ''})</span></h3>
        <span class="mut">as of {ago(Math.max(h.plan?.at ?? 0, h.codexPlan?.at ?? 0))}</span>
        <button class="poll" onclick={pollNow} disabled={polling}>{polling ? 'Polling…' : 'Poll now'}</button>
      </div>
      <div class="pcols">
      {#each [['Claude', h.plan], ['Codex', h.codexPlan]] as [prov, plan]}
        {#if plan}
          <div class="pcol">
          <div class="prov"><span class="tag" class:codex={prov === 'Codex'} class:claude={prov === 'Claude'}>{prov}</span>{#if h.plan && h.codexPlan}<span class="mut small">as of {ago(plan.at)}</span>{/if}</div>
          <div class="ubars">
            {#each [['5-hour window', plan.fiveHour], ['7-day window', plan.sevenDay]] as [label, w]}
              {#if w}
                <div class="ubar">
                  <div class="brow"><span>{prov} · {label}</span><b>{w.pct}%</b></div>
                  <div class="track" role="progressbar" aria-label="{prov} {label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow={w.pct}>
                    <div class="fill" class:warn={w.pct >= 75} class:crit={w.pct >= 90} style="width:{Math.min(100, Math.max(0, w.pct))}%"></div>
                  </div>
                  <span class="mut small">resets {resets(w.resetsAt)}</span>
                </div>
              {/if}
            {/each}
          </div>
          </div>
        {/if}
      {/each}
      </div>
    </section>
  {/each}

  <section class="grid">
    {#each sorted as s (s.host + s.sessionId)}
      <article class="card" class:attn={attnDeep(s)} role="button" tabindex="0" onclick={() => openDetail(s)}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(s); } }}>
        <div class="top">
          <h2 title={s.name}><span class="nm">{s.name}</span>{#if s.launchedBy}<span class="tag" title="started by the megavibe wrapper">megavibe</span>{/if}</h2>
          {#if s.attention}
            <span class="badge warn" title={s.attention.why}>waiting</span>
          {:else if s.diskLevel === 'critical'}
            <span class="badge crit">blocked: disk</span>
          {:else}
            <span class="badge {s.status}">{s.status}</span>
          {/if}
        </div>
        <p class="path" title={s.cwd}>{short(s.cwd)}</p>
        <dl>
          <div><dt>Host</dt><dd>{s.host}</dd></div>
          <div class="wide"><dt>Where</dt><dd>{s.where}</dd></div>
          <div><dt>Model</dt><dd>{s.model ?? '–'}</dd></div>
          <div><dt>Provider</dt><dd>{s.provider ?? '–'}</dd></div>
          <div><dt>Agents</dt><dd>{s.agents}</dd></div>
          <div><dt>Background</dt><dd title="Shells running under the session (monitors, background commands). Approximate: misses commands started with exec.">{s.bgShells ? `~${s.bgShells} shell${s.bgShells > 1 ? 's' : ''}` : '–'}</dd></div>
          <div><dt>Running</dt><dd>{age(s.startedAt)}</dd></div>
        </dl>
        {#if s.agentList?.length}
          <ul class="agents">
            {#each s.agentList as a}<li>{a.type ?? 'agent'}<span>{a.model ?? ''}</span></li>{/each}
          </ul>
        {/if}
        {#if jobsOf(s).length}
          <ul class="agents jobs">
            {#each jobsOf(s) as j}<li>codex job<span>{dur(j.runningS)}{j.via === 'watcher' ? ' · context watcher' : ''}</span></li>{/each}
          </ul>
        {/if}
        {#if kidsOf(s).length}
          <div class="subs">
            <span class="subh">Sub-sessions</span>
            {#each groupsOf(s) as g (g.first.sessionId)}
              <div class="sub link" role="button" tabindex="0" title="Open stats for this sub-session"
                onclick={(e) => { e.stopPropagation(); openDetail(g.first); }}
                onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openDetail(g.first); } }}>
                <span class="badge {g.attn ? 'warn' : g.busy ? 'busy' : 'idle'}">{g.attn ? g.attn.attention.why : g.busy ? 'busy' : 'idle'}</span>
                <span class="sn" title={g.first.cwd}>{g.label}{g.n > 1 ? ` ×${g.n}` : ''}</span>
                <span class="mut">{g.first.model ?? ''}</span>
              </div>
            {/each}
          </div>
        {/if}
        {#if s.attention}<p class="why">{s.attention.why}</p>{/if}
      </article>
    {:else}
      <p class="empty">No live sessions.</p>
    {/each}
  </section>
  {/if}

</main>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') { if (selected) closeDetail(); else if (group) group = null; } }} />

{#if groupSel}
  <div class="scrim" onclick={() => (group = null)} role="presentation"></div>
  <aside class="panel" role="dialog" aria-modal="true" aria-label="Session history">
    <header class="phead">
      <div>
        <h2>{groupSel.name}</h2>
        <p class="path">{short(groupSel.cwd)} · {groupSel.host}</p>
      </div>
      <button class="close" onclick={() => (group = null)} aria-label="Close history">✕</button>
    </header>
    <p class="note">{groupSel.rows.length} session{groupSel.rows.length > 1 ? 's' : ''}, newest first. Click one for its totals.</p>
    <ul class="hlist">
      {#each groupSel.rows as r (r.sessionId)}
        <li>
          <button class="hrow" onclick={() => openDetail(r)}>
            <span class="hmain">
              <b>{stamp(r.lastMs)}</b>
              {#if liveIds.has(r.host + ' ' + r.sessionId)}<span class="badge busy">live</span>{/if}
              <span class="mut path-inline">{r.branch ?? ''}</span>
            </span>
            <span class="mut hmeta">{span(r)} · {mb(r.bytes)}</span>
          </button>
        </li>
      {/each}
    </ul>
  </aside>
{/if}

{#if selected}
  {@const row = sessions.find((x) => x.host === selected.host && x.sessionId === selected.sessionId)}
  {@const tmuxLive = row?.where?.startsWith('tmux ')}
  <div class="scrim above" onclick={closeDetail} role="presentation"></div>
  <aside class="panel above" role="dialog" aria-modal="true" aria-label="Session details">
    <header class="phead">
      <div>
        <h2>{row?.name ?? 'Session ended'}</h2>
        <p class="path">{short(row?.cwd)}{row ? ` · ${selected.host}` : ''}</p>
        {#if row}<p class="path">{row.where}{row.launchedBy ? ' · megavibe' : ''}</p>{/if}
        <button class="term" disabled={termBusy || (!!row && !tmuxLive)} onclick={() => openTerminal(selected)}
          title={!row ? 'Resume this ended session in a new Terminal window' : tmuxLive ? 'Attach to this tmux session in a new Terminal window' : 'Running in a plain terminal: cannot be opened from here'}>{!row ? 'Resume in terminal' : tmuxLive ? 'Attach in terminal' : 'Open in terminal'}</button>
      </div>
      <button class="close" onclick={closeDetail} aria-label="Close details">✕</button>
    </header>
    {#if termMsg}<p class="err">Could not open terminal: {termMsg}</p>{/if}

    {#if detailLoading}
      <p class="mut">Reading the transcript…</p>
    {:else if detailErr}
      <p class="err">Could not load details: {detailErr}</p>
    {:else if detail?.found}
      {@const d = detail}
      {@const maxTool = Math.max(1, ...d.session.tools.map((t) => t[1]))}
      <section>
        <h3>Overview <span class="tag claude">Claude</span></h3>
        <dl class="kv">
          <div><dt>Running</dt><dd>{row ? age(row.startedAt) : '–'}</dd></div>
          <div><dt>Assistant messages</dt><dd>{d.session.messages}</dd></div>
          <div><dt>Tool calls</dt><dd>{d.session.toolCalls}</dd></div>
          <div><dt>Sessions in this project</dt><dd>{d.projectSessions}</dd></div>
          <div><dt>First activity</dt><dd>{when(d.session.first)}</dd></div>
          <div><dt>Last activity</dt><dd>{when(d.session.last)}</dd></div>
        </dl>
      </section>

      <section>
        <h3>Tokens <span class="tag claude">Claude</span></h3>
        <dl class="kv">
          {#each tokenRows as [k, label]}
            <div class:dim={k === 'cacheRead'}><dt>{label}</dt><dd title={d.session.tokens[k].toLocaleString()}>{compact(d.session.tokens[k])}</dd></div>
          {/each}
        </dl>
        <p class="note">Cache read is the context re-read on every turn, not new work: it dwarfs the rest and is not added up. Counted per message, each reply once.</p>
      </section>

      <section>
        <h3>Models <span class="tag claude">Claude</span></h3>
        <ul class="plain">
          {#each Object.entries(d.session.models) as [m, n]}<li><span>{m}</span><span class="mut">{n} messages</span></li>{/each}
        </ul>
      </section>

      {#if d.session.tools.length}
        <section>
          <h3>Top tools <span class="tag claude">Claude</span></h3>
          <ul class="bars">
            {#each d.session.tools as [name, n]}
              <li><span class="bn">{name}</span><span class="bar"><i style:width="{(n / maxTool) * 100}%"></i></span><span class="bv">{n}</span></li>
            {/each}
          </ul>
        </section>
      {/if}

      <section>
        <h3>Skills used <span class="tag claude">Claude</span></h3>
        {#if d.session.skills?.length}
          <ul class="plain">
            {#each d.session.skills as [name, n]}<li><span>{name}</span><span class="mut">{n}×</span></li>{/each}
          </ul>
        {:else}
          <p class="mut">No skills were used in this session.</p>
        {/if}
      </section>

      <section>
        <h3>Agents <span class="tag claude">Claude</span></h3>
        {#if d.agents.count === 0}
          <p class="mut">No subagents were started in this session.</p>
        {:else}
          <dl class="kv">
            <div><dt>Started</dt><dd>{d.agents.count}</dd></div>
            <div><dt>Running now</dt><dd>{d.agents.running}</dd></div>
            <div><dt>Messages</dt><dd>{d.agents.messages}</dd></div>
            <div><dt>Output tokens</dt><dd>{compact(d.agents.tokens.output)}</dd></div>
          </dl>
          <ul class="plain agents-list">
            {#each d.agents.byType as r}
              <li>
                <span>{r.type} <span class="mut">{r.model}</span></span>
                <span class="mut">×{r.count}{r.running ? ` · ${r.running} running` : ''} · {r.messages} msgs · {compact(r.tokens.output)} out</span>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <section>
        <h3>Usage <span class="mut">(this directory, during the session)</span> <span class="tag codex">Codex</span></h3>
        {#if d.codex}
          <dl class="kv">
            <div><dt>Runs</dt><dd>{d.codex.runs}</dd></div>
            <div><dt>Input tokens <span class="mut small">(uncached)</span></dt><dd title={d.codex.tokens.input.toLocaleString()}>{compact(d.codex.tokens.input)}</dd></div>
            <div><dt>Output tokens</dt><dd title={d.codex.tokens.output.toLocaleString()}>{compact(d.codex.tokens.output)}</dd></div>
          </dl>
          <ul class="plain">
            {#each Object.entries(d.codex.models) as [m, n]}<li><span>{m}</span><span class="mut">{n} turns</span></li>{/each}
          </ul>
        {:else}
          <p class="mut">No Codex runs.</p>
        {/if}
        <p class="note">Matched by directory and time, so runs by other sessions or by hand in the same directory are included. Reviews run in a scratch directory cannot be attributed and are not counted.</p>
      </section>

      {#if row}
        {@const groups = groupsOf(row)}
        {@const jobs = jobsOf(row)}
        <section>
          <h3>Live now</h3>
          <dl class="kv">
            <div><dt>Sub-sessions</dt><dd>{kidsOf(row).length}</dd></div>
            <div><dt>Codex jobs</dt><dd>{jobs.length}</dd></div>
            <div><dt>Background shells</dt><dd>{row.bgShells ? `~${row.bgShells}` : '–'}</dd></div>
            <div><dt>State</dt><dd>{row.attention ? row.attention.why : row.status}</dd></div>
          </dl>
          {#if groups.length}
            <ul class="plain">
              {#each groups as g}<li><span>{g.label}{g.n > 1 ? ` ×${g.n}` : ''}</span><span class="mut">{g.first.model ?? ''}</span></li>{/each}
            </ul>
          {/if}
        </section>
      {/if}

      {#if d.truncated}<p class="note">Some logs were too large to read and are left out of these totals.</p>{/if}
    {:else}
      <p class="mut">No transcript found for this session{detail?.truncated ? ' (too large to read)' : ''}.</p>
    {/if}
  </aside>
{/if}

<style>
  :global(:root) {
    --bg:#0b0d12; --card:#141820; --card2:#1a1f2a; --line:#242b38; --fg:#e6e9ef; --mut:#8b95a7;
    --ok:#34d399; --busy:#60a5fa; --warn:#fbbf24; --crit:#f87171; color-scheme: dark;
  }
  :global(body) { margin:0; background:radial-gradient(1200px 600px at 20% -10%, #17203a 0%, var(--bg) 60%) fixed; color:var(--fg); font:14px/1.45 system-ui, -apple-system, sans-serif; }
  main { max-width:1200px; margin:0 auto; padding:24px 16px 40px; }
  h1 { font-size:22px; margin:0; letter-spacing:-.01em; }
  main > header { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
  .metrics { color:var(--mut); border:1px solid var(--line); border-radius:999px; padding:5px 14px; text-decoration:none; font-size:13px; }
  .actions { display:flex; gap:8px; align-items:center; }
  .play { background:none; cursor:pointer; font:inherit; font-size:13px; }
  .play.on { color:#86efac; border-color:#2f6b4a; }
  .gears { position:fixed; inset:0; z-index:0; pointer-events:none; overflow:hidden; }
  .bolt { position:absolute; top:0; bottom:0; width:1px; overflow:hidden; }
  .bolt::after { content:''; position:absolute; left:0; right:0; top:0; height:14vh; background:linear-gradient(to bottom, rgba(255,32,200,0), rgba(255,32,200,.95)); box-shadow:0 0 7px 1px rgba(255,32,200,.55); transform:translateY(-100%); animation:rush var(--d) cubic-bezier(.5,0,1,.6) var(--w) infinite; }
  @keyframes rush { 0% { transform:translateY(-100%); opacity:1; } 8% { transform:translateY(calc(100vh)); opacity:1; } 9%,100% { transform:translateY(calc(100vh)); opacity:0; } }
  main { position:relative; z-index:1; }
  .metrics:hover { color:var(--fg); background:var(--card2); border-color:#35405a; }
  .sub { margin:2px 0 0; color:var(--mut); font-size:13px; }
  .banner { background:#3a1618; border:1px solid var(--crit); color:var(--crit); padding:8px 12px; border-radius:10px; }

  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:12px 0 20px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; display:flex; flex-direction:column; }
  .stat .n { font-size:30px; font-weight:650; font-variant-numeric:tabular-nums; line-height:1.1; }
  .stat .l { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.06em; margin-top:4px; }
  .stat.busy .n { color:var(--busy); }
  .stat.alert { border-color:var(--crit); background:#2a1417; }
  .stat.alert .n { color:var(--crit); }

  .hosts { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
  .chip { display:flex; align-items:center; gap:8px; background:var(--card); border:1px solid var(--line); border-radius:999px; padding:5px 14px 5px 10px; }
  .chip.down { border-color:var(--crit); }
  .mut { color:var(--mut); } .crit { color:var(--crit); }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--ok); box-shadow:0 0 8px currentColor; color:var(--ok); }
  .dot.warn { background:var(--warn); color:var(--warn); } .dot.critical { background:var(--crit); color:var(--crit); }

  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }
  .card { background:linear-gradient(180deg,var(--card2),var(--card)); border:1px solid var(--line); border-radius:16px; padding:16px; transition:transform .12s, border-color .12s; }
  .card:hover { transform:translateY(-2px); border-color:#35405a; }
  .card.attn { border-color:var(--warn); box-shadow:0 0 0 1px var(--warn) inset, 0 8px 30px -12px rgba(251,191,36,.35); }
  .top { display:flex; justify-content:space-between; align-items:center; gap:10px; }
  h2 { font-size:16px; margin:0; min-width:0; display:flex; flex-wrap:wrap; align-items:center; gap:4px 0; overflow-wrap:anywhere; }
  h2 .tag { flex:none; white-space:nowrap; }
  .top .badge { flex:none; }
  .path { margin:4px 0 12px; color:var(--mut); font:12px ui-monospace, SFMono-Regular, Menlo, monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge { padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; white-space:nowrap; background:color-mix(in srgb, currentColor 16%, transparent); }
  .badge.idle { color:var(--ok); } .badge.busy { color:var(--busy); } .badge.warn { color:var(--warn); } .badge.crit { color:var(--crit); }
  .badge.busy::before { content:''; display:inline-block; width:6px; height:6px; margin-right:6px; border-radius:50%; background:currentColor; animation:pulse 1.2s infinite; }
  @keyframes pulse { 50% { opacity:.25; } }
  dl { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 16px; margin:0; }
  dl .wide { grid-column:1 / -1; }
  dl .wide dd { white-space:normal; overflow-wrap:anywhere; }
  dl > div { min-width:0; }
  dt { color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
  dd { margin:1px 0 0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-variant-numeric:tabular-nums; }
  .agents { list-style:none; margin:12px 0 0; padding:0; display:flex; flex-wrap:wrap; gap:6px; }
  .agents li { background:#1f2a44; color:var(--busy); border-radius:8px; padding:2px 8px; font-size:12px; }
  .jobs li { background:#2a2440; color:#c4b5fd; }
  .tag { margin-left:8px; padding:1px 7px; border-radius:6px; font-size:11px; font-weight:500; color:var(--mut); border:1px solid var(--line); vertical-align:middle; }
  .agents li span { color:var(--mut); margin-left:6px; }
  .subs { margin-top:12px; padding-top:10px; border-top:1px dashed var(--line); display:grid; gap:6px; }
  .subh { color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
  .sub { display:flex; align-items:center; gap:8px; font-size:13px; }
  .sub.link { cursor:pointer; border-radius:8px; margin:0 -6px; padding:2px 6px; }
  .sub.link:hover { background:#1f2a44; }
  .sub .sn { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .card { cursor:pointer; }
  .card:focus-visible { outline:2px solid var(--busy); outline-offset:2px; }
  .scrim { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:10; }
  .panel { position:fixed; top:0; right:0; bottom:0; width:min(520px,100vw); z-index:11; overflow-y:auto; box-sizing:border-box;
    background:var(--card); border-left:1px solid var(--line); padding:20px 20px 40px; box-shadow:-20px 0 60px rgba(0,0,0,.45); }
  .phead { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }
  .phead h2 { font-size:20px; white-space:normal; }
  .close { background:none; border:1px solid var(--line); color:var(--mut); border-radius:8px; width:32px; height:32px; cursor:pointer; font-size:14px; }
  .term { margin-top:8px; background:none; border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:0 10px; height:32px; cursor:pointer; font-size:12px; white-space:nowrap; }
  .term:disabled { opacity:.4; cursor:not-allowed; }
  .term:hover:not(:disabled) { border-color:#35405a; }
  .close:hover { color:var(--fg); border-color:#35405a; }
  .panel section { margin-top:20px; padding-top:16px; border-top:1px solid var(--line); }
  .panel h3 { display:flex; align-items:center; gap:6px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--mut); margin:0 0 10px; font-weight:600; }
  .kv { display:grid; grid-template-columns:1fr 1fr; gap:12px 16px; margin:0; }
  .kv dd { font-size:15px; }
  .kv .dim dd { color:var(--mut); }
  .pcols { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:8px 28px; }
  .prov { display:flex; gap:8px; align-items:center; margin:10px 0 4px; }
  .panel h3 .tag { margin-left:auto; }
  .tag.claude { color:#f08a24; border-color:#9a4d0c; }
  .tag.codex { color:#86efac; border-color:#2f6b4a; }
  .usage { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:0 0 20px; }
  .uhead { display:flex; align-items:baseline; gap:12px; margin-bottom:10px; }
  .uhead h3 { margin:0; font-size:14px; }
  .uhead .poll { margin-left:auto; }
  .ubars { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px 24px; }
  .brow { display:flex; justify-content:space-between; font-size:13px; margin-bottom:6px; }
  .track { height:8px; background:var(--card2); border-radius:999px; overflow:hidden; }
  .fill { height:100%; background:var(--busy); border-radius:999px; }
  .fill.warn { background:var(--warn); }
  .fill.crit { background:var(--crit); }
  .small { font-size:12px; display:block; margin-top:4px; }
  .poll { background:none; border:1px solid var(--line); color:var(--mut); border-radius:999px; padding:2px 10px; cursor:pointer; font:inherit; font-size:12px; }
  .poll:disabled { opacity:.6; cursor:default; }
  .note { color:var(--mut); font-size:12px; margin:10px 0 0; }
  .plain { list-style:none; margin:10px 0 0; padding:0; display:grid; gap:6px; }
  .plain li { display:flex; justify-content:space-between; gap:12px; font-size:13px; }
  .bars { list-style:none; margin:0; padding:0; display:grid; gap:6px; }
  .bars li { display:grid; grid-template-columns:120px 1fr 40px; align-items:center; gap:10px; font-size:13px; }
  .bn { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .bar { background:var(--bg); border-radius:4px; height:8px; overflow:hidden; }
  .bar i { display:block; height:100%; background:var(--busy); border-radius:4px; }
  .bv { text-align:right; color:var(--mut); font-variant-numeric:tabular-nums; }
  .why { margin:12px 0 0; padding:8px 10px; border-radius:8px; background:#2a2113; color:var(--warn); font-size:12px; }
  .empty { color:var(--mut); }
  .scrim.above { z-index:12; } .panel.above { z-index:13; }
  .toolbar { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px 16px; margin:16px 0 4px; }
  .tabs { display:flex; gap:6px; }
  .tabs button { background:none; border:1px solid var(--line); color:var(--mut); border-radius:999px; padding:5px 16px; cursor:pointer; font:inherit; }
  .tabs button.on { color:var(--fg); background:var(--card2); border-color:#35405a; }
  .hist { margin-top:12px; }
  .hbar { display:flex; flex-wrap:wrap; gap:14px; align-items:center; margin-bottom:12px; color:var(--mut); }
  .hbar input[type=search] { background:var(--card); border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:6px 10px; min-width:240px; font:inherit; }
  .hlist { list-style:none; margin:0; padding:0; display:grid; gap:6px; }
  .hrow { width:100%; text-align:left; display:flex; justify-content:space-between; gap:16px; align-items:center; background:var(--card); border:1px solid var(--line);
    border-radius:10px; padding:10px 14px; color:var(--fg); font:inherit; cursor:pointer; }
  .hrow:hover { border-color:#35405a; }
  .hmain { display:flex; align-items:center; gap:10px; min-width:0; }
  .hmain b { white-space:nowrap; }
  .path-inline { font:12px ui-monospace, SFMono-Regular, Menlo, monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .hmeta { font-size:12px; white-space:nowrap; font-variant-numeric:tabular-nums; }
  .err { color:var(--crit); }
  @media (max-width:640px) { .hrow { flex-direction:column; align-items:flex-start; gap:4px; } }
  @media (max-width:420px) { .grid { grid-template-columns:1fr; } }
</style>
