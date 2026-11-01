"""
HK Stock Panel - FastAPI Backend
Multi-stock CCASS monitoring, IR onboarding, and trading analytics.
No built-in stock data — everything is fetched on-demand.
"""
import os
import sys
import json
import logging
import uuid
import threading
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))
from db import (
    ensure_stock, get_stock_conn, get_meta_conn, list_stocks,
    delete_stock, update_stock_last_updated, get_stock_upload_dir,
    init_meta_db, ensure_dirs, get_stock_db_path
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="HK Stock Panel API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')


@app.on_event("startup")
def startup():
    ensure_dirs()
    init_meta_db()
    # Start daily auto-fetch scheduler
    try:
        from services import scheduler
        scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler start failed: {e}")
    logger.info("HK Stock Panel API started")


# ============ Stock Management ============

@app.get("/api/stocks")
def get_stocks():
    """List all monitored stocks."""
    stocks = list_stocks()
    return {'stocks': stocks}


# In-memory job tracking
_fetch_jobs: dict[str, dict] = {}


def _broadcast_ccass_progress(state: dict, pct: int, date) -> None:
    """Write one crawl's progress onto every job attached to it (leader and followers)."""
    from services.ccass_range import record_ccass_progress
    for jid in record_ccass_progress(state, pct, date):
        job = _fetch_jobs.get(jid)
        if not job or job.get('status') in ('done', 'error'):
            continue
        job['progress'] = pct
        job['current_date'] = date
        job['target_days'] = state.get('target_days')


def _seed_follower_progress(job_id: str, state: dict) -> None:
    """Copy the crawl's latest percent onto a job that joined after the last date finished."""
    from services.ccass_range import shared_ccass_progress
    pct, date = shared_ccass_progress(state)
    job = _fetch_jobs.get(job_id)
    if job is None or pct is None or job.get('status') in ('done', 'error'):
        return
    job['progress'] = pct
    if date:
        job['current_date'] = date
    job['target_days'] = state.get('target_days')


def _run_fetch_job(job_id: str, code: str, days: int):
    """Background task to fetch data for a stock."""
    _fetch_jobs[job_id] = {'status': 'running', 'progress': 0, 'stage': 'starting', 'code': code}
    try:
        from scrapers.trading_scraper import backfill_trading
        _fetch_jobs[job_id].update({'stage': 'trading'})
        trading_days = backfill_trading(code)
        _fetch_jobs[job_id].update({'trading_days': trading_days})

        from services.ccass_range import claim_ccass_backfill, run_ccass_backfill_leader
        _fetch_jobs[job_id].update({'stage': 'ccass'})

        # Share the per-stock crawl with on-demand 180/365 fills so two jobs
        # never hit HKEX for the same code at the same time.
        leader, state = claim_ccass_backfill(code, days, job_id)
        _fetch_jobs[job_id]['target_days'] = state['target_days']

        def ccass_progress(done, total, date):
            if total > 0:
                pct = min(95, 10 + int(done / total * 85))
                _broadcast_ccass_progress(state, pct, date)

        if leader:
            run_ccass_backfill_leader(code, state, progress_callback=ccass_progress)
        else:
            _seed_follower_progress(job_id, state)
            state['done'].wait()
        if state.get('status') == 'error':
            raise RuntimeError(state.get('error') or 'CCASS backfill failed')
        ccass_days = state.get('fetched') or 0
        _fetch_jobs[job_id].update({'ccass_days': ccass_days})

        from services.selloff_engine import compute_selloff_range
        _fetch_jobs[job_id].update({'stage': 'selloff'})
        try:
            compute_selloff_range(code, days=20)
        except Exception as e:
            logger.error(f"Selloff compute failed: {e}")

        update_stock_last_updated(code)
        _fetch_jobs[job_id].update({
            'status': 'done', 'progress': 100, 'stage': 'complete',
            'trading_days': trading_days, 'ccass_days': ccass_days,
        })
    except Exception as e:
        logger.error(f"Fetch job {job_id} failed: {e}")
        _fetch_jobs[job_id].update({'status': 'error', 'error': str(e)})


@app.post("/api/stocks/{stock_code}")
def add_stock(stock_code: str, background_tasks: BackgroundTasks):
    """Add a stock to monitor and trigger background data fetch."""
    code = stock_code.zfill(5)
    if not code.isdigit() or len(code) != 5:
        raise HTTPException(400, "Invalid stock code. Use 5-digit HK code (e.g. 00700)")

    # Fetch stock info first to validate
    from scrapers.trading_scraper import fetch_stock_info, save_stock_info
    info = fetch_stock_info(code)
    stock_name = info.get('stock_name', '')

    if not stock_name:
        raise HTTPException(404, f"Stock code {code} not found. Please check the code.")

    ensure_stock(code, stock_name)
    if info:
        save_stock_info(code, info)

    with get_meta_conn() as conn:
        conn.execute("UPDATE stocks SET stock_name = ? WHERE stock_code = ?", (stock_name, code))

    # Quick fetch of trading data (fast) synchronously so dashboard has something to show
    try:
        from scrapers.trading_scraper import backfill_trading
        backfill_trading(code)
    except Exception as e:
        logger.error(f"Quick trading fetch failed: {e}")

    # Launch CCASS fetch in background
    job_id = uuid.uuid4().hex[:12]
    _fetch_jobs[job_id] = {'status': 'running', 'progress': 10, 'stage': 'queued', 'code': code}
    thread = threading.Thread(target=_run_fetch_job, args=(job_id, code, 90), daemon=True)
    thread.start()

    return {'status': 'ok', 'stock_code': code, 'stock_name': stock_name, 'job_id': job_id}


@app.delete("/api/stocks/{stock_code}")
def remove_stock(stock_code: str):
    """Delete all data for a stock."""
    code = stock_code.zfill(5)
    delete_stock(code)
    return {'status': 'deleted', 'stock_code': code}


@app.post("/api/stocks/{stock_code}/fetch")
def fetch_stock_data(stock_code: str, days: int = Query(90, ge=7, le=365)):
    """Trigger background data fetch for a stock (CCASS + trading). Returns immediately with job_id."""
    code = stock_code.zfill(5)
    ensure_stock(code)

    job_id = uuid.uuid4().hex[:12]
    _fetch_jobs[job_id] = {'status': 'running', 'progress': 0, 'stage': 'queued', 'code': code}
    thread = threading.Thread(target=_run_fetch_job, args=(job_id, code, days), daemon=True)
    thread.start()
    return {'status': 'started', 'job_id': job_id}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Poll fetch job status."""
    if job_id not in _fetch_jobs:
        raise HTTPException(404, "Job not found")
    return _fetch_jobs[job_id]


# ============ Stock Info ============

@app.get("/api/stock/{stock_code}/info")
def get_stock_info(stock_code: str):
    """Get basic stock info."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute("SELECT key, value FROM stock_info").fetchall()
        info = {r['key']: json.loads(r['value']) if r['value'] else None for r in rows}

        # Get latest trading data
        latest = conn.execute("SELECT * FROM daily_trading ORDER BY trade_date DESC LIMIT 1").fetchone()
        if latest:
            info['latest_trading'] = dict(latest)

        # Get latest CCASS total
        ct = conn.execute("SELECT * FROM ccass_total ORDER BY trade_date DESC LIMIT 1").fetchone()
        if ct:
            info['latest_ccass'] = dict(ct)

        # Get latest HK Connect
        hk = conn.execute("SELECT * FROM hk_connect ORDER BY trade_date DESC LIMIT 1").fetchone()
        if hk:
            info['latest_hk_connect'] = dict(hk)

        # Attribution coverage
        attr_count = conn.execute("SELECT COUNT(*) as c FROM shareholder_attribution").fetchone()
        known_participants = conn.execute("""
            SELECT COUNT(DISTINCT sa.participant_id) as c
            FROM shareholder_attribution sa
        """).fetchone()
        total_latest = conn.execute("SELECT COUNT(*) as c FROM ccass_participants WHERE trade_date = (SELECT MAX(trade_date) FROM ccass_participants)").fetchone()

        info['ir_configured'] = (attr_count['c'] > 0) if attr_count else False
        info['known_participant_count'] = known_participants['c'] if known_participants else 0
        info['total_participant_count'] = total_latest['c'] if total_latest else 0

        return info


# ============ Announcements ============


def _run_announcement_fetch_job(job_id: str, code: str, days: int):
    """Background task to fetch HKEX announcements for a stock."""
    _fetch_jobs[job_id] = {'status': 'running', 'progress': 10, 'stage': 'announcements', 'code': code}
    try:
        from scrapers.announcement_scraper import backfill_announcements
        count = backfill_announcements(code, days=days)
        update_stock_last_updated(code)
        _fetch_jobs[job_id].update({
            'status': 'done', 'progress': 100, 'stage': 'complete', 'announcements': count,
        })
    except Exception as e:
        logger.error(f"Announcement fetch job {job_id} failed: {e}")
        _fetch_jobs[job_id].update({'status': 'error', 'error': str(e)})


@app.get("/api/stock/{stock_code}/announcements")
def get_announcements(stock_code: str, days: int = Query(90, ge=1, le=730)):
    """Get recent HKEX announcements for a stock from local DB."""
    code = stock_code.zfill(5)
    ensure_stock(code)
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    with get_stock_conn(code) as conn:
        rows = conn.execute("""
            SELECT * FROM announcements
            WHERE announce_date >= ?
            ORDER BY announce_date DESC, id DESC
        """, (since,)).fetchall()
        return {'announcements': [dict(r) for r in rows]}


@app.post("/api/stock/{stock_code}/announcements/fetch")
def fetch_stock_announcements(stock_code: str, days: int = Query(90, ge=1, le=365)):
    """Trigger background HKEX announcement fetch for a stock."""
    code = stock_code.zfill(5)
    ensure_stock(code)

    job_id = uuid.uuid4().hex[:12]
    _fetch_jobs[job_id] = {'status': 'running', 'progress': 0, 'stage': 'queued', 'code': code}
    thread = threading.Thread(target=_run_announcement_fetch_job, args=(job_id, code, days), daemon=True)
    thread.start()
    return {'status': 'started', 'job_id': job_id}


# ============ Trading Data ============

@app.get("/api/stock/{stock_code}/trading")
def get_trading(stock_code: str, days: int = Query(90, ge=1, le=365)):
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute("""
            SELECT * FROM daily_trading ORDER BY trade_date DESC LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in reversed(rows)]


# ============ CCASS Data ============

@app.get("/api/stock/{stock_code}/ccass/total")
def get_ccass_total(stock_code: str, days: int = Query(60, ge=1, le=365)):
    """CCASS total + southbound series. `days` is a trading-session count, max 365."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute("""
            SELECT ct.*, hk.shares as hk_connect_shares, hk.pct_of_issued as hk_connect_pct, hk.shares_change as hk_connect_change
            FROM ccass_total ct
            LEFT JOIN hk_connect hk ON ct.trade_date = hk.trade_date
            ORDER BY ct.trade_date DESC LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in reversed(rows)]


@app.get("/api/stock/{stock_code}/ccass/participants")
def get_participants(stock_code: str, date: str = None, limit: int = Query(50, ge=1, le=500)):
    """Get CCASS participants for a specific date (or latest)."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        if date:
            target_date = date
        else:
            latest = conn.execute("SELECT MAX(trade_date) as d FROM ccass_participants").fetchone()
            if not latest:
                return []
            target_date = latest['d']

        rows = conn.execute("""
            SELECT cp.*, cdc.shares_change, cdc.shares_change_3d, cdc.shares_change_5d,
                   sa.shareholder_name
            FROM ccass_participants cp
            LEFT JOIN ccass_daily_changes cdc ON cp.participant_id = cdc.participant_id AND cp.trade_date = cdc.trade_date
            LEFT JOIN shareholder_attribution sa ON cp.participant_id = sa.participant_id
            WHERE cp.trade_date = ?
            ORDER BY cp.shares DESC LIMIT ?
        """, (target_date, limit)).fetchall()
        return {'date': target_date, 'participants': [dict(r) for r in rows]}


@app.get("/api/stock/{stock_code}/ccass/changes")
def get_ccass_changes(stock_code: str, date: str = None, limit: int = Query(50, ge=1, le=200)):
    """Get CCASS daily changes (sorted by absolute change)."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        if date:
            target_date = date
        else:
            latest = conn.execute("SELECT MAX(trade_date) as d FROM ccass_daily_changes").fetchone()
            if not latest:
                return []
            target_date = latest['d']

        rows = conn.execute("""
            SELECT cdc.*, sa.shareholder_name
            FROM ccass_daily_changes cdc
            LEFT JOIN shareholder_attribution sa ON cdc.participant_id = sa.participant_id
            WHERE cdc.trade_date = ?
            ORDER BY ABS(cdc.shares_change) DESC LIMIT ?
        """, (target_date, limit)).fetchall()
        return {'date': target_date, 'changes': [dict(r) for r in rows]}


@app.get("/api/stock/{stock_code}/ccass/top10")
def get_top10(stock_code: str):
    """Get Top 10 participants over time."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        dates = [r['trade_date'] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM ccass_top10 ORDER BY trade_date DESC LIMIT 30"
        ).fetchall()]
        result = {}
        for d in reversed(dates):
            rows = conn.execute(
                "SELECT rank, participant_id, participant_name, shares, pct_of_issued FROM ccass_top10 WHERE trade_date = ? ORDER BY rank",
                (d,)
            ).fetchall()
            result[d] = [dict(r) for r in rows]
        return result


@app.get("/api/stock/{stock_code}/ccass/dates")
def get_available_dates(stock_code: str, days: int = Query(60, ge=1, le=365)):
    """Get dates with CCASS data, newest first, capped at the requested window."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM ccass_participants ORDER BY trade_date DESC LIMIT ?",
            (days,),
        ).fetchall()
        return {'dates': [r['trade_date'] for r in rows]}


def _ccass_trade_dates(code: str) -> list[str]:
    with get_stock_conn(code) as conn:
        rows = conn.execute("SELECT DISTINCT trade_date FROM ccass_participants").fetchall()
        return [r['trade_date'] for r in rows]


def _start_ccass_ensure_job(code: str, state: dict) -> None:
    """Background leader for an on-demand history fill. Uses the shared crawl lock."""
    job_id = state['job_id']

    def ccass_progress(done, total, date):
        if total > 0:
            from services.ccass_range import ccass_ensure_progress_pct
            _broadcast_ccass_progress(state, ccass_ensure_progress_pct(done, total), date)

    def runner():
        from services.ccass_range import run_ccass_backfill_leader
        try:
            run_ccass_backfill_leader(code, state, progress_callback=ccass_progress)
            if state.get('status') == 'done':
                update_stock_last_updated(code)
                _fetch_jobs[job_id].update({
                    'status': 'done', 'progress': 100, 'stage': 'complete',
                    'ccass_days': state.get('fetched') or 0,
                })
            else:
                _fetch_jobs[job_id].update({
                    'status': 'error',
                    'error': state.get('error') or 'CCASS backfill failed',
                })
        except Exception as e:
            logger.error(f"CCASS ensure job {job_id} failed: {e}")
            if job_id in _fetch_jobs:
                _fetch_jobs[job_id].update({'status': 'error', 'error': str(e)})

    threading.Thread(target=runner, daemon=True).start()


@app.post("/api/stock/{stock_code}/ccass/ensure")
def ensure_ccass_range(stock_code: str, days: int = Query(60, ge=1, le=365)):
    """Return coverage for the requested window and backfill when history is short.

    The crawl reuses backfill_stock (AntiBan rate limit, 2 workers, batches of 8).
    A window that was just attempted is not queued again, so refreshing the tab
    does not spray HKEX. 365 calendar days is the HKEX ceiling.
    """
    from services.ccass_range import (
        assess_ccass_coverage,
        claim_ccass_backfill,
        should_skip_automatic_backfill,
    )

    code = stock_code.zfill(5)
    ensure_stock(code)
    coverage = assess_ccass_coverage(_ccass_trade_dates(code), days)
    if coverage['sufficient']:
        return {**coverage, 'backfill': 'idle', 'job_id': None}

    recent = should_skip_automatic_backfill(code, days)
    if recent and recent['status'] == 'running':
        # target_days is already >= this request; join that crawl.
        return {**coverage, 'backfill': 'running', 'job_id': recent['job_id']}
    if recent and recent['status'] in ('done', 'error'):
        return {**coverage, 'backfill': recent['status'], 'job_id': recent['job_id']}

    job_id = uuid.uuid4().hex[:12]
    leader, state = claim_ccass_backfill(code, days, job_id)
    active_id = state['job_id']
    if active_id not in _fetch_jobs:
        _fetch_jobs[active_id] = {
            'status': 'running', 'progress': 0, 'stage': 'ccass',
            'code': code, 'target_days': state['target_days'],
        }
    else:
        _fetch_jobs[active_id]['target_days'] = state['target_days']
    if leader:
        _start_ccass_ensure_job(code, state)
    return {**coverage, 'backfill': 'running', 'job_id': active_id}


# ============ IR: Shareholder Attribution ============

@app.get("/api/stock/{stock_code}/attribution")
def get_attributions(stock_code: str):
    """Get shareholder-seat attributions."""
    code = stock_code.zfill(5)
    from services.selloff_engine import get_shareholder_summary
    return {'shareholders': get_shareholder_summary(code)}


@app.post("/api/stock/{stock_code}/attribution")
def add_attribution(stock_code: str, data: dict):
    """Manually add/edit a shareholder attribution."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO shareholder_attribution
            (shareholder_name, participant_id, participant_name, shares_baseline,
             investor_type, cost_hkd, unlock_date, attribution_type, confidence, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 'high', ?)
        """, (
            data.get('shareholder_name', ''),
            data.get('participant_id', '').upper(),
            data.get('participant_name', ''),
            data.get('shares_baseline', 0),
            data.get('investor_type', ''),
            data.get('cost_hkd'),
            data.get('unlock_date'),
            data.get('notes', ''),
        ))
    return {'status': 'ok'}


@app.delete("/api/stock/{stock_code}/attribution/{att_id}")
def delete_attribution(stock_code: str, att_id: int):
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        conn.execute("DELETE FROM shareholder_attribution WHERE id = ?", (att_id,))
    return {'status': 'ok'}


# ============ IR: Selloff Tracking ============

@app.get("/api/stock/{stock_code}/selloff")
def get_selloff(stock_code: str, days: int = Query(30, ge=1, le=90)):
    """Get shareholder selloff data."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute("""
            SELECT ss.*, dt.close
            FROM shareholder_selloff ss
            LEFT JOIN daily_trading dt ON ss.trade_date = dt.trade_date
            ORDER BY ss.trade_date DESC, ABS(ss.shares_change) DESC
            LIMIT ?
        """, (days * 20,)).fetchall()

        # Group by date
        result = {}
        for r in rows:
            d = r['trade_date']
            if d not in result:
                result[d] = {'date': d, 'close': r['close'], 'shareholders': []}
            if r['shares_change'] != 0:
                result[d]['shareholders'].append(dict(r))

        return {'data': list(result.values())[:days]}


# ============ IR: Unlock Calendar ============

@app.get("/api/stock/{stock_code}/unlocks")
def get_unlocks(stock_code: str):
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        today = datetime.now().strftime('%Y-%m-%d')
        upcoming = conn.execute("SELECT * FROM unlock_calendar WHERE unlock_date >= ? ORDER BY unlock_date", (today,)).fetchall()
        past = conn.execute("SELECT * FROM unlock_calendar WHERE unlock_date < ? ORDER BY unlock_date DESC LIMIT 20", (today,)).fetchall()
        return {'upcoming': [dict(r) for r in upcoming], 'past': [dict(r) for r in past]}


# ============ IR: File Upload ============

@app.post("/api/stock/{stock_code}/upload")
async def upload_file(stock_code: str, file: UploadFile = File(...)):
    """Upload a shareholder/broker mapping file and parse it."""
    code = stock_code.zfill(5)
    ensure_stock(code)

    upload_dir = get_stock_upload_dir(code)
    file_id = uuid.uuid4().hex[:8]
    # Sanitize filename to prevent path traversal (strip directory components)
    import re
    raw_name = os.path.basename(file.filename or 'upload')  # remove any ../ or directory parts
    raw_name = re.sub(r'[^\w.\-]+', '_', raw_name)  # allow only alphanumerics, dots, hyphens, underscores
    ext = os.path.splitext(raw_name)[1].lower()
    safe_name = f"{file_id}_{raw_name}"
    filepath = os.path.join(upload_dir, safe_name)

    content = await file.read()
    with open(filepath, 'wb') as f:
        f.write(content)

    # Parse the file
    from services.file_parser import parse_uploaded_file, apply_attributions
    try:
        result = parse_uploaded_file(filepath, file.filename)

        # Save upload record
        with get_meta_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO uploads (stock_code, filename, file_type, parsed_status, parsed_data, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (code, file.filename, result.get('file_type', ''),
                  'parsed' if result.get('attributions') else 'review',
                  json.dumps(result.get('summary', {}), ensure_ascii=False),
                  f"Saved as {safe_name}"))
            upload_id = cursor.lastrowid

        # Auto-apply attributions
        if result.get('attributions'):
            apply_attributions(code, result['attributions'], result.get('unlocks', []))
            # Mark IR as configured
            with get_meta_conn() as conn:
                conn.execute("UPDATE stocks SET is_ir_configured = 1 WHERE stock_code = ?", (code,))
            # Recompute selloff
            from services.selloff_engine import compute_selloff_range
            try:
                compute_selloff_range(code)
            except Exception as e:
                logger.error(f"Selloff recompute failed after upload: {e}")

        payload = {
            'status': 'ok' if result.get('attributions') else 'empty',
            'upload_id': upload_id,
            'summary': result.get('summary', {}),
            'attributions': result.get('attributions', []),
            'unlocks': result.get('unlocks', []),
            'preview_rows': result.get('rows_raw', [])[:5],
        }
        if result.get('error'):
            payload['error'] = result['error']
        return payload
    except Exception as e:
        logger.error(f"File parse error: {e}")
        raise HTTPException(500, f"Failed to parse file: {str(e)}")


# ============ Anomaly Detection / Signals ============

@app.get("/api/stock/{stock_code}/signals")
def get_signals(stock_code: str, date: str = None):
    """Get anomaly signals for a stock."""
    code = stock_code.zfill(5)
    from services.anomaly_detector import get_signal_score
    result = get_signal_score(code, date)
    return result


@app.get("/api/stock/{stock_code}/anomalies")
def get_anomalies(stock_code: str, date: str = None):
    """Get detailed anomaly list."""
    code = stock_code.zfill(5)
    from services.anomaly_detector import detect_anomalies
    anomalies = detect_anomalies(code, date)
    if not date:
        with get_stock_conn(code) as conn:
            latest = conn.execute("SELECT MAX(trade_date) as d FROM ccass_daily_changes").fetchone()
            date = latest['d'] if latest else None
    return {'date': date, 'anomalies': anomalies}


# ============ Config / Settings ============

@app.get("/api/config")
def get_config():
    """Get public config (redacts API keys)."""
    from services.config_service import load_config
    cfg = load_config()
    # Redact secrets
    public = dict(cfg)
    if public.get('llm', {}).get('api_key'):
        public['llm'] = dict(public['llm'])
        key = public['llm']['api_key']
        public['llm']['api_key'] = key[:8] + '****' + key[-4:] if len(key) > 12 else '****'
    if public.get('notification', {}).get('webhook_url'):
        public['notification'] = dict(public['notification'])
        public['notification']['webhook_url'] = '****'
    return public


@app.put("/api/config")
async def update_config(request: Request):
    """Update config."""
    from services.config_service import save_config
    body = await request.json()
    cfg = save_config(body)
    # The scheduler polls config periodically, so changes take effect without restart.
    return {'status': 'ok', 'config': cfg}


# ============ Auto-Fetch Scheduler ============

@app.get("/api/auto-fetch/status")
def auto_fetch_status():
    from services import scheduler
    return scheduler.status()


@app.post("/api/auto-fetch/run-now")
def auto_fetch_run_now():
    """Manually trigger an immediate fetch of all tracked stocks."""
    from services import scheduler
    return scheduler.trigger_now()


# ============ Excel Export ============

@app.get("/api/stock/{stock_code}/export")
def export_excel(stock_code: str):
    """Export all stock data to Excel file."""
    from fastapi.responses import Response
    from services.excel_export import export_stock_to_excel
    code = stock_code.zfill(5)
    data = export_stock_to_excel(code)
    filename = f"{code}_CCASS_Export_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return Response(
        content=data,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ============ HK Connect (Southbound) ============

@app.get("/api/stock/{stock_code}/hk-connect")
def get_hk_connect(stock_code: str, days: int = Query(60, ge=1, le=365)):
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        rows = conn.execute("""
            SELECT * FROM hk_connect ORDER BY trade_date DESC LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in reversed(rows)]


# ============ Knowledge Base / RAG ============

@app.post("/api/knowledge/documents")
async def add_knowledge_document(request: Request):
    """Add a document to the RAG knowledge base (for AI-enhanced reports)."""
    from services.rag_service import add_document, init_knowledge_base
    init_knowledge_base()
    body = await request.json()
    name = body.get('name', 'doc')
    content = body.get('content', '')
    category = body.get('category', 'custom')
    if not content:
        raise HTTPException(400, 'Content is required')
    path = add_document(name, content, category)
    return {'status': 'ok', 'path': path}


@app.get("/api/knowledge/documents")
def list_knowledge_documents():
    """List knowledge base documents."""
    from services.rag_service import KNOWLEDGE_DIR, init_knowledge_base
    init_knowledge_base()
    docs = []
    if os.path.exists(KNOWLEDGE_DIR):
        for f in os.listdir(KNOWLEDGE_DIR):
            if f.endswith('.md') and not f.startswith('_'):
                path = os.path.join(KNOWLEDGE_DIR, f)
                stats = os.stat(path)
                with open(path, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                docs.append({
                    'name': f,
                    'size': stats.st_size,
                    'chars': len(content),
                    'modified': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M'),
                })
    return {'documents': docs}


@app.delete("/api/knowledge/documents/{name}")
def delete_knowledge_document(name: str):
    """Delete a knowledge base document."""
    from services.rag_service import KNOWLEDGE_DIR
    path = os.path.join(KNOWLEDGE_DIR, name)
    if os.path.exists(path) and not name.startswith('_'):
        os.remove(path)
    return {'status': 'ok'}


@app.post("/api/knowledge/reindex")
def reindex_knowledge():
    """Rebuild the knowledge base index."""
    from services.rag_service import _build_index
    _build_index()
    return {'status': 'ok'}


# ============ Reports ============

@app.get("/api/stock/{stock_code}/report")
def generate_report(stock_code: str, report_type: str = Query('daily', regex='^(daily|weekly|monthly)$'),
                    mode: str = Query('trader', regex='^(trader|ir)$'),
                    format: str = Query('json', regex='^(json|html)$'),
                    use_ai: bool = Query(False),
                    lang: str = Query('zh', regex='^(zh|en)$')):
    from services.report_generator import generate_report as gen_report
    from services.html_report import render_html_report
    code = stock_code.zfill(5)
    report = gen_report(code, report_type, mode, use_ai=use_ai, lang=lang)
    if format == 'html':
        from fastapi.responses import HTMLResponse
        html = render_html_report(report)
        return HTMLResponse(content=html)
    return report


# ============ Cost Structure & Selling Pressure Analysis ============

@app.get("/api/stock/{stock_code}/cost-analysis")
def get_cost_analysis(stock_code: str):
    """Get shareholder cost structure and selling pressure analysis."""
    code = stock_code.zfill(5)
    ensure_stock(code)
    from services.cost_analysis import analyze_cost_structure
    return analyze_cost_structure(code)


@app.get("/api/stock/{stock_code}/scenario-analysis")
def get_scenario_analysis(stock_code: str):
    """Get selling pressure scenario analysis (optimistic/base/pessimistic)."""
    code = stock_code.zfill(5)
    ensure_stock(code)
    from services.cost_analysis import scenario_analysis
    return scenario_analysis(code)


# ============ Capital Flow ============

@app.get("/api/stock/{stock_code}/capital-flow")
def get_capital_flow(stock_code: str, days: int = Query(5, ge=1, le=365), end_date: str = Query(None)):
    """Get capital flow / Sankey data for shareholder → broker → buy/sell flows."""
    code = stock_code.zfill(5)
    ensure_stock(code)
    from services.cost_analysis import get_capital_flow
    return get_capital_flow(code, days=days, end_date=end_date)


# ============ Dashboard Summary ============

@app.get("/api/stock/{stock_code}/dashboard")
def get_dashboard(stock_code: str):
    """Get full dashboard summary for a stock."""
    code = stock_code.zfill(5)

    with get_stock_conn(code) as conn:
        # Latest trading
        trade = conn.execute("SELECT * FROM daily_trading ORDER BY trade_date DESC LIMIT 1").fetchone()
        ccass = conn.execute("SELECT * FROM ccass_total ORDER BY trade_date DESC LIMIT 1").fetchone()
        hk = conn.execute("SELECT * FROM hk_connect ORDER BY trade_date DESC LIMIT 1").fetchone()

        # 30-day trading summary
        trades_30d = conn.execute("""
            SELECT AVG(volume) as avg_vol, AVG(turnover) as avg_turnover,
                   MIN(low) as period_low, MAX(high) as period_high
            FROM (SELECT * FROM daily_trading ORDER BY trade_date DESC LIMIT 30)
        """).fetchone()

        # Top 10 holders
        if ccass:
            top10 = conn.execute("""
                SELECT * FROM ccass_top10 WHERE trade_date = ? ORDER BY rank
            """, (ccass['trade_date'],)).fetchall()
        else:
            top10 = []

        # Attribution summary
        attr_count = conn.execute("SELECT COUNT(*) as c FROM shareholder_attribution").fetchone()
        known_shares = conn.execute("""
            SELECT SUM(cp.shares) as total
            FROM ccass_participants cp
            JOIN shareholder_attribution sa ON cp.participant_id = sa.participant_id
            WHERE cp.trade_date = (SELECT MAX(trade_date) FROM ccass_participants)
        """).fetchone()

    # Signals
    from services.anomaly_detector import get_signal_score
    signals = get_signal_score(code)

    # Stock info
    info = {}
    with get_stock_conn(code) as conn:
        for r in conn.execute("SELECT key, value FROM stock_info"):
            try:
                info[r['key']] = json.loads(r['value']) if r['value'] else None
            except:
                info[r['key']] = r['value']

    return {
        'stock_code': code,
        'stock_name': info.get('stock_name', ''),
        'info': info,
        'latest_trading': dict(trade) if trade else None,
        'latest_ccass': dict(ccass) if ccass else None,
        'latest_hk_connect': dict(hk) if hk else None,
        'trading_30d': dict(trades_30d) if trades_30d else None,
        'top10': [dict(r) for r in top10],
        'ir_configured': (attr_count['c'] > 0) if attr_count else False,
        'known_shares': known_shares['total'] if known_shares else 0,
        'signals': signals,
    }


# ============ Frontend Static Serving ============
if os.path.isdir(FRONTEND_DIST):
    from fastapi.responses import FileResponse, Response

    @app.get("/assets/{file_path:path}")
    async def serve_assets(file_path: str):
        asset_path = os.path.join(FRONTEND_DIST, "assets", file_path)
        if os.path.exists(asset_path):
            return FileResponse(asset_path)
        raise HTTPException(404)

    @app.get("/favicon.ico")
    async def serve_favicon():
        fav = os.path.join(FRONTEND_DIST, "favicon.ico")
        if os.path.exists(fav):
            return FileResponse(fav)
        return Response(status_code=204)

    @app.get("/")
    async def serve_index():
        html_path = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(html_path):
            return FileResponse(html_path)
        return Response(content="Frontend not built. Run cd frontend && npm run build", media_type="text/plain")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA for non-API, non-asset routes."""
        if full_path.startswith("api/"):
            raise HTTPException(404)
        # Don't intercept asset-like paths
        if '.' in full_path.split('/')[-1]:
            raise HTTPException(404)
        html_path = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(html_path):
            return FileResponse(html_path)
        return Response(content="Frontend not built", media_type="text/plain")


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3003)
