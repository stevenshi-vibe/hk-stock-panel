"""Create, update, and delete shareholder rows, and keep derived figures in sync."""
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_PID_RE = re.compile(r'^[A-Z]\d{5}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

EDITABLE_FIELDS = (
    'shareholder_name', 'participant_id', 'participant_name',
    'shares_baseline', 'holding_pct', 'investor_type',
    'cost_hkd', 'unlock_date', 'notes',
)


class AttributionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def issued_shares_from_conn(conn) -> int:
    """Issued shares implied by the latest CCASS total, else the stored stock info."""
    row = conn.execute(
        """
        SELECT total_shares, pct_of_issued FROM ccass_total
        WHERE pct_of_issued > 0 AND total_shares > 0
        ORDER BY trade_date DESC LIMIT 1
        """
    ).fetchone()
    if row and row['pct_of_issued']:
        return int(round(row['total_shares'] / (row['pct_of_issued'] / 100.0)))
    info = conn.execute("SELECT value FROM stock_info WHERE key = 'issued_shares'").fetchone()
    if info and info['value']:
        try:
            return int(float(str(info['value']).replace(',', '')))
        except ValueError:
            return 0
    return 0


def lookup_issued_shares(code: str) -> int:
    """Best issued-share count for percentage math. Zero when nothing is known."""
    from db import get_meta_conn, get_stock_conn
    code = code.zfill(5)
    with get_stock_conn(code) as conn:
        found = issued_shares_from_conn(conn)
    if found:
        return found
    try:
        with get_meta_conn() as conn:
            row = conn.execute(
                "SELECT issued_shares FROM stocks WHERE stock_code = ?", (code,)
            ).fetchone()
            if row and row['issued_shares']:
                return int(row['issued_shares'])
    except Exception:
        logger.exception("issued share lookup failed for %s", code)
    return 0


def _clean_text(value, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _parse_date(value):
    if value is None or str(value).strip() == '':
        return None
    text = str(value).strip()[:10]
    if not _DATE_RE.match(text):
        raise AttributionError(422, '日期须为 YYYY-MM-DD。Date must be YYYY-MM-DD.')
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        raise AttributionError(422, '日期无效。Invalid date.')
    return text


def _parse_shares(value):
    if value is None or value == '':
        return 0
    if isinstance(value, bool):
        raise AttributionError(422, '持股数量须为非负整数。Shares must be a non-negative integer.')
    if isinstance(value, str):
        value = value.replace(',', '').strip()
        if value == '':
            return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AttributionError(422, '持股数量须为非负整数。Shares must be a non-negative integer.')
    if number < 0 or number != int(number):
        raise AttributionError(422, '持股数量须为非负整数。Shares must be a non-negative integer.')
    return int(number)


def _parse_pct(value):
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AttributionError(422, '持股比例须为 0 到 100 之间的数字。Percentage must be between 0 and 100.')
    if number < 0 or number > 100:
        raise AttributionError(422, '持股比例须为 0 到 100 之间的数字。Percentage must be between 0 and 100.')
    return round(number, 4)


def _parse_cost(value):
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AttributionError(422, '成本须为非负数字。Cost must be a non-negative number.')
    if number < 0:
        raise AttributionError(422, '成本须为非负数字。Cost must be a non-negative number.')
    return number


def normalize_payload(data: dict, *, partial: bool) -> dict:
    if not isinstance(data, dict):
        raise AttributionError(422, '请求体须为 JSON 对象。Body must be a JSON object.')
    present = {key: data[key] for key in EDITABLE_FIELDS if key in data}
    if partial and not present:
        raise AttributionError(422, '没有可更新的字段。No editable fields were sent.')

    out = {}
    if not partial or 'shareholder_name' in present:
        name = _clean_text(present.get('shareholder_name'), 200)
        if not name:
            raise AttributionError(422, '股东名称不能为空。Shareholder name is required.')
        out['shareholder_name'] = name
    if not partial or 'participant_id' in present:
        pid = _clean_text(present.get('participant_id'), 16).upper().replace(' ', '')
        if pid.endswith('.0') and pid[:-2].isdigit():
            pid = pid[:-2]
        if not _PID_RE.match(pid):
            raise AttributionError(
                422,
                '席位号须为 1 位字母加 5 位数字，例如 C00019。Participant ID must look like C00019.',
            )
        out['participant_id'] = pid
    if not partial or 'participant_name' in present:
        out['participant_name'] = _clean_text(present.get('participant_name'), 200)
    if not partial or 'investor_type' in present:
        out['investor_type'] = _clean_text(present.get('investor_type'), 100)
    if not partial or 'notes' in present:
        out['notes'] = _clean_text(present.get('notes'), 2000)
    if not partial or 'shares_baseline' in present:
        out['shares_baseline'] = _parse_shares(present.get('shares_baseline'))
    if not partial or 'holding_pct' in present:
        out['holding_pct'] = _parse_pct(present.get('holding_pct'))
    if not partial or 'cost_hkd' in present:
        out['cost_hkd'] = _parse_cost(present.get('cost_hkd'))
    if not partial or 'unlock_date' in present:
        out['unlock_date'] = _parse_date(present.get('unlock_date'))
    return out


def align_shares_and_pct(fields: dict, issued: int, sent: dict) -> dict:
    """Keep the stored percentage and the share count on the same issued base.

    When both are sent, the share count wins and the percentage is recomputed.
    A request that only sends the percentage back-calculates the share count.
    """
    has_shares = 'shares_baseline' in sent
    has_pct = 'holding_pct' in sent
    if issued and has_pct and not has_shares and fields.get('holding_pct') is not None:
        fields['shares_baseline'] = int(round(issued * fields['holding_pct'] / 100.0))
    if issued and 'shares_baseline' in fields:
        shares = fields.get('shares_baseline') or 0
        fields['holding_pct'] = round(shares / issued * 100.0, 4) if shares else 0
    return fields


def _sent(data: dict) -> dict:
    return {key: data[key] for key in EDITABLE_FIELDS if key in data}


def _conflict(conn, name: str, pid: str, exclude_id: int | None = None):
    row = conn.execute(
        "SELECT id FROM shareholder_attribution WHERE shareholder_name = ? AND participant_id = ?",
        (name, pid),
    ).fetchone()
    if row and row['id'] != exclude_id:
        return row
    return None


def _sync_unlock(conn, previous, name, unlock_date, shares, cost, broker, investor_type):
    old_date = previous['unlock_date'] if previous else None
    old_name = previous['shareholder_name'] if previous else None
    own_id = previous['id'] if previous else -1
    if old_date and (old_date != unlock_date or old_name != name):
        still = conn.execute(
            """
            SELECT COUNT(*) AS c FROM shareholder_attribution
            WHERE shareholder_name = ? AND unlock_date = ? AND id != ?
            """,
            (old_name, old_date, own_id),
        ).fetchone()['c']
        if still == 0:
            conn.execute(
                "DELETE FROM unlock_calendar WHERE shareholder_name = ? AND unlock_date = ?",
                (old_name, old_date),
            )
    if not unlock_date:
        return
    conn.execute(
        """
        INSERT INTO unlock_calendar
            (unlock_date, shareholder_name, shares, cost_hkd, broker, investor_type, unlock_status)
        VALUES (?, ?, ?, ?, ?, ?, 'upcoming')
        ON CONFLICT(unlock_date, shareholder_name) DO UPDATE SET
            shares = excluded.shares,
            cost_hkd = excluded.cost_hkd,
            broker = excluded.broker,
            investor_type = excluded.investor_type
        """,
        (unlock_date, name, shares or 0, cost, broker or '', investor_type or ''),
    )


def _set_ir_flag(code: str, on: bool):
    from db import ensure_stock, get_meta_conn
    ensure_stock(code)
    with get_meta_conn() as conn:
        conn.execute(
            "UPDATE stocks SET is_ir_configured = ? WHERE stock_code = ?",
            (1 if on else 0, code.zfill(5)),
        )


def refresh_derived(code: str):
    """Recompute selloff rows so charts and the summary pick up the edit."""
    from services.selloff_engine import compute_selloff_range
    try:
        compute_selloff_range(code)
    except Exception:
        logger.exception("Selloff recompute failed for %s", code)


def _load_row(code: str, att_id: int) -> dict:
    from services.selloff_engine import get_shareholder_summary
    for row in get_shareholder_summary(code):
        if row.get('id') == att_id:
            return row
    raise AttributionError(404, '股东记录不存在。Shareholder record was not found.')


def create_attribution(code: str, data: dict) -> dict:
    from db import get_stock_conn, init_stock_db
    code = code.zfill(5)
    init_stock_db(code)
    fields = normalize_payload(data, partial=False)
    issued = lookup_issued_shares(code)
    align_shares_and_pct(fields, issued, _sent(data))
    with get_stock_conn(code) as conn:
        if _conflict(conn, fields['shareholder_name'], fields['participant_id']):
            raise AttributionError(
                409,
                '该股东在这个席位上已有记录，请直接编辑。This shareholder already has a row on that seat.',
            )
        cursor = conn.execute(
            """
            INSERT INTO shareholder_attribution
                (shareholder_name, participant_id, participant_name, shares_baseline, holding_pct,
                 investor_type, cost_hkd, unlock_date, notes, attribution_type, confidence,
                 manually_edited, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 'high', 1, datetime('now'))
            """,
            (
                fields['shareholder_name'], fields['participant_id'], fields.get('participant_name', ''),
                fields.get('shares_baseline', 0), fields.get('holding_pct'),
                fields.get('investor_type', ''), fields.get('cost_hkd'), fields.get('unlock_date'),
                fields.get('notes', ''),
            ),
        )
        att_id = cursor.lastrowid
        _sync_unlock(
            conn, None, fields['shareholder_name'], fields.get('unlock_date'),
            fields.get('shares_baseline', 0), fields.get('cost_hkd'),
            fields.get('participant_name', ''), fields.get('investor_type', ''),
        )
    _set_ir_flag(code, True)
    refresh_derived(code)
    return _load_row(code, att_id)


def update_attribution(code: str, att_id: int, data: dict, *, partial: bool) -> dict:
    from db import get_stock_conn, init_stock_db
    code = code.zfill(5)
    init_stock_db(code)
    fields = normalize_payload(data, partial=partial)
    issued = lookup_issued_shares(code)
    align_shares_and_pct(fields, issued, _sent(data))
    with get_stock_conn(code) as conn:
        current = conn.execute(
            "SELECT * FROM shareholder_attribution WHERE id = ?", (att_id,)
        ).fetchone()
        if not current:
            raise AttributionError(404, '股东记录不存在。Shareholder record was not found.')
        name = fields.get('shareholder_name', current['shareholder_name'])
        pid = fields.get('participant_id', current['participant_id'])
        if _conflict(conn, name, pid, exclude_id=att_id):
            raise AttributionError(
                409,
                '该股东在这个席位上已有另一条记录。Another row already uses this shareholder and seat.',
            )
        assignments = [f"{key} = ?" for key in fields]
        values = list(fields.values())
        assignments.append("manually_edited = 1")
        assignments.append("updated_at = datetime('now')")
        values.append(att_id)
        conn.execute(
            f"UPDATE shareholder_attribution SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        updated = conn.execute(
            "SELECT * FROM shareholder_attribution WHERE id = ?", (att_id,)
        ).fetchone()
        _sync_unlock(
            conn, current, updated['shareholder_name'], updated['unlock_date'],
            updated['shares_baseline'], updated['cost_hkd'],
            updated['participant_name'], updated['investor_type'],
        )
    refresh_derived(code)
    return _load_row(code, att_id)


def delete_attribution(code: str, att_id: int) -> dict:
    from db import get_stock_conn, init_stock_db
    code = code.zfill(5)
    init_stock_db(code)
    with get_stock_conn(code) as conn:
        current = conn.execute(
            "SELECT * FROM shareholder_attribution WHERE id = ?", (att_id,)
        ).fetchone()
        if not current:
            raise AttributionError(404, '股东记录不存在。Shareholder record was not found.')
        name = current['shareholder_name']
        conn.execute("DELETE FROM shareholder_attribution WHERE id = ?", (att_id,))
        left = conn.execute(
            "SELECT COUNT(*) AS c FROM shareholder_attribution WHERE shareholder_name = ?",
            (name,),
        ).fetchone()['c']
        if left == 0:
            conn.execute("DELETE FROM shareholder_selloff WHERE shareholder_name = ?", (name,))
            conn.execute("DELETE FROM unlock_calendar WHERE shareholder_name = ?", (name,))
        elif current['unlock_date']:
            still = conn.execute(
                """
                SELECT COUNT(*) AS c FROM shareholder_attribution
                WHERE shareholder_name = ? AND unlock_date = ?
                """,
                (name, current['unlock_date']),
            ).fetchone()['c']
            if still == 0:
                conn.execute(
                    "DELETE FROM unlock_calendar WHERE shareholder_name = ? AND unlock_date = ?",
                    (name, current['unlock_date']),
                )
        remaining = conn.execute("SELECT COUNT(*) AS c FROM shareholder_attribution").fetchone()['c']
    _set_ir_flag(code, remaining > 0)
    refresh_derived(code)
    return {'status': 'ok', 'deleted_id': att_id}
