"""
CCASS Scraper v2 — multi-stock, parallelized, session-reusing.
Fetches CCASS participant holdings, total holdings, and HK Connect data from HKEX.
Uses ThreadPoolExecutor for parallel date fetching to drastically speed up backfill.

v2.1 anti-ban: uses anti_ban.AntiBanClient for rotating UA, rate limiting,
exponential backoff, session rotation, and proxy support.
"""
import logging
import time
import random
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from bs4 import BeautifulSoup

from .anti_ban import make_ccass_client

logger = logging.getLogger(__name__)

CCASS_SEARCH_URL = 'https://www3.hkexnews.hk/sdw/search/searchsdw_c.aspx'
CCASS_MUTUAL_URL = 'https://www3.hkexnews.hk/sdw/search/mutualmarket_c.aspx?t=hk'

# Thread-local clients to avoid connection sharing issues
_thread_local = threading.local()

# Cache for form data (per URL, refreshed periodically)
_form_cache = {}
_form_cache_lock = threading.Lock()
FORM_CACHE_TTL = 300  # seconds


def parse_number(text):
    if not text:
        return None
    text = str(text).strip().replace(',', '').replace(' ', '').replace('%', '')
    try:
        return int(text) if '.' not in text else float(text)
    except (ValueError, TypeError):
        return None


def _get_client():
    """Get or create a thread-local AntiBanClient."""
    if not hasattr(_thread_local, 'client'):
        _thread_local.client = make_ccass_client()
    return _thread_local.client


def _get_form_data_cached(url: str) -> tuple[dict, BeautifulSoup]:
    """Get form data with caching to avoid repeated GET requests."""
    now = time.time()
    with _form_cache_lock:
        if url in _form_cache:
            cached_time, form_data = _form_cache[url]
            if now - cached_time < FORM_CACHE_TTL:
                return form_data, None

    client = _get_client()
    for attempt in range(3):
        try:
            r = client.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
            form_data = {}
            for inp in soup.find_all(['input', 'select', 'textarea']):
                name = inp.get('name', '')
                if name:
                    form_data[name] = inp.get('value', '')

            with _form_cache_lock:
                _form_cache[url] = (now, form_data)
            return form_data, soup
        except Exception as e:
            logger.warning(f"Form data fetch attempt {attempt+1} failed for {url}: {e}")
            time.sleep(1.0 * (attempt + 1))
    return {}, None


def invalidate_form_cache():
    """Clear form cache (e.g. after session errors)."""
    with _form_cache_lock:
        _form_cache.clear()


def _cell_text(cell) -> str:
    body = cell.find('div', class_='mobile-list-body')
    return body.get_text(strip=True) if body else cell.get_text(strip=True)


def _parse_ccass_summary(soup) -> Optional[dict]:
    """Parse the CCASS total box. Percentage may be blank."""
    summary_div = soup.find('div', class_='ccass-search-total')
    if not summary_div:
        return None

    def value_of(cls: str):
        box = summary_div.find('div', class_=cls)
        if not box:
            return None
        val = box.find('div', class_='value')
        if not val:
            return None
        return parse_number(val.get_text(strip=True).replace('%', ''))

    total_shares = value_of('shareholding')
    if not total_shares:
        return None
    return {
        'total_shares': total_shares,
        'total_pct': value_of('percent-of-participants'),
        'participant_count': value_of('number-of-participants'),
    }


def _parse_participant_rows(soup) -> list[dict]:
    """Parse CCASS participant rows.

    HKEX drops the "% of issued shares/units" column — and leaves the summary
    percentage blank — when it has no current issued-units figure. That happens
    for some ETFs and leveraged products with daily creation/redemption
    (reproduced on 07709). Identify columns by CSS class so a missing percent
    column does not discard the shareholding rows.
    """
    tables = soup.find_all('table')
    data_table = None
    for table in tables:
        if table.find('td', class_='col-shareholding'):
            data_table = table
            break
    if data_table is None and len(tables) >= 2:
        data_table = tables[1]
    if data_table is None:
        return []

    participants = []
    for row in data_table.find_all('tr'):
        cells = row.find_all('td')
        if not cells:
            continue

        by_class: dict[str, str] = {}
        for cell in cells:
            for cls in (cell.get('class') or []):
                if cls.startswith('col-'):
                    by_class[cls] = _cell_text(cell)

        pct = None
        if 'col-participant-id' in by_class or 'col-shareholding' in by_class:
            pid = by_class.get('col-participant-id', '')
            name = by_class.get('col-participant-name', '')
            shares = parse_number(by_class.get('col-shareholding'))
            pct_raw = by_class.get('col-shareholding-percent')
            if pct_raw:
                pct = parse_number(pct_raw.replace('%', ''))
        else:
            bodies = [_cell_text(c) for c in cells]
            if len(bodies) < 4:
                continue
            pid = bodies[0]
            name = bodies[1]
            if '%' in bodies[-1]:
                shares = parse_number(bodies[-2])
                pct = parse_number(bodies[-1].replace('%', ''))
            else:
                shares = parse_number(bodies[-1])

        if pid and shares is not None:
            participants.append({
                'participant_id': pid,
                'participant_name': name,
                'shares': shares,
                'pct_of_issued': pct,
            })
    return participants


def fetch_ccass_participants(stock_code: str, query_date: str) -> tuple[str, list[dict], Optional[dict]]:
    """
    Fetch CCASS participant holdings + total summary for a given stock and query date.
    query_date format: 'YYYY/MM/DD'
    Returns (data_date, participants_list, ccass_total_dict).
    ccass_total_dict: {'total_shares', 'total_pct', 'participant_count'}
    """
    code = stock_code.zfill(5)
    data_date = compute_query_to_data_date(query_date)
    client = _get_client()

    # AntiBanClient handles retries/backoff internally; we do one outer call
    try:
        form_data, _ = _get_form_data_cached(CCASS_SEARCH_URL)
        if not form_data:
            return data_date, [], None

        fd = dict(form_data)
        fd['txtShareholdingDate'] = query_date
        fd['txtStockCode'] = code
        fd['txtStockName'] = ''
        fd['txtParticipantID'] = ''
        fd['txtParticipantName'] = ''
        fd['txtSelPartID'] = ''
        fd['__EVENTTARGET'] = 'btnSearch'
        fd['__EVENTARGUMENT'] = ''

        r = client.post(
            CCASS_SEARCH_URL,
            data=fd,
            headers={
                "Origin": "https://www3.hkexnews.hk",
                "Content-Type": "application/x-www-form-urlencoded",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
            },
        )
    except Exception as e:
        logger.warning(f"CCASS participant fetch failed for {code} {query_date}: {e}")
        invalidate_form_cache()
        return data_date, [], None

    soup = BeautifulSoup(r.text, 'html.parser')

    # Verify date
    date_input = soup.find('input', {'id': 'txtShareholdingDate'})
    shown_date = date_input.get('value', '') if date_input else ''
    if shown_date != query_date:
        return data_date, [], None

    # Verify stock code
    code_input = soup.find('input', {'id': 'txtStockCode'})
    shown_code = code_input.get('value', '') if code_input else ''
    if shown_code != code:
        return data_date, [], None

    ccass_total = _parse_ccass_summary(soup)
    participants = _parse_participant_rows(soup)
    return data_date, participants, ccass_total


# Cache for mutual market form data per date (it's date-specific response)
_mutual_cache = {}
_mutual_cache_lock = threading.Lock()


def fetch_hk_connect(stock_code: str, query_date: str) -> Optional[dict]:
    """
    Fetch HK Connect (southbound) holdings from mutual market page.
    The mutual market page only contains southbound holdings (Stock Connect), not total CCASS.
    Returns {'hk_connect_shares', 'hk_connect_pct'} or None.
    """
    code_num = str(int(stock_code))

    with _mutual_cache_lock:
        cached_html = _mutual_cache.get(query_date)

    if cached_html is None:
        client = _get_client()
        try:
            form_data, _ = _get_form_data_cached(CCASS_MUTUAL_URL)
            if not form_data:
                return None
            fd = dict(form_data)
            fd['txtShareholdingDate'] = query_date
            fd['__EVENTTARGET'] = 'btnSearch'
            fd['__EVENTARGUMENT'] = ''

            r = client.post(
                CCASS_MUTUAL_URL,
                data=fd,
                headers={
                    "Origin": "https://www3.hkexnews.hk",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": CCASS_MUTUAL_URL,
                },
            )
            cached_html = r.text
            with _mutual_cache_lock:
                _mutual_cache[query_date] = cached_html
        except Exception as e:
            logger.warning(f"Mutual market fetch failed for {query_date}: {e}")
            invalidate_form_cache()
            return None

    soup = BeautifulSoup(cached_html, 'html.parser')
    tables = soup.find_all('table')

    # The mutual market page's stock data table is the one with stock code rows (Table 1 typically)
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            code_text = cells[0].get_text(strip=True)
            code_val = parse_number(code_text.split(':')[-1] if ':' in code_text else code_text)
            if code_val != int(code_num):
                continue

            def parse_share_cell(cell_text):
                for prefix in ['Shareholding in CCASS:', '於中央結算系統的持股量:', '持股量:']:
                    if prefix in cell_text:
                        return parse_number(cell_text.replace(prefix, '').replace(',', '').strip())
                return None

            def parse_pct_cell(cell_text):
                for prefix in ['% of the total number of Issued Shares/Units:', '佔已發行股份/單位百分比:', '百分比:']:
                    if prefix in cell_text:
                        return parse_number(cell_text.replace(prefix, '').replace('%', '').strip())
                return None

            shares_text = cells[2].get_text(strip=True)
            pct_text = cells[3].get_text(strip=True)
            shares = parse_share_cell(shares_text)
            pct = parse_pct_cell(pct_text)

            if shares:
                return {'hk_connect_shares': shares, 'hk_connect_pct': pct}

    return None


def _fetch_one_day(args: tuple) -> tuple[str, list[dict], Optional[dict]]:
    """Fetch one day of CCASS data. Returns (data_date, participants, ccass_total_with_hkconnect)."""
    code, query_date = args
    try:
        data_date, participants, ccass_total = fetch_ccass_participants(code, query_date)
        if participants:
            # Merge HK Connect data
            hk = fetch_hk_connect(code, query_date)
            if hk and ccass_total:
                ccass_total.update(hk)
            elif hk:
                ccass_total = hk
            # Small jitter delay between days within a thread
            time.sleep(0.3 + random.random() * 0.4)
        return data_date, participants, ccass_total
    except Exception as e:
        logger.error(f"Error fetching {code} {query_date}: {e}")
        return query_date.replace('/', '-'), [], None


def save_ccass_data(stock_code: str, data_date: str, participants: list[dict], ccass_total: dict = None,
                    prev_participant_ids: set = None):
    """Save fetched CCASS data to the per-stock database."""
    from db import get_stock_conn

    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        if ccass_total:
            conn.execute("""
                INSERT OR REPLACE INTO ccass_total (trade_date, total_shares, pct_of_issued)
                VALUES (?, ?, ?)
            """, (data_date, ccass_total.get('total_shares'), ccass_total.get('total_pct')))

            if ccass_total.get('hk_connect_shares') is not None:
                prev_hk = conn.execute(
                    "SELECT shares FROM hk_connect WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                    (data_date,)
                ).fetchone()
                prev_shares = prev_hk['shares'] if prev_hk else 0
                hk_change = ccass_total['hk_connect_shares'] - prev_shares if prev_shares else 0
                conn.execute("""
                    INSERT OR REPLACE INTO hk_connect (trade_date, shares, pct_of_issued, shares_change)
                    VALUES (?, ?, ?, ?)
                """, (data_date, ccass_total['hk_connect_shares'], ccass_total.get('hk_connect_pct'), hk_change))

        for p in participants:
            conn.execute("""
                INSERT OR REPLACE INTO ccass_participants
                (trade_date, participant_id, participant_name, shares, pct_of_issued)
                VALUES (?, ?, ?, ?, ?)
            """, (data_date, p['participant_id'], p['participant_name'], p['shares'], p['pct_of_issued']))

        sorted_p = sorted(participants, key=lambda x: x['shares'] or 0, reverse=True)
        for rank, p in enumerate(sorted_p[:10], 1):
            conn.execute("""
                INSERT OR REPLACE INTO ccass_top10 (trade_date, rank, participant_id, participant_name, shares, pct_of_issued)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (data_date, rank, p['participant_id'], p['participant_name'], p['shares'], p['pct_of_issued']))

        if prev_participant_ids is not None:
            current_ids = {p['participant_id'] for p in participants}
            new_ids = current_ids - prev_participant_ids
            exited_ids = prev_participant_ids - current_ids

            for p in participants:
                if p['participant_id'] in new_ids:
                    conn.execute("""
                        INSERT OR REPLACE INTO ccass_new_participants
                        (trade_date, participant_id, participant_name, shares, pct_of_issued, is_new)
                        VALUES (?, ?, ?, ?, ?, 1)
                    """, (data_date, p['participant_id'], p['participant_name'], p['shares'], p['pct_of_issued']))

            for pid in exited_ids:
                prev = conn.execute(
                    "SELECT participant_name FROM ccass_participants WHERE participant_id = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                    (pid, data_date)
                ).fetchone()
                name = prev['participant_name'] if prev else ''
                conn.execute("""
                    INSERT OR REPLACE INTO ccass_new_participants
                    (trade_date, participant_id, participant_name, shares, pct_of_issued, is_new)
                    VALUES (?, ?, ?, 0, 0, 0)
                """, (data_date, pid, name))

        _compute_daily_changes(conn, data_date)


def _compute_daily_changes(conn, data_date: str):
    """Compute daily changes by comparing with previous trading day."""
    prev_date_row = conn.execute(
        "SELECT MAX(trade_date) as d FROM ccass_participants WHERE trade_date < ?", (data_date,)
    ).fetchone()
    if not prev_date_row or not prev_date_row['d']:
        return
    prev_date = prev_date_row['d']

    prev_map = {}
    for row in conn.execute("SELECT participant_id, shares FROM ccass_participants WHERE trade_date = ?", (prev_date,)):
        prev_map[row['participant_id']] = row['shares']

    def get_hist_map(n):
        dates = [r['trade_date'] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM ccass_participants WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (data_date, n + 1)
        )]
        if len(dates) > n:
            target_date = dates[n]
            m = {}
            for r in conn.execute("SELECT participant_id, shares FROM ccass_participants WHERE trade_date = ?", (target_date,)):
                m[r['participant_id']] = r['shares']
            return m
        return {}

    prev3_map = get_hist_map(3)
    prev5_map = get_hist_map(5)

    current = conn.execute("SELECT * FROM ccass_participants WHERE trade_date = ?", (data_date,)).fetchall()
    for p in current:
        pid = p['participant_id']
        cur_shares = p['shares'] or 0
        prev_shares = prev_map.get(pid, 0)
        change_1d = cur_shares - prev_shares
        change_3d = cur_shares - prev3_map.get(pid, cur_shares) if prev3_map else 0
        change_5d = cur_shares - prev5_map.get(pid, cur_shares) if prev5_map else 0

        conn.execute("""
            INSERT OR REPLACE INTO ccass_daily_changes
            (trade_date, participant_id, participant_name, shares, shares_change,
             shares_change_3d, shares_change_5d)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data_date, pid, p['participant_name'], cur_shares, change_1d, change_3d, change_5d))


def compute_query_to_data_date(query_date_str: str) -> str:
    """Convert query date (YYYY/MM/DD) to data date (YYYY-MM-DD).

    HKEX CCASS returns holdings as at the requested ``txtShareholdingDate``,
    which is already the latest published session (T+1). The query date is
    therefore the data date, and must not be shifted.
    """
    return datetime.strptime(query_date_str, '%Y/%m/%d').strftime('%Y-%m-%d')


def find_latest_query_date() -> str:
    """Find the latest available HKEX disclosure date."""
    now = datetime.utcnow() + timedelta(hours=8)  # HKT
    qd = now - timedelta(days=1)
    while qd.weekday() >= 5:
        qd -= timedelta(days=1)
    return qd.strftime('%Y/%m/%d')


def _participant_ids_before(conn, data_date: str) -> set:
    """Participant ids on the latest stored session strictly before `data_date`."""
    row = conn.execute(
        "SELECT MAX(trade_date) as d FROM ccass_participants WHERE trade_date < ?",
        (data_date,),
    ).fetchone()
    if not row or not row['d']:
        return set()
    return {
        r['participant_id']
        for r in conn.execute(
            "SELECT participant_id FROM ccass_participants WHERE trade_date = ?",
            (row['d'],),
        )
    }


def backfill_stock(stock_code: str, start_date: str = None, days: int = 90, delay: float = 0.2,
                   progress_callback=None, max_workers: int = 2, batch_size: int = None):
    """
    Backfill CCASS data for a stock.

    Dates are fetched in small batches with the existing AntiBan client
    (per-host interval + max_workers, default 2). Each batch is saved before
    the next one starts, so a 180/365-day fill does not queue the whole year
    of HKEX requests up front. The calendar window is capped at 12 months.
    `delay` is retained for callers; pacing itself lives in AntiBanClient.
    """
    from db import get_stock_conn, ensure_stock, update_stock_last_updated
    from services.ccass_range import (
        BACKFILL_BATCH_SIZE,
        HKEX_CCASS_MAX_CALENDAR_DAYS,
        chunked,
        plan_backfill_query_dates,
    )

    code = ensure_stock(stock_code)
    logger.info(f"Backfilling CCASS data for {code}, {days} days (parallel workers={max_workers})")

    end = datetime.strptime(find_latest_query_date(), '%Y/%m/%d')
    with get_stock_conn(code) as conn:
        existing = {r['trade_date'] for r in conn.execute("SELECT DISTINCT trade_date FROM ccass_participants").fetchall()}
        prior_min_row = conn.execute("SELECT MIN(trade_date) as d FROM ccass_participants").fetchone()
        prior_min = prior_min_row['d'] if prior_min_row else None

    if start_date:
        # Explicit start still cannot walk past the 12-month HKEX window.
        requested_start = datetime.strptime(start_date, '%Y/%m/%d')
        floor = end - timedelta(days=HKEX_CCASS_MAX_CALENDAR_DAYS)
        start = requested_start if requested_start > floor else floor
        tasks = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                date_str = cur.strftime('%Y/%m/%d')
                data_date = compute_query_to_data_date(date_str)
                if data_date not in existing:
                    tasks.append(date_str)
            cur += timedelta(days=1)
    else:
        tasks = plan_backfill_query_dates(existing, end, days, compute_query_to_data_date)

    total_tasks = len(tasks)
    if total_tasks == 0:
        logger.info(f"No new dates to fetch for {code}")
        update_stock_last_updated(code)
        return 0

    logger.info(f"Fetching {total_tasks} new dates for {code} in batches")
    step = batch_size if batch_size else BACKFILL_BATCH_SIZE
    workers = max(1, int(max_workers))
    done_count = 0
    success_count = 0
    saved_dates = []

    for batch in chunked(tasks, step):
        results = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_one_day, (code, query_date)): query_date for query_date in batch}
            for future in as_completed(futures):
                query_date = futures[future]
                data_date = query_date.replace('/', '-')
                try:
                    data_date, participants, ccass_total = future.result()
                    if participants:
                        results[data_date] = (participants, ccass_total)
                except Exception as e:
                    logger.error(f"Future error: {e}")
                done_count += 1
                if progress_callback:
                    progress_callback(done_count, total_tasks, data_date)

        for data_date in sorted(results):
            participants, ccass_total = results[data_date]
            try:
                with get_stock_conn(code) as conn:
                    prev_ids = _participant_ids_before(conn, data_date)
                save_ccass_data(code, data_date, participants, ccass_total, prev_ids)
                saved_dates.append(data_date)
                success_count += 1
            except Exception as e:
                logger.error(f"Save error for {data_date}: {e}")

    # Prepending older sessions leaves the previous earliest day pointing at the
    # wrong prior session. Recompute just that boundary row.
    if prior_min and any(d < prior_min for d in saved_dates):
        try:
            with get_stock_conn(code) as conn:
                _compute_daily_changes(conn, prior_min)
        except Exception as e:
            logger.error(f"Boundary change recompute failed for {code} {prior_min}: {e}")

    update_stock_last_updated(code)
    logger.info(f"Backfill complete for {code}: {success_count}/{total_tasks} new dates")
    return success_count


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    if len(sys.argv) < 3:
        print("Usage: python ccass_scraper.py <stock_code> [backfill|daily] [days]")
        sys.exit(1)

    code = sys.argv[1]
    cmd = sys.argv[2]

    if cmd == 'backfill':
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
        backfill_stock(code, days=days, max_workers=2)
    elif cmd == 'daily':
        qd = find_latest_query_date()
        dd = compute_query_to_data_date(qd)
        dd_str, participants, ccass_total = fetch_ccass_participants(code, qd)
        time.sleep(0.2)
        if participants:
            hk = fetch_hk_connect(code, qd)
            if hk and ccass_total:
                ccass_total.update(hk)
            from db import ensure_stock, get_stock_conn
            ensure_stock(code)
            with get_stock_conn(code) as conn:
                prev_dates = conn.execute("SELECT DISTINCT trade_date FROM ccass_participants ORDER BY trade_date DESC LIMIT 1").fetchall()
                prev_ids = set()
                if prev_dates:
                    prev_ids = {r['participant_id'] for r in conn.execute(
                        "SELECT participant_id FROM ccass_participants WHERE trade_date = ?", (prev_dates[0]['trade_date'],)
                    )}
            save_ccass_data(code, dd, participants, ccass_total, prev_ids)
            print(f"Saved daily data for {code} on {dd}")
