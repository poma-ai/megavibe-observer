<script>
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

<main>
  <header>
    <div>
      <h1>Megavibe Observer</h1>
      <p class="sub">{fetchedAt ? 'updated ' + age(fetchedAt) + ' ago' : 'connecting…'}</p>
    </div>
  </header>

  {#if error}<p class="banner">Server unreachable: {error}</p>{/if}

  <section class="stats">
    <div class="stat"><span class="n">{totals.sessions}</span><span class="l">sessions{totals.subs ? ` (+${totals.subs} sub)` : ''}</span></div>
    <div class="stat busy"><span class="n">{totals.busy}</span><span class="l">busy</span></div>
    <div class="stat"><span class="n">{totals.agents}</span><span class="l">agents running</span></div>
    <div class="stat" class:alert={totals.attention}><span class="n">{totals.attention}</span><span class="l">need attention</span></div>
  </section>

  <section class="hosts">
    {#each hosts as h (h.name)}
      <div class="chip" class:down={!h.ok}>
        <span class="dot {h.ok ? h.disk.level : 'critical'}"></span>
        <b>{h.name}</b>
        {#if h.ok}
          <span class="mut">{h.sessions.length} sessions · {h.disk.freeGB} GB free{h.plan?.fiveHour ? ` · 5h ${h.plan.fiveHour.pct}%` : ''}{h.plan?.sevenDay ? ` · 7d ${h.plan.sevenDay.pct}%` : ''}{(h.jobs ?? []).length ? ` · ${h.jobs.length} codex job${h.jobs.length > 1 ? 's' : ''}` : ''}</span>
        {:else}
          <span class="crit">unreachable: {h.error}</span>
        {/if}
      </div>
    {/each}
  </section>

  <section class="grid">
    {#each sorted as s (s.host + s.sessionId)}
      <article class="card" class:attn={attnDeep(s)} role="button" tabindex="0" onclick={() => openDetail(s)}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(s); } }}>
        <div class="top">
          <h2 title={s.name}>{s.name}{#if s.launchedBy}<span class="tag" title="started by the megavibe wrapper">megavibe</span>{/if}</h2>
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
          <div><dt>Where</dt><dd>{s.where}</dd></div>
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
              <div class="sub">
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

</main>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && selected) closeDetail(); }} />

{#if selected}
  {@const row = sessions.find((x) => x.host === selected.host && x.sessionId === selected.sessionId)}
  {@const plan = hosts.find((h) => h.name === selected.host)?.plan}
  <div class="scrim" onclick={closeDetail} role="presentation"></div>
  <aside class="panel" role="dialog" aria-modal="true" aria-label="Session details">
    <header class="phead">
      <div>
        <h2>{row?.name ?? 'Session ended'}</h2>
        <p class="path">{short(row?.cwd)}{row ? ` · ${selected.host}` : ''}</p>
      </div>
      <button class="close" onclick={closeDetail} aria-label="Close details">✕</button>
    </header>

    {#if detailLoading}
      <p class="mut">Reading the transcript…</p>
    {:else if detailErr}
      <p class="err">Could not load details: {detailErr}</p>
    {:else if detail?.found}
      {@const d = detail}
      {@const maxTool = Math.max(1, ...d.session.tools.map((t) => t[1]))}
      <section>
        <h3>Overview</h3>
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
        <h3>Tokens</h3>
        <dl class="kv">
          {#each tokenRows as [k, label]}
            <div class:dim={k === 'cacheRead'}><dt>{label}</dt><dd title={d.session.tokens[k].toLocaleString()}>{compact(d.session.tokens[k])}</dd></div>
          {/each}
        </dl>
        <p class="note">Cache read is the context re-read on every turn, not new work: it dwarfs the rest and is not added up. Counted per message, each reply once.</p>
      </section>

      <section>
        <h3>Models</h3>
        <ul class="plain">
          {#each Object.entries(d.session.models) as [m, n]}<li><span>{m}</span><span class="mut">{n} messages</span></li>{/each}
        </ul>
      </section>

      {#if d.session.tools.length}
        <section>
          <h3>Top tools</h3>
          <ul class="bars">
            {#each d.session.tools as [name, n]}
              <li><span class="bn">{name}</span><span class="bar"><i style:width="{(n / maxTool) * 100}%"></i></span><span class="bv">{n}</span></li>
            {/each}
          </ul>
        </section>
      {/if}

      <section>
        <h3>Agents</h3>
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

      {#if plan}
        <section>
          <h3>Plan usage <span class="mut">(account-wide, {selected.host})</span></h3>
          <dl class="kv">
            {#if plan.fiveHour}<div><dt>5-hour window</dt><dd>{plan.fiveHour.pct}% <span class="mut">· resets {resets(plan.fiveHour.resetsAt)}</span></dd></div>{/if}
            {#if plan.sevenDay}<div><dt>7-day window</dt><dd>{plan.sevenDay.pct}% <span class="mut">· resets {resets(plan.sevenDay.resetsAt)}</span></dd></div>{/if}
          </dl>
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
  .sub { margin:2px 0 0; color:var(--mut); font-size:13px; }
  .banner { background:#3a1618; border:1px solid var(--crit); color:var(--crit); padding:8px 12px; border-radius:10px; }

  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:20px 0 12px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; display:flex; flex-direction:column; }
  .stat .n { font-size:30px; font-weight:650; font-variant-numeric:tabular-nums; line-height:1.1; }
  .stat .l { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.06em; margin-top:4px; }
  .stat.busy .n { color:var(--busy); }
  .stat.alert { border-color:var(--crit); background:#2a1417; }
  .stat.alert .n { color:var(--crit); }

  .hosts { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:20px; }
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
  h2 { font-size:16px; margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .path { margin:4px 0 12px; color:var(--mut); font:12px ui-monospace, SFMono-Regular, Menlo, monospace; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge { padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; white-space:nowrap; background:color-mix(in srgb, currentColor 16%, transparent); }
  .badge.idle { color:var(--ok); } .badge.busy { color:var(--busy); } .badge.warn { color:var(--warn); } .badge.crit { color:var(--crit); }
  .badge.busy::before { content:''; display:inline-block; width:6px; height:6px; margin-right:6px; border-radius:50%; background:currentColor; animation:pulse 1.2s infinite; }
  @keyframes pulse { 50% { opacity:.25; } }
  dl { display:grid; grid-template-columns:1fr 1fr; gap:10px 16px; margin:0; }
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
  .sub .sn { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .card { cursor:pointer; }
  .card:focus-visible { outline:2px solid var(--busy); outline-offset:2px; }
  .scrim { position:fixed; inset:0; background:rgba(0,0,0,.55); z-index:10; }
  .panel { position:fixed; top:0; right:0; bottom:0; width:min(520px,100vw); z-index:11; overflow-y:auto; box-sizing:border-box;
    background:var(--card); border-left:1px solid var(--line); padding:20px 20px 40px; box-shadow:-20px 0 60px rgba(0,0,0,.45); }
  .phead { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }
  .phead h2 { font-size:20px; white-space:normal; }
  .close { background:none; border:1px solid var(--line); color:var(--mut); border-radius:8px; width:32px; height:32px; cursor:pointer; font-size:14px; }
  .close:hover { color:var(--fg); border-color:#35405a; }
  .panel section { margin-top:20px; padding-top:16px; border-top:1px solid var(--line); }
  .panel h3 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--mut); margin:0 0 10px; font-weight:600; }
  .kv { display:grid; grid-template-columns:1fr 1fr; gap:12px 16px; margin:0; }
  .kv dd { font-size:15px; }
  .kv .dim dd { color:var(--mut); }
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
  @media (max-width:420px) { .grid { grid-template-columns:1fr; } }
</style>
