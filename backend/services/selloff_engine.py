"""
Selloff attribution engine — multi-stock.
Computes daily shareholder-level net selling based on seat attributions.

Key logic:
- For mixed seats (custodian banks with multiple shareholders), one CCASS participant maps to multiple shareholders
- Each shareholder has a rule that defines how daily seat flows are attributed
- Remaining shares = baseline - cumulative recognized net selling (NOT current seat holdings)
- Static shareholders (rule='static') have constant holdings (static_shares), no selling

Rules:
- static        : no change ever, remaining = static_shares
- all_flow      : all net changes (inflow AND outflow) on seat = shareholder change (for seat==single shareholder)
- all_outflow   : only net outflows count as selling; net inflows NOT counted as buying back (safeguard)
- wpp_first     : shareholder sells first on a mixed seat until sell_end_date, then 100% cleared
- citi_kpcb     : after wpp_first clears, ALL net outflows on that seat = this shareholder selling; inflows != buybacks
                  baseline = seat holdings on sell_start date minus any static shareholders on same seat
- hsbc_dahlia   : ALL net changes on seat = this shareholder; baseline = seat holdings on sell_start minus other static shareholders on same seat
- dahlia_cleared: like hsbc_dahlia but confirmed cleared by sell_end_date (remaining=0 after)
- ht_sequoia    : net outflows from sell_start until sell_end (100% cleared after end_date)
- cicc_chunhua  : manual tracking; remaining = baseline - known_sold_shares (no auto flow-tracking on mixed seat)
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_participant_ts(conn, participant_id):
    """Get full time series for a participant: {date: shares}."""
    rows = conn.execute(
        "SELECT trade_date, shares FROM ccass_participants WHERE participant_id = ? ORDER BY trade_date",
        (participant_id,)
    ).fetchall()
    return {r['trade_date']: r['shares'] for r in rows}


def _get_daily_changes_for_participant(conn, participant_id, start_date=None, end_date=None):
    """Get daily shares_change for a participant, ordered by date."""
    if start_date and end_date:
        rows = conn.execute(
            "SELECT trade_date, shares_change FROM ccass_daily_changes WHERE participant_id = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (participant_id, start_date, end_date)
        ).fetchall()
    elif start_date:
        rows = conn.execute(
            "SELECT trade_date, shares_change FROM ccass_daily_changes WHERE participant_id = ? AND trade_date >= ? ORDER BY trade_date",
            (participant_id, start_date)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT trade_date, shares_change FROM ccass_daily_changes WHERE participant_id = ? ORDER BY trade_date",
            (participant_id,)
        ).fetchall()
    return [(r['trade_date'], r['shares_change'] or 0) for r in rows]


def _get_close(conn, trade_date):
    row = conn.execute("SELECT close FROM daily_trading WHERE trade_date = ?", (trade_date,)).fetchone()
    return row['close'] if row else None


def compute_shareholder_selloff(stock_code: str, trade_date: str = None):
    """
    Compute shareholder-level selloff for a given date (or latest available).
    Remaining = baseline - cumulative recognized selling (rule-based).
    """
    from db import get_stock_conn
    code = stock_code.zfill(5)

    with get_stock_conn(code) as conn:
        if trade_date:
            target_date = trade_date
        else:
            latest = conn.execute("SELECT MAX(trade_date) as d FROM ccass_daily_changes").fetchone()
            if not latest or not latest['d']:
                logger.warning(f"No CCASS data for {code}")
                return []
            target_date = latest['d']

        attributions = conn.execute("SELECT * FROM shareholder_attribution").fetchall()
        if not attributions:
            return []

        # Pre-compute static holdings per participant (sum of static_shares for rule='static' shareholders)
        static_by_pid = {}
        for a in attributions:
            if (a['rule'] or '') == 'static':
                p = a['participant_id']
                static_by_pid[p] = static_by_pid.get(p, 0) + (a['static_shares'] or 0)

        # Cache time series per participant
        ts_cache = {}

        def get_ts(pid):
            if pid not in ts_cache:
                ts_cache[pid] = _get_participant_ts(conn, pid)
            return ts_cache[pid]

        results = []

        for att in attributions:
            sh_name = att['shareholder_name']
            pid = att['participant_id']
            rule = att['rule'] or 'all_flow'
            baseline = att['shares_baseline'] or 0
            static_shares = att['static_shares'] or 0
            sell_start = att['sell_start_date']
            sell_end = att['sell_end_date']
            ts = get_ts(pid)
            seat_static_total = static_by_pid.get(pid, 0)

            shares_change_today = 0
            shares_remaining = 0
            notes = att['notes'] or ''

            # ---- Static shareholders: no selling ----
            if rule == 'static':
                shares_remaining = static_shares
                shares_change_today = 0

            # ---- wpp_first: first seller on mixed seat; sells until sell_end, then 100% cleared ----
            elif rule == 'wpp_first':
                baseline_sh = baseline
                end_d = sell_end
                if not baseline_sh or not end_d:
                    shares_remaining = baseline_sh
                    shares_change_today = 0
                elif sell_start:
                    dates = sorted([d for d in ts.keys() if sell_start <= d <= end_d])
                    if dates:
                        prev_sh = ts[dates[0]]
                        for d in dates:
                            cur_sh = ts[d]
                            ch = cur_sh - prev_sh
                            if d == end_d and target_date >= end_d:
                                shares_change_today = ch
                                if d == target_date:
                                    shares_change_today = ch
                            elif d == target_date:
                                shares_change_today = ch
                            prev_sh = cur_sh
                    if target_date > end_d:
                        shares_remaining = 0
                        shares_change_today = 0
                    elif target_date == end_d:
                        shares_remaining = 0
                    else:
                        dates2 = sorted([d for d in ts.keys() if sell_start <= d <= target_date])
                        prev_sh2 = ts.get(dates2[0], baseline_sh) if dates2 else baseline_sh
                        cum_sell = 0
                        for d in dates2:
                            cur_sh = ts[d]
                            ch = cur_sh - prev_sh2
                            if ch < 0:
                                cum_sell += -ch
                            if d == target_date:
                                shares_change_today = ch if ch < 0 else 0
                            prev_sh2 = cur_sh
                        shares_remaining = max(0, baseline_sh - cum_sell)

            # ---- citi_kpcb: after a prior seller clears, ALL net outflows on seat = this shareholder; inflows != buybacks ----
            elif rule == 'citi_kpcb':
                # baseline = seat holdings on sell_start date minus static shareholders on same seat
                baseline_date = sell_start or (min(ts.keys()) if ts else None)
                if baseline_date:
                    base_row = conn.execute(
                        "SELECT shares FROM ccass_participants WHERE participant_id = ? AND trade_date = ?",
                        (pid, baseline_date)
                    ).fetchone()
                    seat_base = base_row['shares'] if base_row else 0
                    baseline_sh = seat_base - seat_static_total
                    cum_sell = 0
                    dates = sorted([d for d in ts.keys() if d > baseline_date and d <= target_date])
                    prev_sh = seat_base
                    for d in dates:
                        cur_sh = ts[d]
                        ch = cur_sh - prev_sh
                        if ch < 0:
                            cum_sell += -ch
                        if d == target_date:
                            shares_change_today = ch if ch < 0 else 0
                        prev_sh = cur_sh
                    shares_remaining = max(0, baseline_sh - cum_sell)
                    baseline = baseline_sh  # update for display

            # ---- dahlia_cleared: mixed-seat shareholder, all net flows = this shareholder; confirmed cleared by sell_end ----
            elif rule == 'dahlia_cleared':
                baseline_sh = baseline
                if target_date >= (sell_end or target_date):
                    shares_remaining = 0
                    shares_change_today = 0
                else:
                    # During selling: use all net flows (both directions count)
                    # Starting position = seat holdings on sell_start minus static shareholders on same seat
                    start_date = sell_start or min(ts.keys()) if ts else None
                    if start_date:
                        hsbc_base_row = conn.execute(
                            "SELECT shares FROM ccass_participants WHERE participant_id = ? AND trade_date = ?",
                            (pid, start_date)
                        ).fetchone()
                        hsbc_base = hsbc_base_row['shares'] if hsbc_base_row else 0
                        dahlia_start = hsbc_base - seat_static_total
                        dates = sorted([d for d in ts.keys() if d > start_date and d <= target_date])
                        prev_sh = hsbc_base
                        cum_net = 0
                        for d in dates:
                            cur_sh = ts[d]
                            ch = cur_sh - prev_sh
                            cum_net += ch
                            if d == target_date:
                                shares_change_today = ch
                            prev_sh = cur_sh
                        shares_remaining = max(0, dahlia_start + cum_net)
                baseline = baseline_sh

            # ---- hsbc_dahlia (legacy, mixed seat): all net flows = this shareholder ----
            elif rule == 'hsbc_dahlia':
                baseline_date = sell_start or (min(ts.keys()) if ts else None)
                if baseline_date:
                    base_row = conn.execute(
                        "SELECT shares FROM ccass_participants WHERE participant_id = ? AND trade_date = ?",
                        (pid, baseline_date)
                    ).fetchone()
                    hsbc_base = base_row['shares'] if base_row else 0
                    # Starting position = seat holdings on baseline_date minus other static shareholders on same seat
                    baseline_sh = hsbc_base - seat_static_total
                    cum_net = 0
                    dates = sorted([d for d in ts.keys() if d > baseline_date and d <= target_date])
                    prev_sh = hsbc_base
                    for d in dates:
                        cur_sh = ts[d]
                        ch = cur_sh - prev_sh
                        cum_net += ch
                        if d == target_date:
                            shares_change_today = ch
                        prev_sh = cur_sh
                shares_remaining = max(0, baseline_sh + cum_net)
                baseline = baseline_sh

            # ---- ht_sequoia: net outflows from sell_start until sell_end (100% cleared after end_date) ----
            elif rule == 'ht_sequoia':
                baseline_sh = baseline
                end_d = sell_end
                if not baseline_sh:
                    # No baseline set; cannot compute without it
                    shares_remaining = 0
                    shares_change_today = 0
                elif end_d and target_date > end_d:
                    shares_remaining = 0
                    shares_change_today = 0
                elif end_d and target_date == end_d:
                    # Clearance day: remaining=0, show day's change
                    shares_remaining = 0
                    dates = sorted([d for d in ts.keys() if d == end_d])
                    if dates:
                        prev_date_row = conn.execute(
                            "SELECT shares FROM ccass_participants WHERE participant_id=? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                            (pid, end_d)
                        ).fetchone()
                        cur_sh = ts[end_d]
                        if prev_date_row:
                            ch = cur_sh - prev_date_row['shares']
                            shares_change_today = ch if ch < 0 else 0
                else:
                    start_d = sell_start or (min(ts.keys()) if ts else None)
                    if start_d:
                        dates = sorted([d for d in ts.keys() if d >= start_d and d <= target_date])
                        prev_sh = ts.get(start_d, baseline_sh)
                        cum_outflow = 0
                        for d in dates:
                            cur_sh = ts[d]
                            ch = cur_sh - prev_sh
                            if ch < 0:
                                cum_outflow += -ch
                            if d == target_date:
                                shares_change_today = ch if ch < 0 else 0
                            prev_sh = cur_sh
                        shares_remaining = max(0, baseline_sh - cum_outflow)

            # ---- cicc_chunhua: manual tracking on mixed seat; remaining = baseline, no auto flow-tracking ----
            # For mixed seats where flow cannot be reliably attributed, use static remaining = baseline.
            # Update baseline in DB as confirmed sales are observed.
            elif rule == 'cicc_chunhua':
                baseline_sh = baseline
                shares_remaining = baseline_sh
                shares_change_today = 0

            # ---- all_flow: all net changes (inflow AND outflow) on seat = shareholder change ----
            elif rule == 'all_flow':
                baseline_sh = baseline
                dates = sorted([d for d in ts.keys() if (sell_start or d) and d <= target_date])
                cum_net = 0
                prev_sh = None
                for d in dates:
                    cur_sh = ts[d]
                    if prev_sh is not None:
                        ch = cur_sh - prev_sh
                        cum_net += ch
                        if d == target_date:
                            shares_change_today = ch
                    prev_sh = cur_sh
                shares_remaining = max(0, baseline_sh + cum_net)

            # ---- all_outflow fallback ----
            elif rule == 'all_outflow':
                baseline_sh = baseline
                cum_sell = 0
                dates = sorted([d for d in ts.keys() if sell_start <= d <= target_date]) if sell_start else sorted(ts.keys())
                prev_sh = baseline_sh
                for d in dates:
                    cur_sh = ts[d]
                    ch = cur_sh - prev_sh
                    if ch < 0:
                        cum_sell += -ch
                    if d == target_date:
                        shares_change_today = ch if ch < 0 else 0
                    prev_sh = cur_sh
                shares_remaining = max(0, baseline_sh - cum_sell)

            else:
                # Fallback: static
                shares_remaining = static_shares or baseline
                shares_change_today = 0

            close = _get_close(conn, target_date)
            estimated_proceeds = abs(shares_change_today) * close if close and shares_change_today < 0 else None

            result = {
                'trade_date': target_date,
                'shareholder_name': sh_name,
                'participant_id': pid,
                'participant_name': att['participant_name'],
                'shares_baseline': baseline,
                'shares_change': shares_change_today,
                'shares_remaining': shares_remaining,
                'estimated_proceeds': estimated_proceeds,
                'investor_type': att['investor_type'],
                'cost_hkd': att['cost_hkd'],
                'unlock_date': att['unlock_date'],
                'confidence': att['confidence'],
                'notes': notes,
            }
            results.append(result)

            conn.execute("""
                INSERT OR REPLACE INTO shareholder_selloff
                (trade_date, shareholder_name, shares_change, shares_remaining, estimated_proceeds, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (target_date, sh_name, shares_change_today, shares_remaining, estimated_proceeds, notes))

        logger.info(f"Computed selloff for {len(results)} shareholders in {code} on {target_date}")
        return results


def compute_selloff_range(stock_code: str, days: int = 30):
    """Compute selloff for the last N trading days."""
    from db import get_stock_conn
    code = stock_code.zfill(5)

    with get_stock_conn(code) as conn:
        dates = [r['trade_date'] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM ccass_daily_changes ORDER BY trade_date DESC LIMIT ?", (days,)
        ).fetchall()]

    all_results = []
    for d in reversed(dates):
        results = compute_shareholder_selloff(code, d)
        all_results.extend(results)

    return all_results


def _summary_without_ccass(att) -> dict:
    """Show an uploaded attribution even when this stock has no CCASS seat history yet."""
    baseline = att['shares_baseline'] or 0
    return {
        'shareholder_name': att['shareholder_name'],
        'participant_id': att['participant_id'],
        'participant_name': att['participant_name'],
        'shares_baseline': baseline,
        'shares_current': baseline,
        'change_from_baseline': 0 if baseline else None,
        'change_30d': None,
        'seat_shares_current': None,
        'cost_hkd': att['cost_hkd'],
        'investor_type': att['investor_type'],
        'unlock_date': att['unlock_date'],
        'confidence': att['confidence'],
        'notes': att['notes'],
    }


def get_shareholder_summary(stock_code: str) -> list[dict]:
    """
    Get current shareholder summary with cumulative changes.
    Uses rule-based attribution: remaining = baseline - cumulative recognized selling.
    """
    from db import get_stock_conn
    code = stock_code.zfill(5)

    # Get latest date first, then compute outside any open read transaction to avoid SQLite lock.
    with get_stock_conn(code) as conn:
        latest_date_row = conn.execute("SELECT MAX(trade_date) as d FROM ccass_participants").fetchone()
        latest_date = latest_date_row['d'] if latest_date_row else None
        if not latest_date:
            attributions = conn.execute("SELECT * FROM shareholder_attribution").fetchall()
            return [_summary_without_ccass(a) for a in attributions]

    # Make sure selloff is computed for latest date.
    # This function writes shareholder_selloff, so it must not run while another connection is open.
    # A failure here must not hide attributions that were just uploaded.
    try:
        compute_shareholder_selloff(code, latest_date)
    except Exception:
        logger.exception("Selloff compute failed for %s; returning stored attributions", code)
        with get_stock_conn(code) as conn:
            attributions = conn.execute("SELECT * FROM shareholder_attribution").fetchall()
            return [_summary_without_ccass(a) for a in attributions]

    with get_stock_conn(code) as conn:
        # Build current seat holdings map
        latest_p = conn.execute(
            "SELECT participant_id, participant_name, shares FROM ccass_participants WHERE trade_date = ?",
            (latest_date,)
        ).fetchall()
        pid_map = {r['participant_id']: {'name': r['participant_name'], 'shares': r['shares']} for r in latest_p}

        # Get 30d change per participant (for the per-seat reference column)
        start_date_row = conn.execute(
            "SELECT trade_date FROM ccass_participants WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1 OFFSET 20",
            (latest_date,)
        ).fetchone()
        start_date = start_date_row['trade_date'] if start_date_row else None

        pid_change_30d = {}
        if start_date:
            start_p = conn.execute(
                "SELECT participant_id, shares FROM ccass_participants WHERE trade_date = ?", (start_date,)
            ).fetchall()
            start_map = {r['participant_id']: r['shares'] for r in start_p}
            for pid, cur in pid_map.items():
                start_shares = start_map.get(pid, 0)
                pid_change_30d[pid] = cur['shares'] - start_shares

        # Pull computed selloff remaining for latest date
        selloff_rows = conn.execute(
            "SELECT * FROM shareholder_selloff WHERE trade_date = ?", (latest_date,)
        ).fetchall()
        selloff_map = {r['shareholder_name']: dict(r) for r in selloff_rows}

        attributions = conn.execute("SELECT * FROM shareholder_attribution").fetchall()
        results = []
        for att in attributions:
            sh_name = att['shareholder_name']
            pid = att['participant_id']
            cur = pid_map.get(pid, {'name': att['participant_name'], 'shares': 0})
            sf = selloff_map.get(sh_name, {})

            shares_current = sf.get('shares_remaining', 0)
            baseline = sf.get('shares_baseline', att['shares_baseline'] or 0)
            change_from_baseline = shares_current - baseline if baseline else None

            results.append({
                'shareholder_name': sh_name,
                'participant_id': pid,
                'participant_name': cur['name'] or att['participant_name'],
                'shares_baseline': baseline,
                'shares_current': shares_current,
                'change_from_baseline': change_from_baseline,
                'change_30d': pid_change_30d.get(pid, 0),
                'seat_shares_current': cur['shares'],  # 席位当前总持仓（参考）
                'cost_hkd': att['cost_hkd'],
                'investor_type': att['investor_type'],
                'unlock_date': att['unlock_date'],
                'confidence': att['confidence'],
                'notes': att['notes'],
            })

        return results


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    code = sys.argv[1] if len(sys.argv) > 1 else '00000'
    results = compute_shareholder_selloff(code)
    for r in results:
        print(f"{r['shareholder_name']:<28} baseline={r.get('shares_baseline',0):>12,}  today={r['shares_change']:+,}  remaining={r['shares_remaining']:>12,}")
