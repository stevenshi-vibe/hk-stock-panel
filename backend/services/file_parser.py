"""
File parser for IR onboarding.
Supports Excel (.xlsx), CSV/TSV, PDF for shareholder/broker mapping uploads.
"""
import csv
import io
import os
import logging
import json
import re
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# CCASS participant IDs are one letter plus five digits (e.g. C00019).
_CCASS_ID_RE = re.compile(r'^[A-Z]\d{5}$')

# Short aliases must match the whole header. Otherwise「股东」steals「股东编号」
# and「id」steals「Participant ID」before the more specific column is considered.
_EXACT_ONLY = {
    '股东', '股東', '投资者', '投資者', '姓名', '名称', '名稱', 'name',
    '编号', '編號', '代码', '代碼', 'id',
    '券商', '席位',
    '持股', '数量', '數量',
    '类型', '類型', 'type',
    '成本', '解禁',
}


def parse_number(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val) if isinstance(val, int) or val == int(val) else float(val)
    s = str(val).strip().replace(',', '').replace(' ', '').replace('股', '')
    if not s or s in ('-', 'N/A', 'n/a', '—'):
        return None
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return None


def detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in ('.xlsx', '.xls'):
        return 'excel'
    elif ext == '.csv':
        return 'csv'
    elif ext == '.pdf':
        return 'pdf'
    elif ext in ('.txt', '.tsv'):
        return 'text'
    return 'unknown'


def parse_excel(filepath: str) -> list[dict]:
    """Parse Excel file and return list of row dicts.

    read_only is intentionally off. Some registers (WPS / certain exporters)
    publish a stale worksheet dimension of A1, and read_only mode then yields
    only the first cell — the upload looks successful and zero rows load.
    """
    import openpyxl
    try:
        wb = openpyxl.load_workbook(filepath, read_only=False, data_only=True)
    except Exception as e:
        msg = str(e).lower()
        if 'old .xls' in msg or 'does not support' in msg:
            raise ValueError('不支持旧版 .xls，请另存为 .xlsx 后再上传。Old .xls is not supported; save as .xlsx.') from e
        raise
    all_rows = []
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            all_rows.extend(_rows_from_grid(rows))
    finally:
        wb.close()
    return all_rows


def parse_csv(filepath: str) -> list[dict]:
    """Parse CSV / TSV text. Header may sit below a title block."""
    text = _read_text(filepath)
    first = next((ln for ln in text.splitlines() if ln.strip()), '')
    comma, tab, semi = first.count(','), first.count('\t'), first.count(';')
    if tab >= comma and tab >= semi and tab > 0:
        delimiter = '\t'
    elif semi > comma and semi > 0:
        delimiter = ';'
    else:
        delimiter = ','
    grid = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _rows_from_grid(grid)


def _read_text(filepath: str) -> str:
    last_error = None
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'big5', 'utf-16'):
        try:
            with open(filepath, 'r', encoding=encoding, newline='') as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError('无法识别文件编码，请另存为 UTF-8 或 GBK。Could not detect encoding; save as UTF-8 or GBK.') from last_error


def parse_pdf(filepath: str) -> list[dict]:
    """Parse PDF tables using pdfplumber."""
    import pdfplumber
    all_rows = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    all_rows.extend(_rows_from_grid(table))
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
    return all_rows


_HEADER_KEYWORDS = (
    '股东', '股東', 'shareholder', '姓名', '持有人', 'holder', '客户', '客戶',
    '账户', '帳戶', '帐户', '投资者', '投資者', 'investor',
    '券商', '席位', 'participant', 'broker', '经纪', '經紀', '托管', '託管',
    '持股', '股份', 'shares', 'holding',
    '解禁', 'unlock', '编号', '編號', '名称', '名稱', 'name',
)


def _find_header_row(rows: list) -> Optional[int]:
    """Pick the header row.

    Real registers put the company name, a "股东名册" title and an as-of date
    above the columns. Scanning only the first hit in the first five rows treated
    that title as the header whenever it contained two keywords, and every data
    row was then discarded.
    """
    best_idx = None
    best_key = (0, 0)  # (keyword score, number of filled cells)
    for i, row in enumerate(rows[:40]):
        if not row:
            continue
        cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
        # A title merged across the sheet is one cell; real headers have several.
        if len(cells) < 2:
            continue
        text = ' '.join(cells).lower()
        score = sum(1 for kw in _HEADER_KEYWORDS if kw in text)
        key = (score, len(cells))
        if key > best_key:
            best_key = key
            best_idx = i
    if best_idx is not None and best_key[0] >= 2:
        return best_idx
    return None


def _rows_from_grid(rows: list) -> list[dict]:
    if not rows:
        return []
    header_idx = _find_header_row(rows)
    if header_idx is None:
        return []
    raw_headers = [str(h).strip() if h else '' for h in rows[header_idx]]
    counts: dict[str, int] = {}
    headers = []
    for i, base in enumerate(raw_headers):
        name = base or f'col_{i}'
        seen = counts.get(name, 0)
        counts[name] = seen + 1
        headers.append(name if seen == 0 else f'{name}_{seen + 1}')
    out = []
    for row in rows[header_idx + 1:]:
        if not row or not any(c is not None and str(c).strip() for c in row):
            continue
        record = {}
        for i, h in enumerate(headers):
            if h.startswith('col_') or i >= len(row):
                continue
            value = row[i]
            if value is None or str(value).strip() == '':
                continue
            record[h] = value
        if record:
            out.append(record)
    return out


# 股东/券商字段映射。简体、繁体和券商账户表的常见列名都要能对上。
FIELD_ALIASES = {
    'shareholder_name': [
        '股东名称', '股東名稱', '股东姓名', '股東姓名', '股东名', '股東名',
        '投资者名称', '投資者名稱', '客户姓名', '客戶姓名', '客户名称', '客戶名稱',
        '持有人名称', '持有人名稱', '持有人', '账户名称', '帳戶名稱', '帐户名称',
        '登记股东', '登記股東', '受益人', 'shareholdername', 'shareholder',
        'investorname', 'investor', 'holder', 'beneficiary',
        '股东', '股東', '投资者', '投資者', '姓名', '名称', '名稱', 'name',
    ],
    'participant_id': [
        '参与者编号', '參與者編號', '参与者代码', '參與者代碼',
        '券商编号', '券商編號', '券商代码', '券商代碼',
        'ccass编号', 'ccass編號', 'ccass号码', 'ccass號碼', 'ccassid',
        '席位号', '席位號', '席位编号', '席位編號',
        'participantid', 'brokerid', 'pid',
        '编号', '編號', '代码', '代碼', 'id',
    ],
    'participant_name': [
        '参与者名称', '參與者名稱', '券商名称', '券商名稱',
        '席位名称', '席位名稱', '开户券商', '開戶券商',
        '开户银行', '開戶銀行', '开户行', '開戶行',
        '托管券商', '託管券商', '托管商', '託管商', '托管人', '託管人', '托管银行', '託管銀行',
        '经纪商', '經紀商', 'participantname', 'brokername', 'custodian',
        '经纪', '經紀', '券商', '席位', 'participant', 'broker',
    ],
    'shares': [
        '持股数量', '持股數量', '持股数目', '持股數目',
        '股份数量', '股份數量', '股份数目', '股份數目',
        '持有股数', '持有股數', '持有数量', '持有數量',
        '证券数量', '證券數量', '持股数', '持股數', '持股量',
        '股数', '股數', 'numberofshares', 'sharesheld', 'shareholding',
        '持股', '数量', '數量', 'shares', 'holding',
    ],
    'investor_type': [
        '投资者类型', '投資者類型', '股东类型', '股東類型', '股份类型', '股份類型',
        'investortype', 'shareholdertype', '类型', '類型', 'category', 'type',
    ],
    'cost_hkd': [
        '投资成本', '投資成本', '入股价', '入股價', '发行价', '發行價',
        '成本价', '成本價', 'ipoprice', 'price', 'cost', '成本',
    ],
    'unlock_date': [
        '解禁日期', '解禁时间', '解禁時間', '解锁日', '解鎖日',
        '锁定期', '鎖定期', 'lockupexpiry', 'lockup',
        '解禁日', 'unlock', '解禁',
    ],
}


def _norm_header(value) -> str:
    text = str(value).strip().lower()
    return re.sub(r'[\s_\-—/\\()（）\[\]【】:：.,，。·]+', '', text)


def map_fields(record: dict) -> dict:
    """Map column aliases to standard field names.

    Each source column is used at most once. The longest / exact alias wins,
    so「股东编号」不再盖住「股东名称」，"Participant ID" 不再被当成券商名称。
    """
    items = []
    for key, value in record.items():
        if key is None or value is None or str(value).strip() == '':
            continue
        items.append((key, value, _norm_header(key)))

    candidates = []
    for std_field, aliases in FIELD_ALIASES.items():
        for key, value, key_norm in items:
            if not key_norm:
                continue
            best = 0
            for alias in aliases:
                alias_norm = _norm_header(alias)
                if not alias_norm:
                    continue
                exact_only = alias_norm in _EXACT_ONLY or alias in _EXACT_ONLY
                if key_norm == alias_norm:
                    best = max(best, 1000 + len(alias_norm))
                elif not exact_only and alias_norm in key_norm:
                    best = max(best, len(alias_norm))
            if best:
                candidates.append((best, len(key_norm), std_field, key, value))

    candidates.sort(key=lambda item: (-item[0], -item[1]))
    mapped = {}
    used_keys = set()
    for _score, _key_len, std_field, key, value in candidates:
        if std_field in mapped or key in used_keys:
            continue
        mapped[std_field] = value
        used_keys.add(key)
    return mapped


def _clean_participant_id(value) -> str:
    if value is None:
        return ''
    text = str(value).strip().upper().replace(' ', '')
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text


def _clean_date(value) -> Optional[str]:
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text[:10], fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return text[:10]


def parse_uploaded_file(filepath: str, original_filename: str) -> dict:
    """
    Parse an uploaded file and extract shareholder/broker mapping data.
    Returns: {
        'file_type': str,
        'rows_raw': list[dict],    # raw rows
        'attributions': list[dict], # mapped shareholder attributions
        'unlocks': list[dict],      # unlock calendar entries
        'summary': dict,            # parsing summary
    }
    """
    ftype = detect_file_type(original_filename)

    try:
        if ftype == 'excel':
            raw_rows = parse_excel(filepath)
        elif ftype in ('csv', 'text'):
            raw_rows = parse_csv(filepath)
        elif ftype == 'pdf':
            raw_rows = parse_pdf(filepath)
        else:
            return {
                'file_type': ftype,
                'error': '不支持的文件类型，请上传 .xlsx、.csv 或 .pdf。Unsupported file type; upload .xlsx, .csv, or .pdf.',
                'rows_raw': [], 'attributions': [], 'unlocks': [], 'summary': {},
            }
    except ValueError as e:
        return {
            'file_type': ftype, 'error': str(e),
            'rows_raw': [], 'attributions': [], 'unlocks': [], 'summary': {},
        }

    attributions = []
    unlocks = []

    for row in raw_rows:
        mapped = map_fields(row)
        if not (mapped.get('shareholder_name') or mapped.get('participant_name') or mapped.get('participant_id')):
            continue
        attr = {
            'shareholder_name': str(mapped.get('shareholder_name', '')).strip(),
            'participant_id': _clean_participant_id(mapped.get('participant_id')),
            'participant_name': str(mapped.get('participant_name', '')).strip(),
            'shares_baseline': parse_number(mapped.get('shares')),
            'investor_type': str(mapped.get('investor_type', '')).strip() if mapped.get('investor_type') else '',
            'cost_hkd': parse_number(mapped.get('cost_hkd')),
            'unlock_date': _clean_date(mapped.get('unlock_date')),
        }
        # 列名是「席位」而单元格里其实是 C00019 这类参与者编号。
        if not attr['participant_id'] and _CCASS_ID_RE.match(attr['participant_name'].replace(' ', '').upper()):
            attr['participant_id'] = attr['participant_name'].replace(' ', '').upper()
            attr['participant_name'] = ''
        if not attr['shareholder_name']:
            attr['shareholder_name'] = attr['participant_name']
        if attr['shareholder_name']:
            attributions.append(attr)

            # Check for unlock date
            if attr.get('unlock_date') and attr.get('shares_baseline'):
                unlocks.append({
                    'unlock_date': attr['unlock_date'],
                    'shareholder_name': attr['shareholder_name'],
                    'shares': attr['shares_baseline'],
                    'cost_hkd': attr['cost_hkd'],
                    'broker': attr['participant_name'],
                    'investor_type': attr['investor_type'],
                })

    summary = {
        'total_rows': len(raw_rows),
        'attributions_found': len(attributions),
        'unlocks_found': len(unlocks),
        'unique_shareholders': len(set(a['shareholder_name'] for a in attributions)),
        'unique_seats': len(set(a['participant_id'] for a in attributions if a['participant_id'])),
    }

    logger.info(f"Parsed {original_filename}: {summary}")
    error = None
    if not attributions:
        cols = []
        for row in raw_rows[:3]:
            for key in row.keys():
                label = str(key).strip()
                if label and not label.startswith('col_') and label not in cols:
                    cols.append(label)
        shown = '、'.join(cols[:8]) if cols else '未找到表头'
        error = (
            f'未识别到股东记录（读到的列：{shown}）。'
            '请包含「股东名称」或「姓名」（繁体可用「股東名稱」）以及「持股数量」。'
            'No shareholder rows recognized; include a name column and a shares column.'
        )
    return {
        'file_type': ftype,
        'rows_raw': raw_rows,
        'attributions': attributions,
        'unlocks': unlocks,
        'summary': summary,
        'error': error,
    }


def _is_protected(row) -> bool:
    """A row the user typed or later edited must not be replaced by a new file."""
    if row is None:
        return False
    keys = row.keys()
    edited = int(row['manually_edited'] or 0) if 'manually_edited' in keys else 0
    origin = (row['attribution_type'] or '') if 'attribution_type' in keys else ''
    return bool(edited) or origin == 'manual'


def _as_shares(value) -> int:
    if value is None or value == '':
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number < 0:
        return 0
    return int(number)


def apply_attributions(
    stock_code: str,
    attributions: list[dict],
    unlocks: list[dict],
    overwrite_manual: bool = False,
) -> dict:
    """Save parsed attributions.

    File columns (name, seat, shares, type, cost, unlock date) update an
    existing upload row in place, so notes and selloff rules stay put.
    Rows the user edited in the panel are left unchanged unless
    overwrite_manual is set. Even then, notes are kept — the file has no
    notes column. Returns the rows applied and the rows skipped.
    """
    from db import get_stock_conn
    from services.shareholder_store import lookup_issued_shares

    code = stock_code.zfill(5)
    issued = lookup_issued_shares(code)
    applied = []
    skipped = []
    with get_stock_conn(code) as conn:
        for raw in attributions:
            name = str(raw.get('shareholder_name') or '').strip()
            pid = str(raw.get('participant_id') or '').strip().upper()
            if not name:
                continue
            existing = conn.execute(
                "SELECT * FROM shareholder_attribution WHERE shareholder_name = ? AND participant_id = ?",
                (name, pid),
            ).fetchone()
            if _is_protected(existing) and not overwrite_manual:
                skipped.append({
                    'id': existing['id'],
                    'shareholder_name': name,
                    'participant_id': pid,
                    'reason': 'manually_edited',
                })
                continue
            shares = _as_shares(raw.get('shares_baseline'))
            pct = round(shares / issued * 100.0, 4) if issued and shares else None
            values = (
                raw.get('participant_name') or '',
                shares,
                pct,
                raw.get('investor_type') or '',
                raw.get('cost_hkd'),
                raw.get('unlock_date'),
            )
            if existing:
                conn.execute(
                    """
                    UPDATE shareholder_attribution SET
                        participant_name = ?, shares_baseline = ?, holding_pct = ?,
                        investor_type = ?, cost_hkd = ?, unlock_date = ?,
                        attribution_type = 'upload', manually_edited = 0,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (*values, existing['id']),
                )
                applied.append({
                    'id': existing['id'], 'action': 'updated',
                    'shareholder_name': name, 'participant_id': pid,
                })
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO shareholder_attribution
                        (shareholder_name, participant_id, participant_name, shares_baseline,
                         holding_pct, investor_type, cost_hkd, unlock_date,
                         attribution_type, confidence, manually_edited, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'upload', 'high', 0, datetime('now'))
                    """,
                    (name, pid, *values),
                )
                applied.append({
                    'id': cursor.lastrowid, 'action': 'inserted',
                    'shareholder_name': name, 'participant_id': pid,
                })

        skipped_names = {row['shareholder_name'] for row in skipped}
        applied_names = {row['shareholder_name'] for row in applied}
        for item in unlocks:
            holder = item.get('shareholder_name', '')
            if holder in skipped_names and holder not in applied_names:
                continue
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
                (
                    item.get('unlock_date'),
                    holder,
                    item.get('shares', 0),
                    item.get('cost_hkd'),
                    item.get('broker', ''),
                    item.get('investor_type', ''),
                ),
            )
    return {
        'applied': applied,
        'skipped': skipped,
        'applied_count': len(applied),
        'skipped_count': len(skipped),
    }


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage: python file_parser.py <filepath>")
        sys.exit(1)
    result = parse_uploaded_file(sys.argv[1], os.path.basename(sys.argv[1]))
    print(json.dumps(result['summary'], indent=2, ensure_ascii=False))
    print(f"\nAttributions: {len(result['attributions'])}")
    for a in result['attributions'][:5]:
        print(f"  {a}")
