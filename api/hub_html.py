"""
api/hub_html.py - the Solray Business Hub HTML page.

Inline HTML+JS+TailwindCDN. Served by /admin/hub. Calls the JSON
/admin/hub/* endpoints client-side with a JWT from localStorage so the
existing admin auth (require_admin) gates all data without needing a
new cookie-auth path.

Operator flow:
  1. Sign in to app.solray.ai with an admin account
  2. Open this hub page
  3. First visit: prompted for JWT token (paste it from localStorage on
     app.solray.ai). Stored in this page's localStorage afterwards.
  4. Hub fetches /admin/hub/overview etc with Authorization: Bearer <token>.

The HTML is plain text in a Python triple-string. Tailwind via CDN
(pinned version per Codex audit recommendation: avoids CDN surprises).
"""

HUB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Solray Business Hub</title>
<script src="https://cdn.tailwindcss.com/3.4.1"></script>
<style>
  :root {
    --forest:    #16261d;
    --forest-2:  #1b2e23;
    --amber:     #c89a3c;
    --amber-2:   #e0b557;
    --pearl:     #f4ecd8;
    --pearl-2:   #ddd0b0;
    --moss:      #6b8159;
    --ember:     #c46a3a;
    --indigo:    #4c5b8a;
    --pearl-dim: #b2a78a;
  }
  body { background: var(--forest); color: var(--pearl); font-family: Georgia, 'Times New Roman', serif; min-height: 100vh; }
  .card { background: var(--forest-2); border: 1px solid rgba(244,236,216,.08); border-radius: 6px; padding: 18px; }
  .label { color: var(--pearl-dim); font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }
  .num { color: var(--amber-2); font-size: 28px; font-weight: 400; }
  .num-small { color: var(--pearl); font-size: 18px; }
  .ok { color: var(--moss); }
  .warn { color: var(--ember); }
  table { width: 100%; border-collapse: collapse; }
  th { color: var(--pearl-dim); text-transform: uppercase; font-size: 11px; letter-spacing: .04em; text-align: left; padding: 6px 8px; border-bottom: 1px solid rgba(244,236,216,.1); font-weight: 400; }
  td { padding: 8px; border-bottom: 1px solid rgba(244,236,216,.05); font-size: 13px; }
  td.right, th.right { text-align: right; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 12px; font-size: 11px; background: rgba(200,154,60,.15); color: var(--amber-2); }
  .pill.bad { background: rgba(196,106,58,.18); color: var(--ember); }
  .pill.ok { background: rgba(107,129,89,.18); color: var(--moss); }
  .heading { font-size: 14px; color: var(--pearl-dim); letter-spacing: .06em; text-transform: uppercase; margin-bottom: 12px; }
  .hub-title { font-size: 22px; color: var(--amber-2); letter-spacing: .04em; }
  .hub-subtitle { color: var(--pearl-dim); font-size: 12px; letter-spacing: .04em; }
  button.primary { background: var(--amber); color: var(--forest); padding: 6px 14px; border-radius: 4px; font-family: inherit; cursor: pointer; }
  button.ghost { background: transparent; color: var(--pearl-dim); border: 1px solid rgba(244,236,216,.2); padding: 6px 14px; border-radius: 4px; font-family: inherit; cursor: pointer; }
  button:hover { opacity: .9; }
  input.tok { width: 100%; padding: 8px 10px; background: var(--forest); color: var(--pearl); border: 1px solid rgba(244,236,216,.2); border-radius: 4px; font-family: monospace; font-size: 12px; }
  .err { color: var(--ember); }
  .grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
  .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  @media (max-width: 800px) { .grid-3, .grid-2 { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="max-w-6xl mx-auto p-6">

<div class="flex items-center justify-between mb-6">
  <div>
    <div class="hub-title">Solray Business Hub</div>
    <div class="hub-subtitle">Bobby ehf. - operations</div>
  </div>
  <div class="flex gap-2">
    <button class="ghost" onclick="loadAll()">Refresh</button>
    <button class="ghost" onclick="resetToken()">Reset token</button>
  </div>
</div>

<div id="auth-gate" style="display:none" class="card mb-6">
  <div class="heading">Admin token required</div>
  <p class="text-sm mb-3" style="color: var(--pearl-dim)">Paste your JWT from app.solray.ai localStorage (key: <code>token</code> or <code>jwt</code>). Stored in this browser only.</p>
  <input type="password" id="tok-in" class="tok" placeholder="eyJ..." />
  <div class="mt-3 flex gap-2">
    <button class="primary" onclick="saveToken()">Save and load</button>
  </div>
  <div id="auth-err" class="err mt-2 text-sm"></div>
</div>

<div id="hub-content" style="display:none">

  <div class="grid-3 mb-4">
    <div class="card"><div class="label">Paying users</div><div class="num" id="m-paying">-</div><div class="num-small"><span id="m-active">-</span> active or trialing - <span id="m-total">-</span> total</div></div>
    <div class="card"><div class="label">MRR estimate</div><div class="num">$<span id="m-mrr">-</span></div><div class="num-small">at $23/month per paying user</div></div>
    <div class="card"><div class="label">Voice quality (7d)</div><div class="num" id="m-voice">-</div><div class="num-small"><span id="m-voice-n">-</span> audited replies</div></div>
  </div>

  <div class="grid-3 mb-4">
    <div class="card"><div class="label">AI spend (7d)</div><div class="num">$<span id="m-spend">-</span></div><div class="num-small"><span id="m-calls7d">-</span> calls</div></div>
    <div class="card"><div class="label">Chats (24h)</div><div class="num" id="m-chats24h">-</div><div class="num-small">requests through /chat</div></div>
    <div class="card"><div class="label">Errors (24h)</div><div class="num" id="m-err">-</div><div class="num-small"><span id="m-err-rate">-</span>% of <span id="m-err-tot">-</span> calls</div></div>
  </div>

  <div class="grid-2 mb-4">
    <div class="card">
      <div class="heading">Cost by surface (7d)</div>
      <table id="t-surface"><thead><tr><th>Surface</th><th class="right">Calls</th><th class="right">Cost</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="card">
      <div class="heading">Cost by model (7d)</div>
      <table id="t-model"><thead><tr><th>Model</th><th class="right">Calls</th><th class="right">Cost</th></tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="grid-2 mb-4">
    <div class="card">
      <div class="heading">Top users by AI spend (7d)</div>
      <table id="t-users"><thead><tr><th>User</th><th class="right">Calls</th><th class="right">Cost</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="card">
      <div class="heading">Recent drift alerts</div>
      <table id="t-drift"><thead><tr><th>When</th><th>Surface</th><th class="right">Stat</th><th>Status</th></tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="grid-2 mb-4">
    <div class="card">
      <div class="heading">Cron job status</div>
      <table id="t-cron"><thead><tr><th>Job</th><th>Last run</th><th>Status</th><th class="right">Duration</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="card">
      <div class="heading">Usage logger queue</div>
      <table id="t-queue"><tbody></tbody></table>
    </div>
  </div>

  <div class="card mb-4">
    <div class="heading">Memory + storage</div>
    <div class="grid-3">
      <div><div class="label">UserMemory rows</div><div class="num-small" id="m-memrows">-</div></div>
      <div><div class="label">NarrativeEvent rows</div><div class="num-small" id="m-nevrows">-</div></div>
      <div><div class="label">Pricing version</div><div class="num-small">2026-05-14</div></div>
    </div>
  </div>

  <div class="card">
    <div class="heading">Lineage lookup</div>
    <div class="flex gap-2">
      <input type="text" id="lineage-in" class="tok" placeholder="request_uuid from a chat request" />
      <button class="primary" onclick="lookupLineage()">Look up</button>
    </div>
    <pre id="lineage-out" style="white-space: pre-wrap; color: var(--pearl-dim); font-family: monospace; font-size: 11px; margin-top: 12px; max-height: 400px; overflow: auto"></pre>
  </div>

  <div class="text-xs mt-6" style="color: var(--pearl-dim)">
    All data: read-only. Source: <code>/admin/hub/*</code> endpoints, admin-only auth.
    Refresh pulls latest data from production. No personal user content displayed.
  </div>
</div>

</div>

<script>
const API_BASE = '';
let TOK = '';

function getToken() {
  try { TOK = localStorage.getItem('solray_admin_token') || ''; } catch(e) { TOK = ''; }
  return TOK;
}
function saveToken() {
  const t = (document.getElementById('tok-in').value || '').trim();
  if (!t) { document.getElementById('auth-err').textContent = 'Paste a token first'; return; }
  try { localStorage.setItem('solray_admin_token', t); } catch(e) {}
  TOK = t;
  document.getElementById('auth-err').textContent = '';
  showHub();
  loadAll();
}
function resetToken() {
  try { localStorage.removeItem('solray_admin_token'); } catch(e) {}
  TOK = '';
  document.getElementById('hub-content').style.display = 'none';
  document.getElementById('auth-gate').style.display = 'block';
  document.getElementById('tok-in').value = '';
}
function showHub() {
  document.getElementById('auth-gate').style.display = 'none';
  document.getElementById('hub-content').style.display = 'block';
}

async function jget(path) {
  const r = await fetch(API_BASE + path, { headers: { 'Authorization': 'Bearer ' + TOK }});
  if (r.status === 401 || r.status === 403) {
    document.getElementById('auth-gate').style.display = 'block';
    document.getElementById('hub-content').style.display = 'none';
    document.getElementById('auth-err').textContent = 'Token invalid or not an admin. Status: ' + r.status;
    throw new Error('auth');
  }
  if (!r.ok) {
    throw new Error('HTTP ' + r.status);
  }
  return await r.json();
}

function fmtUsd(n) {
  if (n === null || n === undefined) return '-';
  if (n < 0.01 && n > 0) return n.toFixed(4);
  return n.toFixed(2);
}
function fmtPct(n) { if (n == null) return '-'; return (n * 100).toFixed(2); }
function trunc(s, n=24) { if (!s) return '-'; return s.length > n ? s.slice(0,n)+'...' : s; }
function fmtAgo(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    const sec = Math.floor((Date.now() - d.getTime())/1000);
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec/60) + 'm ago';
    if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
    return Math.floor(sec/86400) + 'd ago';
  } catch(e) { return iso; }
}

async function loadAll() {
  try {
    const [overview, cost, drift, cron] = await Promise.all([
      jget('/admin/hub/overview'),
      jget('/admin/hub/cost?days=7'),
      jget('/admin/hub/drift?limit=10'),
      jget('/admin/hub/cron'),
    ]);
    renderOverview(overview);
    renderCost(cost);
    renderDrift(drift);
    renderCron(cron);
  } catch(e) {
    if (e.message !== 'auth') console.error(e);
  }
}

function renderOverview(o) {
  document.getElementById('m-paying').textContent = o.users.paying;
  document.getElementById('m-active').textContent = o.users.active_or_trialing;
  document.getElementById('m-total').textContent = o.users.total;
  document.getElementById('m-mrr').textContent = o.revenue.mrr_usd_estimate.toFixed(0);
  document.getElementById('m-voice').textContent = o.voice_quality_7d.avg_score;
  document.getElementById('m-voice-n').textContent = o.voice_quality_7d.samples;
  document.getElementById('m-spend').textContent = fmtUsd(o.ai_spend_7d.cost_usd);
  document.getElementById('m-calls7d').textContent = o.ai_spend_7d.calls;
  document.getElementById('m-chats24h').textContent = o.chats_24h;
  document.getElementById('m-err').textContent = o.errors_24h.errors;
  document.getElementById('m-err-rate').textContent = fmtPct(o.errors_24h.error_rate);
  document.getElementById('m-err-tot').textContent = o.errors_24h.total;
  document.getElementById('m-memrows').textContent = o.memory.user_memory_rows;
  document.getElementById('m-nevrows').textContent = o.memory.narrative_event_rows;
  // queue
  const q = o.usage_queue || {};
  const tbody = document.querySelector('#t-queue tbody');
  tbody.innerHTML = `
    <tr><td>Enabled</td><td>${q.enabled ? '<span class=pill ok>yes</span>' : '<span class=pill bad>no</span>'}</td></tr>
    <tr><td>Queue depth</td><td>${q.queue_depth}</td></tr>
    <tr><td>Enqueued</td><td>${q.enqueued}</td></tr>
    <tr><td>Written</td><td>${q.written}</td></tr>
    <tr><td>Dropped</td><td>${q.dropped}</td></tr>
    <tr><td>Errors</td><td>${q.errors}</td></tr>
  `;
}

function renderCost(c) {
  const surf = document.querySelector('#t-surface tbody');
  surf.innerHTML = (c.by_surface || []).map(r => `
    <tr><td>${r.surface}</td><td class=right>${r.calls}</td><td class=right>$${fmtUsd(r.cost_usd)}</td></tr>
  `).join('');
  const mdl = document.querySelector('#t-model tbody');
  mdl.innerHTML = (c.by_model || []).map(r => `
    <tr><td>${r.model}</td><td class=right>${r.calls}</td><td class=right>$${fmtUsd(r.cost_usd)}</td></tr>
  `).join('');
  const us = document.querySelector('#t-users tbody');
  us.innerHTML = (c.top_users_by_cost || []).map(r => `
    <tr><td>${trunc(r.user_id, 8)}</td><td class=right>${r.calls}</td><td class=right>$${fmtUsd(r.cost_usd)}</td></tr>
  `).join('');
}

function renderDrift(rows) {
  const t = document.querySelector('#t-drift tbody');
  if (!rows || !rows.length) {
    t.innerHTML = '<tr><td colspan=4 style="color: var(--pearl-dim); padding: 16px">No drift alerts. Voice is steady.</td></tr>';
    return;
  }
  t.innerHTML = rows.map(r => `
    <tr><td>${fmtAgo(r.created_at)}</td><td>${r.surface}</td><td class=right>${r.value.toFixed(1)}</td><td><span class="pill ${r.status === 'active' ? 'bad' : ''}">${r.status}</span></td></tr>
  `).join('');
}

function renderCron(c) {
  const t = document.querySelector('#t-cron tbody');
  if (!c.jobs || !c.jobs.length) {
    t.innerHTML = '<tr><td colspan=4 style="color: var(--pearl-dim); padding: 16px">No cron heartbeats yet. Trigger a cron POST to see the first row.</td></tr>';
    return;
  }
  t.innerHTML = c.jobs.map(j => `
    <tr><td>${j.job_name}</td><td>${fmtAgo(j.last_started_at)}</td><td><span class="pill ${j.last_success ? 'ok' : 'bad'}">${j.last_success ? 'ok' : 'fail'}</span></td><td class=right>${j.duration_ms || '-'} ms</td></tr>
  `).join('');
}

async function lookupLineage() {
  const u = (document.getElementById('lineage-in').value || '').trim();
  const out = document.getElementById('lineage-out');
  if (!u) { out.textContent = ''; return; }
  out.textContent = 'Loading...';
  try {
    const data = await jget('/admin/hub/lineage/' + encodeURIComponent(u));
    out.textContent = JSON.stringify(data, null, 2);
  } catch(e) {
    out.textContent = 'Error: ' + e.message;
  }
}

// Boot
(function init() {
  const t = getToken();
  if (!t) {
    document.getElementById('auth-gate').style.display = 'block';
  } else {
    showHub();
    loadAll();
  }
})();
</script>
</body>
</html>
"""
