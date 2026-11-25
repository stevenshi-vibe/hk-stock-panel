"""采集编排和页面用的拆解结果。计算在 decompose/analyze，这里只负责读写和换源。"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from db import ensure_stock, get_stock_conn

from .analyze import build_analysis
from .decompose import decompose
from .models import DailyPoint
from .parse import (
    load_focus_catalog,
    mutualmarket_code,
    normalize_code,
    parse_di_roster_csv,
    parse_di_summary_html,
    parse_eastmoney_daily_row,
    parse_eastmoney_fund_html,
    parse_eastmoney_seat_row,
    parse_licensed_csv,
    parse_mutualmarket_html,
)
from .store import choose_history, load_bundle, panel_connect_seats, upsert_daily, upsert_holdings, upsert_seats, volume_by_date

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "southbound"

COMPLIANCE = [
    {
        "provider_id": "eastmoney_holdranks",
        "role": "南下合计（日频）",
        "note": "东方财富转载的港股通持股，没有面向本项目的商用授权。产品化应换成港交所授权 CSV 或持牌供应商，实现 DailyHoldingsProvider 后替换 provider_id。",
    },
    {
        "provider_id": "eastmoney_fund",
        "role": "公募持仓",
        "note": "天天基金网页接口。持仓数字来自基金法定披露，但传输通道未经东财授权，不宜当作商业供数。",
    },
    {
        "provider_id": "hkex_mutualmarket",
        "role": "官方南下合计（单日核对）",
        "note": "页面位于 www3.hkexnews.hk/sdw/search/，与 CCASS 查询同一组条款：个人、非商业使用；未经书面许可不得再分发或用程序化方式建立数据库。这里每次采集只请求最近一个持股日，用来和东财核对，不回放历史。",
    },
    {
        "provider_id": "hkex_di_daily",
        "role": "权益披露日报",
        "note": "法定公开披露。系统抓取和再分发前仍应向港交所确认站点条款。披露里的外资和控股架构不是港股通。",
    },
    {
        "provider_id": "reference_di_roster",
        "role": "大股东合并名单",
        "note": "来自 2026-11-25 对 di.hkex.com.hk 的实测导出，只覆盖附件里的样例股票。它不是持续更新的接口。",
    },
    {
        "provider_id": "hkex_licensed_csv",
        "role": "预留的授权文件",
        "note": "设置环境变量 SOUTHBOUND_LICENSED_CSV 指向含 trade_date,stock_code,shares,pct_of_issued 的 CSV 后，采集会改读这个文件，不再请求东财日频接口。",
    },
]


def _progress(callback, pct: int, stage: str) -> None:
    if callback:
        callback(pct, stage)


def _dedupe_daily(points: list[DailyPoint]) -> list[DailyPoint]:
    by_date = {}
    for point in points:
        by_date[point.trade_date] = point
    ordered = [by_date[key] for key in sorted(by_date)]
    previous = None
    for point in ordered:
        if point.share_change is None and previous is not None:
            point.share_change = point.shares - previous
        previous = point.shares
    return ordered


def _focus_codes(fund_scan: str) -> list[str]:
    catalog = load_focus_catalog(FIXTURE_DIR / "focus_funds.csv")
    if fund_scan == "none":
        return []
    if fund_scan == "sample":
        wanted = ["159792", "513180", "513130", "000041", "110011", "001837", "001874", "001875"]
        return [code for code in wanted if code in catalog] or wanted
    return list(catalog.keys())


def collect_stock(
    stock_code: str,
    *,
    fund_scan: str = "focus",
    years: list[str] | None = None,
    di_days: int = 8,
    include_reference_roster: bool = True,
    progress=None,
) -> dict:
    """抓取并入库。单只基金或单日日报失败不会丢掉已经拿到的其他数据。"""
    from scrapers.southbound_scraper import (
        fetch_di_summary,
        fetch_eastmoney_daily,
        fetch_eastmoney_seats,
        fetch_fund_archive,
        fetch_mutualmarket,
        recent_weekdays,
    )

    code = ensure_stock(stock_code)
    years = years or ["2024", "2025", "2026"]
    errors: list[str] = []
    counts = {"daily": 0, "seats": 0, "official": 0, "funds": 0, "disclosures": 0, "roster": 0}
    catalog = load_focus_catalog(FIXTURE_DIR / "focus_funds.csv")

    _progress(progress, 5, "南下合计")
    latest_day = None
    stock_name = ""
    licensed_path = os.environ.get("SOUTHBOUND_LICENSED_CSV")
    try:
        if licensed_path:
            points = _dedupe_daily(parse_licensed_csv(licensed_path, code))
            provider_id = "hkex_licensed_csv"
        else:
            raw_rows = fetch_eastmoney_daily(code)
            if raw_rows:
                stock_name = raw_rows[0].get("SECURITY_NAME") or ""
            points = _dedupe_daily([
                item for item in (parse_eastmoney_daily_row(row) for row in raw_rows) if item
            ])
            provider_id = "eastmoney_holdranks"
        with get_stock_conn(code) as conn:
            conn.execute("DELETE FROM southbound_daily WHERE provider_id = ?", (provider_id,))
            counts["daily"] = upsert_daily(conn, points)
        if points:
            latest_day = points[-1].trade_date
    except Exception as exc:
        logger.exception("southbound daily failed")
        errors.append(f"南下合计：{exc}")

    if stock_name:
        ensure_stock(code, stock_name)

    _progress(progress, 18, "分席位")
    if latest_day:
        try:
            seat_rows = fetch_eastmoney_seats(code, latest_day)
            seats = [item for item in (parse_eastmoney_seat_row(row) for row in seat_rows) if item]
            with get_stock_conn(code) as conn:
                conn.execute("DELETE FROM southbound_seats WHERE provider_id = ?", ("eastmoney_seats",))
                counts["seats"] = upsert_seats(conn, seats)
        except Exception as exc:
            logger.exception("southbound seats failed")
            errors.append(f"分席位：{exc}")

    _progress(progress, 28, "港交所合计")
    try:
        html = fetch_mutualmarket()
        official = [point for point in parse_mutualmarket_html(html) if mutualmarket_code(point) == code]
        with get_stock_conn(code) as conn:
            conn.execute("DELETE FROM southbound_daily WHERE provider_id = ?", ("hkex_mutualmarket",))
            counts["official"] = upsert_daily(conn, official)
    except Exception as exc:
        logger.exception("mutualmarket failed")
        errors.append(f"港交所港股通持股页：{exc}")

    fund_codes = _focus_codes(fund_scan)
    fund_holdings = []
    total_calls = max(1, len(fund_codes) * len(years))
    done = 0
    for fund_code in fund_codes:
        meta = catalog.get(fund_code, {})
        for year in years:
            done += 1
            _progress(progress, 30 + int(45 * done / total_calls), f"基金 {fund_code} {year}")
            try:
                text = fetch_fund_archive(fund_code, year)
                fund_holdings.extend(parse_eastmoney_fund_html(
                    text,
                    fund_code=fund_code,
                    fund_name=meta.get("fund_name") or fund_code,
                    fund_type=meta.get("fund_type") or "",
                    stock_code=code,
                ))
            except Exception as exc:
                errors.append(f"基金 {fund_code} {year}：{exc}")
    with get_stock_conn(code) as conn:
        conn.execute("DELETE FROM southbound_holdings WHERE provider_id = ?", ("eastmoney_fund",))
        counts["funds"] = upsert_holdings(conn, fund_holdings)

    _progress(progress, 80, "权益披露日报")
    notices = []
    for day in recent_weekdays(di_days):
        try:
            html = fetch_di_summary(day, "C1")
            notices.extend(parse_di_summary_html(
                html, stock_code=code, summary_date=day.isoformat(),
            ))
        except Exception as exc:
            errors.append(f"权益披露 {day.isoformat()}：{exc}")
    with get_stock_conn(code) as conn:
        conn.execute("DELETE FROM southbound_holdings WHERE provider_id = ?", ("hkex_di_daily",))
        counts["disclosures"] = upsert_holdings(conn, notices)

    _progress(progress, 94, "大股东名单")
    roster_path = FIXTURE_DIR / "di_substantial_shareholders.csv"
    if include_reference_roster and roster_path.exists():
        roster = parse_di_roster_csv(roster_path, code)
        with get_stock_conn(code) as conn:
            conn.execute("DELETE FROM southbound_holdings WHERE provider_id = ?", ("reference_di_roster",))
            counts["roster"] = upsert_holdings(conn, roster)

    _progress(progress, 100, "完成")
    return {
        "stock_code": code,
        "stock_name": stock_name,
        "counts": counts,
        "errors": errors[:30],
        "error_count": len(errors),
        "fund_scan": fund_scan,
        "funds_requested": len(fund_codes),
        "years": years,
        "compliance": COMPLIANCE,
    }


def _series_rows(series: list[DailyPoint], start: str | None, end: str | None) -> list[dict]:
    rows = []
    for point in series:
        if start and point.trade_date < start:
            continue
        if end and point.trade_date > end:
            continue
        rows.append({
            "trade_date": point.trade_date,
            "shares": point.shares,
            "pct_of_issued": point.pct_of_issued,
            "hold_value": point.hold_value,
            "close_price": point.close_price,
            "change_pct": point.change_pct,
            "share_change": point.share_change,
            "source": point.source,
            "provider_id": point.provider_id,
        })
    return rows


def build_view(stock_code: str, start: str | None = None, end: str | None = None) -> dict:
    code = normalize_code(stock_code)
    ensure_stock(code)
    with get_stock_conn(code) as conn:
        bundle = load_bundle(conn)
        series, official = choose_history(bundle["daily"])
        volumes = volume_by_date(conn)
        latest_date = series[-1].trade_date if series else None
        panel = panel_connect_seats(conn, latest_date)
    holdings = bundle["holdings"]
    view = decompose(series, holdings, start=start, end=end)
    analysis = build_analysis(
        series, holdings, view,
        seats=bundle["seats"],
        official=official,
        volume_by_date=volumes,
        panel_seats=panel,
    )
    window_dates = {row["trade_date"] for row in _series_rows(series, start, end)}
    if start or end:
        analysis["abnormal_days"] = [
            item for item in analysis["abnormal_days"] if item["trade_date"] in window_dates
        ]
    providers = sorted({point.provider_id for point in bundle["daily"]} | {item.provider_id for item in holdings})
    return {
        "stock_code": code,
        "has_data": bool(series),
        "providers": providers,
        "compliance": COMPLIANCE,
        "window": view["window"],
        "snapshot": view["snapshot"],
        "holders": view["holders"],
        "series": _series_rows(series, start, end),
        "analysis": analysis,
        "limitations": view["limitations"],
        "as_of": date.today().isoformat(),
    }
