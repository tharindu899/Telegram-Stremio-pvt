
let pollTimer = null;
let lastAnalyticsLoad = 0;
const IDLE_REFRESH_MS = 30000;
const ACTIVE_REFRESH_MS = 30000;
const SPEED_REFRESH_MS = 30000;
let lastSpeedAt = 0;

function escapeHtml(v){ return String(v ?? '').replace(/[&<>\"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;','"':'&quot;'}[m] || m)); }
function niceKey(key){ return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }
function formatBool(v){ return v ? 'Yes' : 'No'; }
function formatValue(v){
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return formatBool(v);
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
  if (Array.isArray(v)) return v.length ? `${v.length} items` : '0';
  if (typeof v === 'object') return 'Details';
  return String(v);
}
function formatBytesShort(bytes){
  bytes = Number(bytes || 0);
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(bytes >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}
async function api(url, opts={}){
  const res = await fetch(url, {headers:{'Content-Type':'application/json'}, ...opts});
  const text = await res.text();
  let data; try { data = JSON.parse(text); } catch { data = {message:text}; }
  if(!res.ok) throw new Error(data.detail || data.message || text || res.statusText);
  return data;
}
function pill(status){
  const st = String(status || 'idle').toLowerCase();
  const icon = st === 'running' ? 'fa-spinner fa-spin' : (st === 'complete' || st === 'success' ? 'fa-check' : (st === 'error' || st === 'failed' ? 'fa-triangle-exclamation' : 'fa-circle'));
  return `<span class="status-pill ${escapeHtml(st)}"><i class="fa-solid ${icon}"></i>${escapeHtml(st.toUpperCase())}</span>`;
}
function statusText(job){ if(!job) return '—'; return job.running ? 'RUNNING' : (job.status || 'idle').toUpperCase(); }
function anyJobRunning(jobs){ return !!(jobs?.scan?.running || jobs?.deadcheck?.running || jobs?.dedupe?.running); }
function updateStatusCard(id, job){
  const el = document.getElementById(id);
  if (!el) return;
  const status = job?.running ? 'RUNNING' : (job?.status || 'idle').toUpperCase();
  el.innerHTML = `${status}`;
  el.className = 'metric-value';
  if (status === 'RUNNING') el.style.color = 'var(--primary)';
  else if (status === 'COMPLETE' || status === 'SUCCESS') el.style.color = '#86efac';
  else if (status === 'ERROR' || status === 'FAILED') el.style.color = '#fca5a5';
  else el.style.color = 'var(--primary)';
}
function renderResultPanel(target, obj, fallbackTitle='Result'){
  const el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return;
  if (typeof obj === 'string') {
    el.innerHTML = `<div class="pretty-message">${escapeHtml(obj)}</div>`;
    return;
  }
  const status = obj?.status || obj?.mode || 'success';
  const msg = obj?.message || obj?.detail || fallbackTitle;
  const cls = String(status).includes('error') || String(status).includes('failed') ? 'bad' : (String(status).includes('started') ? 'warn' : 'good');
  const ignored = new Set(['status','message','detail','raw']);
  const cards = [];
  for (const [k,v] of Object.entries(obj || {})) {
    if (ignored.has(k)) continue;
    if (typeof v === 'object' && v !== null) {
      if (Array.isArray(v)) cards.push([niceKey(k), `${v.length} items`]);
      else {
        const keys = Object.keys(v).slice(0,4);
        const val = keys.length ? keys.map(x => `${niceKey(x)}: ${formatValue(v[x])}`).join(' · ') : '—';
        cards.push([niceKey(k), val]);
      }
    } else cards.push([niceKey(k), formatValue(v)]);
  }
  el.innerHTML = `
    <div class="pretty-message ${cls}">${pill(status)}<div class="mt-2">${escapeHtml(msg)}</div></div>
    ${cards.length ? `<div class="detail-grid three">${cards.slice(0,9).map(([a,b]) => `<div class="detail-item"><span class="detail-label">${escapeHtml(a)}</span><span class="detail-value">${escapeHtml(b)}</span></div>`).join('')}</div>` : ''}
  `;
}
function setResult(obj){ renderResultPanel('toolResult', obj, 'Done'); }
function renderScanDetails(sp, job){
  const el = document.getElementById('scanDetails');
  const running = !!sp.running || !!job?.running;
  const processed = Number(sp.processed || 0), found = Number(sp.messages_found || 0);
  const indexed = Number(sp.indexed || 0), errs = Number(sp.errors || 0);
  const skipped = Number(sp.skipped_duplicate || 0) + Number(sp.skipped_metadata || 0) + Number(sp.skipped_non_video || 0);
  const status = running ? 'running' : (job?.status || 'idle');
  const msg = job?.message || (running ? 'Scanning…' : 'No scan running.');
  const result = job?.result || null;
  let summaryCards = [
    ['Status', pill(status)],
    ['Channel', escapeHtml(sp.channel_name || sp.channel_id || result?.channel || '—')],
    ['Messages', `${processed}/${found}`],
    ['Indexed', indexed],
    ['Skipped', skipped],
    ['Errors', errs],
  ];
  let resultHtml = '';
  if (result && !running) {
    resultHtml = `<div class="pretty-message good">Scan result: ${escapeHtml(result.cancelled ? 'Cancelled' : 'Complete')} · Indexed ${escapeHtml(result.indexed ?? 0)} · Processed ${escapeHtml(result.processed ?? 0)} · Errors ${escapeHtml(result.errors ?? 0)}</div>`;
  } else if (job?.error) {
    resultHtml = `<div class="pretty-message bad">${escapeHtml(job.error)}</div>`;
  }
  el.innerHTML = `
    <div class="pretty-message ${running ? 'warn' : (job?.status === 'error' ? 'bad' : '')}">${escapeHtml(msg)}</div>
    <div class="detail-grid three">${summaryCards.map(([a,b]) => `<div class="detail-item"><span class="detail-label">${escapeHtml(a)}</span><span class="detail-value">${String(b).startsWith('<span') ? b : escapeHtml(b)}</span></div>`).join('')}</div>
    ${resultHtml}
  `;
}
function setAutoBadge(running=false){
  const el = document.getElementById('autoRefreshBadge');
  if (!el) return;
  el.innerHTML = running ? '<span class="pulse-dot"></span> Auto refresh · 30s' : '<span class="pulse-dot"></span> Auto refresh · 30s';
}
async function loadStatus(){
  const data = await api('/api/admin/tools/status');
  const jobs = data.jobs || {};
  updateStatusCard('scanStatus', jobs.scan);
  updateStatusCard('deadStatus', jobs.deadcheck);
  updateStatusCard('dedupeStatus', jobs.dedupe);
  const sp = data.scan_progress || {};
  const processed = Number(sp.processed || 0), found = Number(sp.messages_found || 0);
  const pct = found > 0 ? Math.min(100, Math.round((processed / found) * 100)) : (sp.running ? 7 : 0);
  document.getElementById('scanProgressBar').style.width = pct + '%';
  document.getElementById('scanProgressText').textContent = sp.running ? `${pct}% · ${processed}/${found}` : (jobs.scan?.message || 'Idle');
  renderScanDetails(sp, jobs.scan);
  setAutoBadge(anyJobRunning(jobs));
  return data;
}
async function manualRefresh(){
  try { await refreshAll(true); setResult({status:'success', message:'Admin tools refreshed.'}); }
  catch(e){ setResult({status:'error', message:e.message || String(e)}); }
}
async function refreshAll(forceAnalytics=false){
  const data = await loadStatus();
  const now = Date.now();
  if (now - lastSpeedAt > SPEED_REFRESH_MS || forceAnalytics) { lastSpeedAt = now; await loadSpeed().catch(()=>{}); }
  if (forceAnalytics || now - lastAnalyticsLoad > 120000) { await loadFeaturePack().catch(()=>{}); }
  schedulePolling(anyJobRunning(data.jobs || {}));
  return data;
}
function stopPolling(){ if(pollTimer){ clearTimeout(pollTimer); pollTimer = null; } }
function schedulePolling(active=false){
  stopPolling();
  if (document.visibilityState !== 'visible') return;
  const delay = active ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS;
  pollTimer = setTimeout(async () => {
    try { await refreshAll(false); }
    catch(e){ setResult({status:'error', message:e.message || String(e)}); schedulePolling(false); }
  }, delay);
}
async function startScan(mode){
  const target = document.getElementById('scanTarget').value.trim();
  const yes = mode === 'rescan' ? confirm('Rescan will purge old DB entries for selected AUTH_CHANNEL/group and rebuild. Continue?') : true;
  if(!yes) return;
  const payload = {mode}; if(target) payload.target = target;
  setResult(await api('/api/admin/tools/scan', {method:'POST', body:JSON.stringify(payload)}));
  await refreshAll(true); schedulePolling(true);
}
async function cancelScan(){ setResult(await api('/api/admin/tools/scan/cancel', {method:'POST'})); await refreshAll(true); }
async function startDeadcheck(){ setResult(await api('/api/admin/tools/deadcheck', {method:'POST'})); await refreshAll(true); schedulePolling(true); }
async function dedupe(confirmRun){
  if(confirmRun && !confirm('Delete exact duplicate DB entries and queue old Telegram deletes?')) return;
  setResult(await api('/api/admin/tools/dedupe', {method:'POST', body:JSON.stringify({confirm: confirmRun})}));
  await refreshAll(true); schedulePolling(true);
}
async function clearCacheAll(){ if(!confirm('Clear stream cache from /tmp? Current playback may reload ranges.')) return; setResult(await api('/api/admin/tools/clear-cache', {method:'POST'})); }
async function syncCatalogs(full){ setResult(await api(`/api/custom-catalogs/auto-sync?full_rebuild=${full ? 'true':'false'}`, {method:'POST'})); await loadFeaturePack(); }
async function resetTokenUsage(token){ if(!confirm('Reset this token traffic counters?')) return; setResult(await api(`/api/admin/tokens/${encodeURIComponent(token)}/reset-usage`, {method:'POST'})); await loadFeaturePack(); }
function renderStorageChart(databases){
  const el = document.getElementById('storageChart');
  const rows = Array.isArray(databases) ? databases : [];
  if (!rows.length) { el.innerHTML = '<div class="mini-row">No storage database stats yet.</div>'; return; }
  const max = Math.max(...rows.map(r => Number(r.file_bytes_est || r.data_size || 0)), 1);
  el.innerHTML = rows.map(r => {
    const fileBytes = Number(r.file_bytes_est || 0);
    const dbBytes = Number(r.data_size || 0) + Number(r.index_size || 0);
    const pct = Math.max(2, Math.min(100, Math.round((Math.max(fileBytes, dbBytes) / max) * 100)));
    return `<div class="storage-bar-row">
      <div class="storage-bar-head">
        <div><div class="storage-bar-title">${escapeHtml(r.db || 'storage')}</div><div class="text-xs" style="color:var(--text-sec)">${r.movies || 0} movies · ${r.series || 0} series · ${r.streams || 0} streams · ${r.subtitles || 0} subs</div></div>
        <div class="storage-bar-meta">Library ${escapeHtml(r.file_size_est || formatBytesShort(fileBytes))}<br>DB ${escapeHtml(r.data_readable || formatBytesShort(dbBytes))}</div>
      </div><div class="storage-bar-wrap"><div class="storage-bar" style="width:${pct}%"></div></div>
    </div>`;
  }).join('');
}
async function loadFeaturePack(){
  lastAnalyticsLoad = Date.now();
  const data = await api('/api/admin/features/dashboard');
  const storage = data.storage?.summary || {};
  const usage = data.token_usage?.summary || {};
  renderStorageChart(data.storage?.databases || []);
  const topItems = data.top_watched?.items || [];
  const topics = data.topics?.topics || [];
  const tokens = data.token_usage?.tokens || [];
  document.getElementById('analyticsGrid').innerHTML = [
    ['Storage DBs', storage.storage_dbs ?? 0, storage.data_readable || '0 B'],
    ['Library Size', storage.file_size_est || '0 B', 'estimated from streams'],
    ['Monthly Traffic', usage.monthly_readable || '0 B', `${usage.tokens||0} tokens`],
  ].map(x => `<div class="mini-row"><p class="metric-label">${escapeHtml(x[0])}</p><b class="text-xl">${escapeHtml(x[1])}</b><p class="text-xs" style="color:var(--text-sec)">${escapeHtml(x[2])}</p></div>`).join('');
  document.getElementById('topWatched').innerHTML = topItems.length ? topItems.map((x,i)=>`<div class="mini-row"><b>${i+1}. ${escapeHtml(x.title)}</b><p style="color:var(--text-sec)">${escapeHtml(x.plays)} plays · ${escapeHtml(x.readable)} · ${escapeHtml(x.avg_mbps)} MB/s</p></div>`).join('') : '<div class="mini-row">No stream analytics yet. Play something first.</div>';
  document.getElementById('topicStats').innerHTML = topics.length ? topics.slice(0,15).map(x=>`<div class="mini-row"><b>${escapeHtml(x.topic_name || x.name || x.chat_id || 'Unknown source')}</b><p style="color:var(--text-sec)">${x.chat_id && x.chat_id !== 'unknown' ? 'Chat ' + escapeHtml(x.chat_id) + ' · ' : ''}${escapeHtml(x.streams)} streams · ${escapeHtml(x.subtitles)} subs</p></div>`).join('') : '<div class="mini-row">No source/topic data yet. New scans will add it.</div>';
  document.getElementById('tokenUsage').innerHTML = tokens.length ? tokens.slice(0,20).map(t=>`<div class="mini-row"><b>${escapeHtml(t.name)}</b><p style="color:var(--text-sec)">Today ${escapeHtml(t.daily_readable)} · Month ${escapeHtml(t.monthly_readable)}</p><button class="btn-action small muted mt-2" onclick="resetTokenUsage('${escapeHtml(t.token)}')"><i class="fa-solid fa-rotate-left"></i> Reset quota</button></div>`).join('') : '<div class="mini-row">No tokens found.</div>';
}
async function loadSpeed(){
  const data = await api('/api/admin/tools/speed');
  const bots = data?.data?.bot_workloads || [];
  document.getElementById('botGrid').innerHTML = bots.map(b => `
    <div class="bot-card">
      <div class="flex items-center justify-between gap-2"><h3 class="font-black text-lg">${escapeHtml(b.display_name || ('Bot ' + ((b.client_index||0)+1)))}</h3><span class="bot-health ${(b.status||'').toLowerCase()}">${escapeHtml(b.status || 'healthy')}</span></div>
      <div class="grid grid-cols-3 gap-2 mt-4 text-center">
        <div><p class="metric-label">Active</p><b class="text-2xl">${escapeHtml(b.active_streams ?? 0)}</b></div>
        <div><p class="metric-label">Recent</p><b class="text-2xl">${escapeHtml(b.recent_streams ?? 0)}</b></div>
        <div><p class="metric-label">MB/s</p><b class="text-2xl">${escapeHtml(b.avg_mbps ?? 0)}</b></div>
      </div>
      ${b.last_active ? `<p class="result-mini mt-3">Last active: ${escapeHtml(b.last_active)}</p>` : ''}
    </div>`).join('') || '<p style="color:var(--text-sec)">No bot clients loaded yet.</p>';
}
window.addEventListener('visibilitychange', () => { if (document.visibilityState !== 'visible') stopPolling(); else refreshAll(false).catch(e => setResult({status:'error', message:e.message || String(e)})); });
window.addEventListener('load', () => { refreshAll(true).catch(e => setResult({status:'error', message:e.message || String(e)})); });
