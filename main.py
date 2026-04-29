import os
import hmac
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
import bot
import intelligence as intel_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("glazeprintr")

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
ENABLE_LAUNCH_DETECTION = os.environ.get("ENABLE_LAUNCH_DETECTION", "false").lower() == "true"
ENABLE_WHALE_TRACKING = os.environ.get("ENABLE_WHALE_TRACKING", "false").lower() == "true"
ENABLE_COMPETITOR_DATA = os.environ.get("ENABLE_COMPETITOR_DATA", "false").lower() == "true"
ENABLE_TIME_OPTIMIZATION = os.environ.get("ENABLE_TIME_OPTIMIZATION", "false").lower() == "true"
ENABLE_CORRELATION_TWEETS = os.environ.get("ENABLE_CORRELATION_TWEETS", "false").lower() == "true"
ENABLE_STAKING_TWEETS = os.environ.get("ENABLE_STAKING_TWEETS", "false").lower() == "true"
ENABLE_BURN_TRACKING = os.environ.get("ENABLE_BURN_TRACKING", "false").lower() == "true"
ENABLE_REWARDS_DATA = os.environ.get("ENABLE_REWARDS_DATA", "false").lower() == "true"
ENABLE_LAUNCH_GUIDE = os.environ.get("ENABLE_LAUNCH_GUIDE", "false").lower() == "true"
ENABLE_WALLET_PROFILING = os.environ.get("ENABLE_WALLET_PROFILING", "false").lower() == "true"
ENABLE_WALLET_GLAZING = os.environ.get("ENABLE_WALLET_GLAZING", "true").lower() == "true"
ENABLE_AUTO_CLAIM_REWARDS = os.environ.get("ENABLE_AUTO_CLAIM_REWARDS", "false").lower() == "true"

scheduler = AsyncIOScheduler()

_lock_tweet = asyncio.Lock()


async def _run_original_tweet_with_reschedule():
    """Wrapper that posts an original tweet then reschedules itself based on optimal interval."""
    await bot.post_original_tweet()
    next_interval = intel_mod.get_optimal_interval()
    next_run = datetime.now(timezone.utc) + timedelta(minutes=next_interval)
    try:
        scheduler.reschedule_job("original_tweeter", trigger="date", run_date=next_run)
        logger.info(f"Time optimization: next original tweet in {next_interval} min ({next_run.strftime('%H:%M')} UTC)")
    except Exception as e:
        logger.error(f"Failed to reschedule original tweeter: {e}")
_lock_mentions = asyncio.Lock()
_lock_follower_poll = asyncio.Lock()
_lock_qt_glazer = asyncio.Lock()


async def _require_auth(request: Request):
    if not DASHBOARD_TOKEN:
        raise HTTPException(status_code=503, detail="API endpoints disabled — set DASHBOARD_TOKEN")
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    if not token:
        token = request.query_params.get("token", "")
    if not hmac.compare_digest(token, DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    logger.info(f"glazeprintr starting — DRY_RUN={DRY_RUN} (polling-only mode)")

    # max_instances=1: never run two copies of the same poller concurrently.
    # coalesce=True: if a run was delayed/missed, fire once and skip the backlog.
    # misfire_grace_time=60: tolerate up to 60 s of scheduler lag before skipping a run.
    _now = datetime.now(timezone.utc)
    # Reply list poller disabled — user turned it off, QT Glazer handles all engagement now.
    # scheduler.add_job(bot.poll_list, "interval", minutes=5, id="list_poller", replace_existing=True,
    #                   max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    scheduler.add_job(bot.poll_mentions, "interval", minutes=5, id="mentions_poller", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    scheduler.add_job(bot.poll_qt_glazer_list, "interval", minutes=30, id="qt_glazer_poller", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    # Follower scan disabled — Twitter blocks unsolicited replies, wastes API credits.
    # scheduler.add_job(bot.poll_follower_tweets, "interval", minutes=5, id="follower_poller", replace_existing=True,
    #                   max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    # Stagger original-tweet startup by 1 min so it doesn't race with QT Glazer at boot.
    # The threading.Lock in _check_and_claim_post_slot handles any later overlaps.
    _original_tweet_first_run = _now + timedelta(minutes=1)
    if ENABLE_TIME_OPTIMIZATION:
        scheduler.add_job(_run_original_tweet_with_reschedule, "date", run_date=_original_tweet_first_run,
                          id="original_tweeter", replace_existing=True, max_instances=1, misfire_grace_time=60)
    else:
        scheduler.add_job(bot.post_original_tweet, "interval", minutes=60, id="original_tweeter",
                          replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60,
                          next_run_time=_original_tweet_first_run)
    scheduler.add_job(bot.refresh_ecosystem_context, "interval", hours=6, id="ecosystem_refresher", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    scheduler.add_job(bot.refresh_top_tickers, "interval", hours=6, id="ticker_refresher", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    scheduler.add_job(bot.refresh_intelligence_job, "interval", minutes=15, id="intelligence_refresher", replace_existing=True,
                      max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_LAUNCH_DETECTION:
        scheduler.add_job(bot.poll_new_launches, "interval", minutes=10, id="launch_detector", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_WHALE_TRACKING:
        scheduler.add_job(bot.poll_whale_activity, "interval", minutes=15, id="whale_tracker", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_COMPETITOR_DATA:
        from competitor_tracker import refresh_competitor_stats
        scheduler.add_job(refresh_competitor_stats, "interval", hours=2, id="competitor_refresher", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=300, next_run_time=_now)
    if ENABLE_CORRELATION_TWEETS:
        scheduler.add_job(bot.check_correlations, "interval", minutes=15, id="correlation_checker", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_STAKING_TWEETS:
        scheduler.add_job(bot.post_staking_update, "interval", hours=12, id="staking_updater", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=300, next_run_time=_now)
    if ENABLE_BURN_TRACKING:
        scheduler.add_job(bot.poll_burn_events, "interval", minutes=30, id="burn_tracker", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_WALLET_PROFILING:
        from wallet_profiler import refresh_wallet_profiles as _refresh_wallets
        async def _wallet_refresh_job():
            loop = asyncio.get_running_loop()
            projects = bot._latest_projects or []
            await loop.run_in_executor(None, lambda: _refresh_wallets(projects))
        scheduler.add_job(_wallet_refresh_job, "interval", hours=6, id="wallet_profiler", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=300, next_run_time=_now)
    if ENABLE_WALLET_GLAZING:
        scheduler.add_job(bot.scan_wallet, "interval", minutes=15, id="wallet_glazer", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=60, next_run_time=_now)
    if ENABLE_AUTO_CLAIM_REWARDS:
        scheduler.add_job(bot.claim_staking_rewards, "interval", seconds=604800, id="rewards_claimer", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=3600, next_run_time=_now)
    scheduler.start()
    launch_detection_status = "launch detection (10 min)" if ENABLE_LAUNCH_DETECTION else "launch detection DISABLED"
    whale_status = "whale tracking (15 min)" if ENABLE_WHALE_TRACKING else "whale tracking DISABLED"
    competitor_status = "competitor stats (2h)" if ENABLE_COMPETITOR_DATA else "competitor stats DISABLED"
    burn_status = "burn tracking (30 min)" if ENABLE_BURN_TRACKING else "burn tracking DISABLED"
    rewards_status = "rewards data ON" if ENABLE_REWARDS_DATA else "rewards data DISABLED"
    launch_guide_status = "launch guide ON" if ENABLE_LAUNCH_GUIDE else "launch guide DISABLED"
    time_opt_status = "time optimization ON (dynamic interval 45/60/90 min)" if ENABLE_TIME_OPTIMIZATION else "fixed 60 min interval"
    thread_mode_status = "thread mode ON" if bot.ENABLE_THREAD_MODE else "thread mode DISABLED"
    correlation_status = "correlation tweets (15 min)" if ENABLE_CORRELATION_TWEETS else "correlation tweets DISABLED"
    staking_status = "staking tweets (12h)" if ENABLE_STAKING_TWEETS else "staking tweets DISABLED"
    wallet_status = "wallet profiling (6h)" if ENABLE_WALLET_PROFILING else "wallet profiling DISABLED"
    glazing_status = "wallet glazing (15 min)" if ENABLE_WALLET_GLAZING else "wallet glazing DISABLED"
    auto_claim_status = "auto-claim rewards (weekly)" if ENABLE_AUTO_CLAIM_REWARDS else "auto-claim rewards DISABLED"
    logger.info(
        f"Schedulers started: mentions (5 min), QT glazer (30 min), original tweets ({time_opt_status}), "
        f"intelligence refresh (15 min), ecosystem refresh (6h), ticker refresh (6h), "
        f"{launch_detection_status}, {whale_status}, {competitor_status}, {thread_mode_status}, "
        f"{correlation_status}, {staking_status}, "
        f"{burn_status}, {rewards_status}, {launch_guide_status}, {wallet_status}, {glazing_status}, "
        f"{auto_claim_status} — list poller DISABLED, follower scan DISABLED"
    )

    yield

    scheduler.shutdown(wait=False)
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

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
  document.getElementById('stream-status').className = 'stat-value ' + (d.stream_status === 'connected' ? 'green' : d.stream_status === 'disabled' ? '' : 'red');
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
      <td>@${escapeHtml(rep.author_handle || '')}</td>
      <td>${escapeHtml(truncate(rep.tweet_text, 120))}</td>
      <td>${escapeHtml(truncate(rep.reply_text, 160))}</td>
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
async def api_stats(_: None = Depends(_require_auth)):
    return {
        "paused": database.is_paused(),
        "dry_run": DRY_RUN,
        "replies_today": database.count_replies_today(),
        "stream_status": "disabled",
        "recent_replies": database.get_recent_replies(20),
    }


@app.post("/api/pause")
async def api_pause(body: dict, _: None = Depends(_require_auth)):
    paused = body.get("paused", True)
    database.set_paused(paused)
    status = "paused" if paused else "resumed"
    logger.info(f"Bot {status} via API")
    return {"paused": paused, "status": status}


@app.post("/api/trigger-tweet")
async def api_trigger_tweet(_: None = Depends(_require_auth)):
    if _lock_tweet.locked():
        return {"status": "skipped", "reason": "already_in_progress", "dry_run": DRY_RUN}
    async def _run():
        async with _lock_tweet:
            await bot.post_original_tweet()
    asyncio.create_task(_run())
    logger.info("Manual original tweet trigger via API")
    return {"status": "triggered", "dry_run": DRY_RUN}


@app.post("/api/trigger-mentions")
async def api_trigger_mentions(_: None = Depends(_require_auth)):
    if _lock_mentions.locked():
        return {"status": "skipped", "reason": "already_in_progress", "dry_run": DRY_RUN}
    async def _run():
        async with _lock_mentions:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, bot.poll_mentions)
    asyncio.create_task(_run())
    logger.info("Manual mentions poll trigger via API")
    return {"status": "triggered", "dry_run": DRY_RUN}


@app.post("/api/trigger-follower-poll")
async def api_trigger_follower_poll(_: None = Depends(_require_auth)):
    if _lock_follower_poll.locked():
        return {"status": "skipped", "reason": "already_in_progress", "dry_run": DRY_RUN}
    async def _run():
        async with _lock_follower_poll:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, bot.poll_follower_tweets)
    asyncio.create_task(_run())
    logger.info("Manual follower tweet poll trigger via API")
    return {"status": "triggered", "dry_run": DRY_RUN}


@app.post("/api/trigger-qt-glazer")
async def api_trigger_qt_glazer(_: None = Depends(_require_auth)):
    if _lock_qt_glazer.locked():
        return {"status": "skipped", "reason": "already_in_progress", "dry_run": DRY_RUN}
    async def _run():
        async with _lock_qt_glazer:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, bot.poll_qt_glazer_list)
    asyncio.create_task(_run())
    logger.info("Manual QT Glazer poll trigger via API")
    return {"status": "triggered", "dry_run": DRY_RUN}


@app.post("/api/reset-counter")
async def api_reset_counter(_: None = Depends(_require_auth)):
    shifted = database.reset_daily_reply_counter()
    logger.info("Daily reply counter reset via API, shifted %d rows", shifted)
    return {"status": "reset", "rows_shifted": shifted, "replies_today": database.count_replies_today()}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "glazeprintr", "dry_run": DRY_RUN}


@app.get("/api/status")
async def api_status(_: None = Depends(_require_auth)):
    return {"status": "ok"}
