import os
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
import bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("glazeprintr")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
ENABLE_STREAM = os.environ.get("ENABLE_STREAM", "true").lower() == "true"

scheduler = AsyncIOScheduler()


def _watchdog_stream():
    if not bot.stream_is_alive():
        logger.warning("Stream watchdog: stream dead, restarting...")
        bot.start_stream()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    logger.info(f"glazeprintr starting — DRY_RUN={DRY_RUN}")

    scheduler.add_job(bot.poll_list, "interval", minutes=5, id="list_poller", replace_existing=True)
    scheduler.add_job(bot.poll_mentions, "interval", minutes=5, id="mentions_poller", replace_existing=True)
    scheduler.add_job(bot.post_original_tweet, "interval", hours=2, id="original_tweeter", replace_existing=True)
    if ENABLE_STREAM:
        scheduler.add_job(_watchdog_stream, "interval", minutes=2, id="stream_watchdog", replace_existing=True)
    scheduler.start()
    logger.info("Schedulers started: list poller (5 min), mentions poller (5 min), original tweets (2 hr)")

    if ENABLE_STREAM:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, bot.start_stream)
        logger.info("Filtered stream starting in background thread")

    yield

    scheduler.shutdown(wait=False)
    bot.stop_stream()
    logger.info("glazeprintr shutdown complete")


app = FastAPI(title="glazeprintr", lifespan=lifespan)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>glazeprintr dashboard</title>
<style>
  :root { --bg: #0d0d0d; --card: #1a1a1a; --border: #2a2a2a; --text: #e0e0e0; --muted: #888; --orange: #e8470b; --green: #22c55e; --red: #ef4444; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Courier New', monospace; min-height: 100vh; padding: 2rem; }
  h1 { color: var(--orange); font-size: 1.8rem; letter-spacing: 0.1em; margin-bottom: 0.25rem; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }
  .stat-label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.5rem; }
  .stat-value { font-size: 1.75rem; font-weight: bold; }
  .stat-value.orange { color: var(--orange); }
  .stat-value.green { color: var(--green); }
  .stat-value.red { color: var(--red); }
  .controls { display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }
  button { background: var(--orange); color: #fff; border: none; border-radius: 6px; padding: 0.6rem 1.4rem; font-family: inherit; font-size: 0.9rem; cursor: pointer; transition: opacity 0.2s; }
  button:hover { opacity: 0.85; }
  button.secondary { background: var(--card); border: 1px solid var(--border); }
  .section-title { color: var(--orange); font-size: 1rem; letter-spacing: 0.08em; margin-bottom: 1rem; text-transform: uppercase; }
  .reply-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  .reply-table th { color: var(--muted); text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.06em; text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }
  .reply-table td { padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); vertical-align: top; max-width: 300px; word-break: break-word; }
  .reply-table tr:hover td { background: var(--card); }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .badge-hype { background: #1d4ed8; color: #93c5fd; }
  .badge-dunk { background: #7c3aed; color: #c4b5fd; }
  .badge-educate { background: #065f46; color: #6ee7b7; }
  .badge-chaos { background: #78350f; color: #fcd34d; }
  .badge-dry { background: #3f3f3f; color: #aaa; margin-left: 4px; }
  .ts { color: var(--muted); font-size: 0.72rem; }
  #status-bar { padding: 0.4rem 0.8rem; border-radius: 6px; font-size: 0.8rem; display: inline-block; margin-bottom: 2rem; }
  .status-live { background: #052e16; color: var(--green); border: 1px solid #166534; }
  .status-paused { background: #431407; color: #fb923c; border: 1px solid #9a3412; }
  .status-dry { background: #1e1b4b; color: #a5b4fc; border: 1px solid #3730a3; }
</style>
</head>
<body>
<h1>glazeprintr</h1>
<p class="subtitle">@printrglazr reply bot — Printr ecosystem engagement</p>

<div id="status-bar" class="status-dry">loading...</div>

<div class="stats-grid" id="stats-grid">
  <div class="stat-card"><div class="stat-label">Replies Today</div><div class="stat-value orange" id="replies-today">—</div></div>
  <div class="stat-card"><div class="stat-label">Bot Status</div><div class="stat-value" id="bot-status">—</div></div>
  <div class="stat-card"><div class="stat-label">Stream</div><div class="stat-value" id="stream-status">—</div></div>
  <div class="stat-card"><div class="stat-label">Mode</div><div class="stat-value" id="dry-run-status">—</div></div>
</div>

<div class="controls">
  <button onclick="togglePause()" id="pause-btn">⏸ Pause Bot</button>
  <button class="secondary" onclick="loadStats()">↻ Refresh</button>
</div>

<p class="section-title">Last 20 Replies</p>
<table class="reply-table">
<thead><tr><th>Time</th><th>Account</th><th>Original Tweet</th><th>Reply</th><th>Mode</th></tr></thead>
<tbody id="reply-body"><tr><td colspan="5" style="color:var(--muted);padding:1rem">Loading...</td></tr></tbody>
</table>

<script>
let paused = false;
let dryRun = true;

function badgeMode(mode, dry) {
  let b = `<span class="badge badge-${mode}">${mode}</span>`;
  if (dry) b += `<span class="badge badge-dry">dry</span>`;
  return b;
}

function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }

async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  paused = d.paused;
  dryRun = d.dry_run;

  document.getElementById('replies-today').textContent = d.replies_today;
  document.getElementById('bot-status').textContent = paused ? 'Paused' : 'Running';
  document.getElementById('bot-status').className = 'stat-value ' + (paused ? 'red' : 'green');
  document.getElementById('stream-status').textContent = d.stream_status;
  document.getElementById('stream-status').className = 'stat-value ' + (d.stream_status === 'connected' ? 'green' : 'red');
  document.getElementById('dry-run-status').textContent = dryRun ? 'DRY RUN' : 'LIVE';
  document.getElementById('dry-run-status').className = 'stat-value ' + (dryRun ? '' : 'orange');

  const bar = document.getElementById('status-bar');
  if (paused) { bar.className = 'status-paused'; bar.textContent = '⏸ Bot Paused'; }
  else if (dryRun) { bar.className = 'status-dry'; bar.textContent = '🔵 DRY RUN MODE — replies logged but not posted'; }
  else { bar.className = 'status-live'; bar.textContent = '🟢 LIVE — replies posting to Twitter'; }

  document.getElementById('pause-btn').textContent = paused ? '▶ Resume Bot' : '⏸ Pause Bot';

  const rows = (d.recent_replies || []).map(rep => `
    <tr>
      <td class="ts">${rep.created_at ? rep.created_at.replace('T', ' ').slice(0, 16) : ''}</td>
      <td>@${rep.author_handle || ''}</td>
      <td>${truncate(rep.tweet_text, 120)}</td>
      <td>${truncate(rep.reply_text, 160)}</td>
      <td>${badgeMode(rep.mode, rep.dry_run)}</td>
    </tr>`).join('');
  document.getElementById('reply-body').innerHTML = rows || '<tr><td colspan="5" style="color:var(--muted)">No replies yet</td></tr>';
}

async function togglePause() {
  await fetch('/api/pause', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({paused: !paused}) });
  await loadStats();
}

loadStats();
setInterval(loadStats, 30000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/api/stats")
async def api_stats():
    return {
        "paused": database.is_paused(),
        "dry_run": DRY_RUN,
        "replies_today": database.count_replies_today(),
        "stream_status": database.get_stream_status(),
        "stream_alive": bot.stream_is_alive(),
        "recent_replies": database.get_recent_replies(20),
    }


@app.post("/api/pause")
async def api_pause(body: dict):
    paused = body.get("paused", True)
    database.set_paused(paused)
    status = "paused" if paused else "resumed"
    logger.info(f"Bot {status} via API")
    return {"paused": paused, "status": status}


@app.post("/api/trigger-tweet")
async def api_trigger_tweet():
    logger.info("Manual original tweet trigger via API")
    asyncio.create_task(bot.post_original_tweet())
    return {"status": "triggered", "dry_run": DRY_RUN}


@app.post("/api/restart-stream")
async def api_restart_stream():
    if not ENABLE_STREAM:
        return {"status": "stream disabled"}
    bot.stop_stream()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, bot.start_stream)
    logger.info("Stream restart triggered via API")
    return {"status": "restarting"}


@app.post("/api/reset-counter")
async def api_reset_counter():
    shifted = database.reset_daily_reply_counter()
    logger.info("Daily reply counter reset via API, shifted %d rows", shifted)
    return {"status": "reset", "rows_shifted": shifted, "replies_today": database.count_replies_today()}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "glazeprintr", "dry_run": DRY_RUN}
