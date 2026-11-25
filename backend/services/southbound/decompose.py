"""
覆盖率口径：

分母是对齐到基金报告期的南下合计持股（A00003+A00004），不是用户区间终点的最新持股。
分子只加「确认经港股通」且单位校验通过的好仓，并且必须落在同一个报告期。
更早报告期的基金、可能经港股通、QDII、权益披露都不进分子。
"""
from __future__ import annotations

from .align import align_to_trading_day
from .models import CHANNEL_LABELS, CONFIRMED, NON_CONNECT, POSSIBLE, DailyPoint, Holding


def _in_window(day: str, start: str | None, end: str | None) -> bool:
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _index_by_date(series: list[DailyPoint]) -> dict[str, DailyPoint]:
    return {point.trade_date: point for point in series}


def _window_endpoints(series: list[DailyPoint], start: str | None, end: str | None) -> tuple[DailyPoint | None, DailyPoint | None]:
    rows = [point for point in series if _in_window(point.trade_date, start, end)]
    if not rows:
        return None, None
    return rows[0], rows[-1]


def _latest_on_or_before(holdings: list[Holding], holder_key: str, end: str, position: str = "L") -> Holding | None:
    rows = [
        item for item in holdings
        if item.holder_key == holder_key and item.position_type == position and item.report_date <= end
    ]
    if not rows:
        return None
    return max(rows, key=lambda item: item.report_date)


def _holding_view(item: Holding, previous: Holding | None, change_note: str) -> dict:
    change = None if previous is None else item.shares - previous.shares
    return {
        "holder_key": item.holder_key,
        "holder_name": item.holder_name,
        "holder_type": item.holder_type,
        "channel_tag": item.channel_tag,
        "channel_label": CHANNEL_LABELS.get(item.channel_tag, item.channel_tag),
        "position_type": item.position_type,
        "shares": item.shares,
        "shares_previous": None if previous is None else previous.shares,
        "previous_report_date": None if previous is None else previous.report_date,
        "shares_change": change,
        "change_note": change_note,
        "pct_nav": item.pct_nav,
        "pct_issued": item.pct_issued,
        "report_date": item.report_date,
        "report_label": item.report_label,
        "report_scope": item.report_scope,
        "source": item.source,
        "source_ref": item.source_ref,
        "provider_id": item.provider_id,
        "confidence": item.confidence,
        "unit_status": item.unit_status,
        "raw_unit": item.raw_unit,
        "raw_shares": item.raw_shares,
        "counts_toward_southbound": item.counts_toward_southbound,
        "notes": item.notes,
    }


def _previous_shares(holdings: list[Holding], current: Holding) -> tuple[Holding | None, str]:
    earlier = [
        item for item in holdings
        if item.holder_key == current.holder_key
        and item.position_type == current.position_type
        and item.report_date < current.report_date
        and item.holder_type == current.holder_type
    ]
    if earlier:
        previous = max(earlier, key=lambda item: item.report_date)
        return previous, "相邻已披露报告期之差"
    if current.report_scope == "extended":
        return None, "更早报告期没有这只股票；若当时是全持仓披露，可视为 0，这里仍不自动填 0"
    if current.report_scope == "top10":
        return None, "一季报/三季报通常只有前十大，缺席不等于清仓，变动记为未知"
    return None, "没有更早的可比报告，变动记为未知"


def decompose(
    daily: list[DailyPoint],
    holdings: list[Holding],
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    series = sorted(daily, key=lambda item: item.trade_date)
    trading_days = [point.trade_date for point in series]
    by_date = _index_by_date(series)
    window_start, window_end = _window_endpoints(series, start, end)
    end_cap = end or (series[-1].trade_date if series else "9999-12-31")

    funds = [item for item in holdings if item.holder_type == "public_fund" and item.position_type == "L"]
    confirmed_pool = [item for item in funds if item.counts_toward_southbound and item.report_date <= end_cap]
    snapshot_report = max((item.report_date for item in confirmed_pool), default=None)

    explained_rows = []
    stale_confirmed = []
    if snapshot_report:
        for item in confirmed_pool:
            if item.report_date == snapshot_report:
                previous, note = _previous_shares(funds, item)
                explained_rows.append(_holding_view(item, previous, note))
            else:
                latest = _latest_on_or_before(confirmed_pool, item.holder_key, end_cap)
                if latest and latest.report_date == item.report_date and latest.report_date != snapshot_report:
                    stale_confirmed.append(_holding_view(item, None, "报告期早于本次快照，不计入本期覆盖率"))
        # 同一基金只保留快照日的一条（多来源时留置信度更高的）
        explained_rows = _dedupe_views(explained_rows)
        stale_confirmed = _dedupe_views([
            row for row in stale_confirmed
            if row["holder_key"] not in {item["holder_key"] for item in explained_rows}
        ])

    explained_shares = sum(row["shares"] for row in explained_rows)
    alignment = (
        align_to_trading_day(snapshot_report, trading_days)
        if snapshot_report else {"aligned_date": None, "gap_days": None, "quality": "missing"}
    )
    aligned_point = by_date.get(alignment["aligned_date"]) if alignment["aligned_date"] else None
    southbound_shares = aligned_point.shares if aligned_point else None

    over = bool(southbound_shares is not None and explained_shares > southbound_shares)
    if southbound_shares is None:
        unidentified = None
        coverage = None
        excess = None
    elif over:
        unidentified = 0
        excess = explained_shares - southbound_shares
        coverage = explained_shares / southbound_shares
    else:
        unidentified = southbound_shares - explained_shares
        excess = 0
        coverage = explained_shares / southbound_shares if southbound_shares else None

    possible_rows = _latest_views(
        [item for item in funds if item.channel_tag == POSSIBLE and item.report_date <= end_cap],
        funds,
    )
    non_connect_funds = _latest_views(
        [item for item in funds if item.channel_tag == NON_CONNECT and item.report_date <= end_cap],
        funds,
    )
    possible_aligned = [
        row for row in possible_rows
        if snapshot_report and row["report_date"] == snapshot_report and row["unit_status"] in {"ok", "unchecked"}
    ]
    possible_shares = sum(row["shares"] for row in possible_aligned)

    disclosures = [
        _holding_view(item, None, "权益披露余额，不参与南下覆盖率")
        for item in holdings
        if item.holder_type == "disclosure" and item.report_date <= end_cap
    ]
    disclosures.sort(key=lambda row: (row["report_date"], row["holder_name"]), reverse=True)

    latest = series[-1] if series else None
    staleness = None
    if latest and snapshot_report:
        from datetime import date
        staleness = (date.fromisoformat(latest.trade_date) - date.fromisoformat(snapshot_report)).days

    misaligned = None
    if latest and explained_shares and latest.trade_date != alignment.get("aligned_date"):
        misaligned = {
            "coverage": explained_shares / latest.shares if latest.shares else None,
            "warning": "分子仍是基金报告期持仓，除数换成了最新南下持股，两者不可比",
        }

    limitations = [
        "覆盖率的分子只包括名称能确认为港股通产品、且报告期与南下持股日对齐的公募持仓。",
        "一季报和三季报通常只有前十大，缺席不等于卖光，季度变动不会把缺失填成 0。",
        "险资、社保、私募和大部分主动基金不在本次扫描里，未识别部分会偏大。",
        "权益披露的持股包含非港股通，且同一控制链的百分比会重叠，不加入南下解释，也不在披露表里加总。",
    ]
    if alignment.get("quality") == "stale":
        limitations.append("基金报告期和最近一个南下交易日相差超过 7 天，对齐质量标为滞后。")
    if not confirmed_pool:
        limitations.append("本次没有可计入的「确认经港股通」持仓，覆盖率为 0 只说明名单里没有，不说明南下资金不存在。")

    return {
        "window": {
            "start": window_start.trade_date if window_start else None,
            "end": window_end.trade_date if window_end else None,
            "start_shares": window_start.shares if window_start else None,
            "end_shares": window_end.shares if window_end else None,
            "change_shares": (
                None if not window_start or not window_end else window_end.shares - window_start.shares
            ),
            "end_pct_issued": window_end.pct_of_issued if window_end else None,
            "end_close": window_end.close_price if window_end else None,
            "end_hold_value": window_end.hold_value if window_end else None,
            "source": window_end.source if window_end else None,
            "provider_id": window_end.provider_id if window_end else None,
        },
        "snapshot": {
            "report_date": snapshot_report,
            "aligned_southbound_date": alignment.get("aligned_date"),
            "alignment_gap_days": alignment.get("gap_days"),
            "alignment_quality": alignment.get("quality"),
            "southbound_shares": southbound_shares,
            "southbound_pct_issued": aligned_point.pct_of_issued if aligned_point else None,
            "explained_shares": explained_shares,
            "unidentified_shares": unidentified,
            "coverage": coverage,
            "over_explained": over,
            "excess_shares": excess,
            "possible_shares_same_report": possible_shares,
            "possible_note": "可能经港股通的持仓只作敏感性参考，没有加进覆盖率。",
            "staleness_days": staleness,
            "latest_southbound_date": latest.trade_date if latest else None,
            "latest_southbound_shares": latest.shares if latest else None,
            "misaligned_coverage": misaligned,
        },
        "holders": {
            "confirmed": explained_rows,
            "stale_confirmed": stale_confirmed,
            "possible": possible_rows,
            "non_connect_funds": non_connect_funds,
            "disclosures": disclosures,
        },
        "limitations": limitations,
    }


def _dedupe_views(rows: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (row["holder_key"], row["report_date"], row["position_type"])
        current = best.get(key)
        if current is None or (row.get("confidence") or 0) > (current.get("confidence") or 0):
            best[key] = row
    return sorted(best.values(), key=lambda item: item["shares"], reverse=True)


def _latest_views(rows: list[Holding], all_funds: list[Holding]) -> list[dict]:
    latest: dict[str, Holding] = {}
    for item in rows:
        current = latest.get(item.holder_key)
        if current is None or item.report_date > current.report_date or (
            item.report_date == current.report_date and item.confidence > current.confidence
        ):
            latest[item.holder_key] = item
    views = []
    for item in latest.values():
        previous, note = _previous_shares(all_funds, item)
        views.append(_holding_view(item, previous, note))
    views.sort(key=lambda row: row["shares"], reverse=True)
    return views
