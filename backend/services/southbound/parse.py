"""把各数据源的原始文本/表格解析成统一结构。解析本身不做渠道判断。"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .align import parse_iso_date, report_period_end
from .models import CONFIRMED, DailyPoint, Holding, SeatPoint
from .units import assess_unit, parse_number, shares_to_count

_SHARE_TOKEN = re.compile(r"([\d,]+)\(([LSPlsp])\)")


def normalize_code(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    if not digits:
        return ""
    return digits.zfill(5)[-5:]


def holder_key_fund(fund_code: str) -> str:
    return f"fund:{str(fund_code).strip()}"


def holder_key_disclosure(name: str) -> str:
    folded = re.sub(r"\s+", " ", (name or "").strip().lower())
    return "di:" + folded


def parse_position_tokens(text: str) -> list[dict]:
    """从 '326,833,563(L) 5.84 642,000(S) 0.01' 拆出好仓/淡仓。

    两个仓位之间若是纯小数，视为百分比；下一段带逗号的股数不会被当成百分比。
    """
    raw = text or ""
    matches = list(_SHARE_TOKEN.finditer(raw))
    found = []
    for index, match in enumerate(matches):
        shares = parse_number(match.group(1))
        if shares is None:
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        between = raw[match.end():next_start].strip()
        pct = float(between) if re.fullmatch(r"\d+\.\d+", between) else None
        found.append({
            "position": match.group(2).upper(),
            "shares": int(shares),
            "pct": pct,
        })
    return found


def _finalize_known(holding: Holding, channel_tag: str, confidence: float, note: str) -> Holding:
    status = assess_unit(holding.raw_shares, holding.raw_unit, holding.market_value_wan)
    holding.unit_status = status
    holding.channel_tag = channel_tag
    holding.notes = note if not holding.notes else f"{note}；{holding.notes}"
    if status == "mismatch":
        confidence = min(confidence, 0.3)
        holding.notes += "；持股单位与市值隐含价格不一致，已从南下解释中剔除"
    elif status == "unchecked" and holding.holder_type == "public_fund":
        confidence = max(0.0, confidence - 0.05)
        holding.notes += "；没有市值，未能交叉验证万股换算"
    holding.confidence = round(confidence, 2)
    holding.counts_toward_southbound = (
        channel_tag == CONFIRMED
        and holding.position_type == "L"
        and status in {"ok", "unchecked"}
        and holding.shares > 0
    )
    return holding


def make_fund_holding(
    *,
    fund_code: str,
    fund_name: str,
    fund_type: str,
    stock_code: str,
    stock_name: str,
    report_label: str,
    report_date: str,
    raw_shares: float,
    pct_nav: float | None,
    market_value_wan: float | None,
    report_scope: str,
    source: str,
    provider_id: str,
    source_ref: str = "",
) -> Holding | None:
    from .classify import classify_fund

    if normalize_code(stock_code) == "" or raw_shares is None or raw_shares <= 0 or not report_date:
        return None
    tag, confidence, note = classify_fund(fund_name, fund_type)
    shares = shares_to_count(raw_shares, "wan_shares")
    holding = Holding(
        holder_key=holder_key_fund(fund_code),
        holder_name=fund_name,
        holder_type="public_fund",
        channel_tag=tag,
        report_date=report_date,
        shares=shares,
        raw_shares=raw_shares,
        raw_unit="wan_shares",
        source=source,
        provider_id=provider_id,
        confidence=confidence,
        counts_toward_southbound=False,
        report_label=report_label,
        pct_nav=pct_nav,
        market_value_wan=market_value_wan,
        source_ref=source_ref or fund_code,
        report_scope=report_scope,
        fund_type=fund_type or "",
        notes=f"标的 {normalize_code(stock_code)} {stock_name}".strip(),
    )
    return _finalize_known(holding, tag, confidence, note)


def make_disclosure_holding(
    *,
    name: str,
    report_date: str,
    shares: int,
    position: str,
    pct: float | None,
    source: str,
    provider_id: str,
    source_ref: str,
    report_label: str = "",
    notes: str = "",
) -> Holding | None:
    from .classify import classify_disclosure

    if not name or not report_date or shares is None:
        return None
    tag, confidence, note = classify_disclosure(name)
    holding = Holding(
        holder_key=holder_key_disclosure(name),
        holder_name=name.strip(),
        holder_type="disclosure",
        channel_tag=tag,
        report_date=report_date,
        shares=int(shares),
        raw_shares=float(shares),
        raw_unit="shares",
        source=source,
        provider_id=provider_id,
        confidence=confidence,
        counts_toward_southbound=False,
        report_label=report_label,
        pct_issued=pct,
        position_type=position,
        source_ref=source_ref,
        report_scope="disclosure",
        notes=notes,
    )
    return _finalize_known(holding, tag, confidence, note)


def _scope_for_quarter(label: str, row_count: int, truncated: bool) -> str:
    quarter = None
    match = re.search(r"([1-4])\s*季度", label or "")
    if match:
        quarter = int(match.group(1))
    if quarter in (1, 3):
        return "top10"
    if truncated:
        return "truncated"
    if row_count > 10:
        return "extended"
    return "unknown"


def unwrap_fund_archive(text: str) -> str:
    if "content:\"" not in text:
        return text
    start = text.find('content:"')
    end = text.rfind('",arryear:')
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start + len('content:"'):end]


def parse_eastmoney_fund_html(
    text: str,
    *,
    fund_code: str,
    fund_name: str,
    fund_type: str = "",
    stock_code: str | None = None,
    source: str = "天天基金 FundArchivesDatas",
    provider_id: str = "eastmoney_fund",
) -> list[Holding]:
    html = unwrap_fund_archive(text)
    soup = BeautifulSoup(html, "lxml")
    want = normalize_code(stock_code) if stock_code else ""
    holdings: list[Holding] = []
    boxes = soup.select("div.box") or [soup]
    for box in boxes:
        heading = box.find("h4")
        label = heading.get_text(" ", strip=True) if heading else ""
        as_of_node = box.select_one("font.px12")
        as_of = as_of_node.get_text(strip=True) if as_of_node else ""
        report_date = report_period_end(label, as_of)
        table = box.find("table")
        if table is None or not report_date:
            continue
        header_cells = table.find_all("th")
        headers = [re.sub(r"\s+", "", cell.get_text(" ", strip=True)) for cell in header_cells]
        index = {}
        for i, header in enumerate(headers):
            if "股票代码" in header and "code" not in index:
                index["code"] = i
            elif "股票名称" in header and "name" not in index:
                index["name"] = i
            elif "占净值" in header:
                index["pct"] = i
            elif "持股数" in header:
                index["shares"] = i
            elif "持仓市值" in header:
                index["mv"] = i
        if "code" not in index or "shares" not in index:
            continue
        body_rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]
        truncated = "LoadMore" in str(box) or "显示全部" in box.get_text()
        scope = _scope_for_quarter(label, len(body_rows), truncated)
        for row in body_rows:
            cells = row.find_all("td")
            if len(cells) <= max(index.values()):
                continue
            code = normalize_code(cells[index["code"]].get_text(" ", strip=True))
            if want and code != want:
                continue
            raw = parse_number(cells[index["shares"]].get_text(" ", strip=True))
            if raw is None:
                continue
            pct = parse_number(cells[index["pct"]].get_text(" ", strip=True)) if "pct" in index else None
            mv = parse_number(cells[index["mv"]].get_text(" ", strip=True)) if "mv" in index else None
            stock_name = cells[index["name"]].get_text(" ", strip=True) if "name" in index else ""
            holding = make_fund_holding(
                fund_code=fund_code,
                fund_name=fund_name or fund_code,
                fund_type=fund_type,
                stock_code=code,
                stock_name=stock_name,
                report_label=label,
                report_date=report_date,
                raw_shares=raw,
                pct_nav=pct,
                market_value_wan=mv,
                report_scope=scope,
                source=source,
                provider_id=provider_id,
                source_ref=f"{fund_code}:{report_date}",
            )
            if holding:
                holdings.append(holding)
    return holdings


def _csv_rows(path: str | Path) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def parse_fund_hits_csv(path: str | Path, catalog: dict[str, dict] | None = None) -> list[Holding]:
    """附件 parsed/fund_holdings_hits_*.csv。持股数是万股，文件头没有写单位。"""
    catalog = catalog or {}
    holdings = []
    for row in _csv_rows(path):
        code = (row.get("基金代码") or "").strip()
        meta = catalog.get(code, {})
        report_date = report_period_end(row.get("季度"))
        raw = parse_number(row.get("持股数"))
        if raw is None or not report_date:
            continue
        holding = make_fund_holding(
            fund_code=code,
            fund_name=(row.get("基金名称") or meta.get("fund_name") or code).strip(),
            fund_type=meta.get("fund_type") or "",
            stock_code=row.get("股票代码") or "",
            stock_name=row.get("股票名称") or "",
            report_label=row.get("季度") or "",
            report_date=report_date,
            raw_shares=raw,
            pct_nav=parse_number(row.get("占净值比例")),
            market_value_wan=parse_number(row.get("持仓市值")),
            report_scope="unknown",
            source="天天基金/AKShare fund_portfolio_hold_em（附件实测导出）",
            provider_id="fixture_fund_hits",
            source_ref=f"{code}:{report_date}",
        )
        if holding:
            holdings.append(holding)
    return holdings


def load_focus_catalog(path: str | Path) -> dict[str, dict]:
    catalog = {}
    for row in _csv_rows(path):
        code = (row.get("fund_code") or "").strip()
        if code:
            catalog[code] = {
                "fund_name": row.get("fund_name") or "",
                "fund_type": row.get("fund_type") or "",
                "group": row.get("group") or "",
            }
    return catalog


def parse_akshare_daily_csv(path: str | Path, provider_id: str = "fixture_eastmoney_daily") -> list[DailyPoint]:
    points = []
    for row in _csv_rows(path):
        trade_date = parse_iso_date(row.get("持股日期"))
        shares = parse_number(row.get("持股数量"))
        if not trade_date or shares is None:
            continue
        points.append(DailyPoint(
            trade_date=trade_date,
            shares=int(shares),
            # 上游列名写成「占A股百分比」，港股实测与港交所「占已发行股份」一致。
            pct_of_issued=parse_number(row.get("持股数量占A股百分比")),
            hold_value=parse_number(row.get("持股市值")),
            close_price=parse_number(row.get("当日收盘价")),
            change_pct=parse_number(row.get("当日涨跌幅")),
            source="东方财富 RPT_MUTUAL_STOCK_HOLDRANKS（附件 AKShare 导出）",
            provider_id=provider_id,
        ))
    points.sort(key=lambda item: item.trade_date)
    _fill_share_change(points)
    return points


def parse_eastmoney_daily_row(row: dict, provider_id: str = "eastmoney_holdranks") -> DailyPoint | None:
    trade_date = parse_iso_date(row.get("TRADE_DATE") or row.get("HOLD_DATE"))
    shares = parse_number(row.get("HOLD_SHARES"))
    if not trade_date or shares is None:
        return None
    # HOLD_SHARES_RATIO 与港交所官方百分比一致。TOTAL_SHARES_RATIO 对不上，不用。
    pct = parse_number(row.get("HOLD_SHARES_RATIO"))
    return DailyPoint(
        trade_date=trade_date,
        shares=int(shares),
        pct_of_issued=pct,
        hold_value=parse_number(row.get("HOLD_MARKET_CAP")),
        close_price=parse_number(row.get("CLOSE_PRICE")),
        change_pct=parse_number(row.get("CHANGE_RATE")),
        share_change=_as_int(row.get("HOLD_SHARES_CHANGE")),
        source="东方财富 RPT_MUTUAL_STOCK_HOLDRANKS",
        provider_id=provider_id,
    )


def parse_eastmoney_seat_row(row: dict, provider_id: str = "eastmoney_seats") -> SeatPoint | None:
    trade_date = parse_iso_date(row.get("HOLD_DATE") or row.get("TRADE_DATE"))
    shares = parse_number(row.get("HOLD_NUM"))
    participant = (row.get("PARTICIPANT_CODE") or "").strip().upper()
    if not trade_date or shares is None or not participant:
        return None
    return SeatPoint(
        trade_date=trade_date,
        participant_id=participant,
        participant_name=row.get("ORG_NAME") or "",
        shares=int(shares),
        pct_of_issued=parse_number(row.get("HOLD_SHARES_RATIO")),
        source="东方财富 RPT_MUTUAL_HOLD_DET",
        provider_id=provider_id,
    )


def _cell_value(cell) -> str:
    body = cell.select_one(".mobile-list-body")
    return (body or cell).get_text(" ", strip=True)


def parse_mutualmarket_html(html: str, provider_id: str = "hkex_mutualmarket") -> list[DailyPoint]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    match = re.search(r"Shareholding Date:\s*(\d{4}/\d{2}/\d{2})", text)
    trade_date = parse_iso_date(match.group(1)) if match else None
    points = []
    for row in soup.find_all("tr"):
        code_cell = row.select_one(".col-stock-code")
        share_cell = row.select_one(".col-shareholding")
        pct_cell = row.select_one(".col-shareholding-percent")
        name_cell = row.select_one(".col-stock-name")
        if not code_cell or not share_cell:
            continue
        code = normalize_code(_cell_value(code_cell))
        shares = parse_number(_cell_value(share_cell))
        if not code or shares is None or not trade_date:
            continue
        points.append(DailyPoint(
            trade_date=trade_date,
            shares=int(shares),
            pct_of_issued=parse_number(_cell_value(pct_cell) if pct_cell else None),
            source="港交所 mutualmarket.aspx（A00003+A00004 合计）",
            provider_id=provider_id,
            # 股票代码先放在 source 末尾由调用方过滤；这里用 share_change 存不了代码。
            # 用 close_price 不合适。把代码写入 notes 式字段：provider 调用方会按页面过滤。
        ))
        points[-1].source = (
            "港交所 mutualmarket.aspx（A00003+A00004 合计）|"
            f"{code}|{_cell_value(name_cell) if name_cell else ''}"
        )
    return points


def mutualmarket_code(point: DailyPoint) -> str:
    parts = (point.source or "").split("|")
    return parts[1] if len(parts) > 1 else ""


def parse_di_summary_html(
    html: str,
    *,
    stock_code: str,
    summary_date: str,
    provider_id: str = "hkex_di_daily",
) -> list[Holding]:
    soup = BeautifulSoup(html, "lxml")
    want = normalize_code(stock_code)
    holdings = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 10:
            continue
        code = normalize_code(cells[2].get_text(" ", strip=True))
        if code != want:
            continue
        serial = cells[0].get_text(" ", strip=True)
        if not re.match(r"^[A-Z]{2}\d", serial):
            continue
        name = cells[4].get_text(" ", strip=True)
        event = parse_iso_date(cells[5].get_text(" ", strip=True))
        if not event or not name:
            continue
        positions = _positions_from_balance_cell(cells[-1])
        for pos in positions:
            holding = make_disclosure_holding(
                name=name,
                report_date=event,
                shares=pos["shares"],
                position=pos["position"],
                pct=pos.get("pct"),
                source="港交所权益披露日报",
                provider_id=provider_id,
                source_ref=serial,
                report_label=f"DSM {summary_date} 事件日 {event}",
                notes=f"日报日期 {summary_date}；表格 {serial}；余额为申报当时的好仓/淡仓，不是基金报告期",
            )
            if holding:
                holdings.append(holding)
    return holdings


def _positions_from_balance_cell(cell) -> list[dict]:
    inner_rows = cell.find_all("tr")
    if inner_rows:
        found = []
        for inner in inner_rows:
            found.extend(parse_position_tokens(inner.get_text(" ", strip=True)))
        return found
    return parse_position_tokens(cell.get_text(" ", strip=True))


def parse_di_roster_csv(path: str | Path, stock_code: str, provider_id: str = "reference_di_roster") -> list[Holding]:
    """完整/合并大股东名单。同一股票若有合并名单，只用合并名单，避免同一利益链重复。"""
    want = normalize_code(stock_code)
    rows = [row for row in _csv_rows(path) if normalize_code(row.get("stock_code") or "") == want]
    kinds = {row.get("report") for row in rows}
    if "consolidated_ss" in kinds:
        rows = [row for row in rows if row.get("report") == "consolidated_ss"]
    holdings = []
    seen = set()
    for row in rows:
        name = (row.get("shareholder") or "").strip()
        report_date = parse_iso_date(row.get("last_notice_date"))
        serial = (row.get("form_serial") or "").strip()
        if not name or not report_date or not re.match(r"^[A-Z]{2}\d", serial):
            continue
        share_tokens = parse_position_tokens(row.get("shares_interested") or "")
        pct_tokens = {item["position"]: item["pct"] for item in _pct_tokens(row.get("pct_issued_voting") or "")}
        for token in share_tokens:
            key = (serial, token["position"])
            if key in seen:
                continue
            seen.add(key)
            pct = pct_tokens.get(token["position"])
            holding = make_disclosure_holding(
                name=name,
                report_date=report_date,
                shares=token["shares"],
                position=token["position"],
                pct=pct if pct is not None else token.get("pct"),
                source="港交所权益披露大股东合并名单（2026-11-25 实测导出）",
                provider_id=provider_id,
                source_ref=serial,
                report_label="合并名单",
                notes="名单余额对应最近一次申报，不是每日持仓；不同股东的百分比重叠，禁止加总",
            )
            if holding:
                holdings.append(holding)
    return holdings


def _pct_tokens(text: str) -> list[dict]:
    found = []
    for match in re.finditer(r"([\d.]+)\(([LSPlsp])\)", text or ""):
        pct = parse_number(match.group(1))
        if pct is None:
            continue
        found.append({"position": match.group(2).upper(), "shares": 0, "pct": pct})
    return found


def parse_licensed_csv(path: str | Path, stock_code: str | None = None) -> list[DailyPoint]:
    """授权 CSV：trade_date, stock_code, shares, pct_of_issued。单位必须已经是股。"""
    want = normalize_code(stock_code) if stock_code else ""
    points = []
    for row in _csv_rows(path):
        code = normalize_code(row.get("stock_code") or "")
        if want and code and code != want:
            continue
        trade_date = parse_iso_date(row.get("trade_date"))
        shares = parse_number(row.get("shares"))
        if not trade_date or shares is None:
            continue
        points.append(DailyPoint(
            trade_date=trade_date,
            shares=int(shares),
            pct_of_issued=parse_number(row.get("pct_of_issued")),
            hold_value=parse_number(row.get("hold_value")),
            close_price=parse_number(row.get("close_price")),
            change_pct=parse_number(row.get("change_pct")),
            source="授权 CSV（SOUTHBOUND_LICENSED_CSV）",
            provider_id="hkex_licensed_csv",
        ))
    points.sort(key=lambda item: item.trade_date)
    _fill_share_change(points)
    return points


def parse_official_snapshot_csv(path: str | Path) -> list[DailyPoint]:
    points = []
    for row in _csv_rows(path):
        trade_date = parse_iso_date(row.get("shareholding_date"))
        shares = parse_number(row.get("shareholding_in_ccass"))
        if not trade_date or shares is None:
            continue
        code = normalize_code(row.get("stock_code") or "")
        points.append(DailyPoint(
            trade_date=trade_date,
            shares=int(shares),
            pct_of_issued=parse_number(row.get("pct_of_issued")),
            source=f"港交所 mutualmarket.aspx|{code}|{row.get('name') or ''}",
            provider_id="hkex_mutualmarket",
        ))
    return points


def _as_int(value) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    return int(number)


def _fill_share_change(points: list[DailyPoint]) -> None:
    previous = None
    for point in points:
        if point.share_change is None and previous is not None:
            point.share_change = point.shares - previous
        previous = point.shares
