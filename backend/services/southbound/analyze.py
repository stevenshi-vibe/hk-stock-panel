"""
只做日频南下序列和季度公募持仓能够支撑的分析。

不做：把季报持仓摊到每一天、把权益披露百分比加总、用险资/社保填未识别部分。
"""
from __future__ import annotations

from .align import align_to_trading_day
from .models import DailyPoint, Holding, SeatPoint


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    var_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y)


def _interpret(r: float | None, left: str, right: str) -> str:
    if r is None:
        return f"{left}与{right}的样本不足，或其中一列没有波动。"
    strength = abs(r)
    if strength < 0.15:
        word = "很弱"
    elif strength < 0.35:
        word = "弱"
    elif strength < 0.6:
        word = "中等"
    else:
        word = "较强"
    direction = "正" if r >= 0 else "负"
    return (
        f"{left}与{right}的同期皮尔逊相关为 {r:.3f}，属于{direction}向{word}相关。"
        "这是同一交易日的统计关系，不是因果关系；南下数字是日终持股。"
    )


def price_relationship(series: list[DailyPoint]) -> dict:
    xs: list[float] = []
    ys: list[float] = []
    previous = None
    for point in series:
        if previous and previous > 0 and point.share_change is not None and point.change_pct is not None:
            xs.append(point.share_change / previous * 100)
            ys.append(point.change_pct)
        previous = point.shares
    r = _pearson(xs, ys) if len(xs) >= 30 else None
    return {
        "available": len(xs) >= 30,
        "n": len(xs),
        "pearson_r": None if r is None else round(r, 4),
        "x": "南下持股日变动占上日持股的百分比",
        "y": "当日涨跌幅（%）",
        "interpretation": _interpret(r, "南下持股变动", "当日涨跌幅") if len(xs) >= 30 else "有效交易日不足 30，不计算相关。",
    }


def volume_relationship(series: list[DailyPoint], volume_by_date: dict[str, float] | None) -> dict:
    if not volume_by_date:
        return {
            "available": False,
            "reason": "行情表里没有成交量，本次不计算南下变动和成交的关系。",
        }
    flow: list[float] = []
    volume: list[float] = []
    for point in series:
        vol = volume_by_date.get(point.trade_date)
        if vol is None or point.share_change is None:
            continue
        flow.append(float(abs(point.share_change)))
        volume.append(float(vol))
    r = _pearson(flow, volume) if len(flow) >= 30 else None
    return {
        "available": len(flow) >= 30,
        "n": len(flow),
        "pearson_r": None if r is None else round(r, 4),
        "x": "南下持股日变动的绝对值（股）",
        "y": "当日成交量",
        "interpretation": _interpret(r, "南下持股变动绝对值", "成交量") if len(flow) >= 30 else "成交量样本不足 30 天。",
    }


def abnormal_days(series: list[DailyPoint], window: int = 60, z_min: float = 2.5, limit: int = 12) -> list[dict]:
    changes: list[tuple[DailyPoint, int]] = []
    for point in series:
        if point.share_change is None:
            continue
        changes.append((point, point.share_change))
    flags = []
    for index, (point, change) in enumerate(changes):
        history = [item[1] for item in changes[max(0, index - window):index]]
        if len(history) < 30:
            continue
        mean = sum(history) / len(history)
        variance = sum((item - mean) ** 2 for item in history) / (len(history) - 1)
        std = variance ** 0.5
        if std == 0:
            continue
        score = (change - mean) / std
        if abs(score) < z_min:
            continue
        flags.append({
            "trade_date": point.trade_date,
            "share_change": change,
            "z": round(score, 2),
            "close_price": point.close_price,
            "change_pct": point.change_pct,
            "shares": point.shares,
            "direction": "流入" if change > 0 else "流出",
            "rule": f"相对前 {len(history)} 个交易日持股变动，|z|≥{z_min}",
        })
    flags.sort(key=lambda item: abs(item["z"]), reverse=True)
    return flags[:limit]


def concentration(confirmed_rows: list[dict]) -> dict:
    shares = [row["shares"] for row in confirmed_rows if row.get("shares")]
    total = sum(shares)
    if total <= 0 or not confirmed_rows:
        return {
            "available": False,
            "reason": "没有可计入的确认港股通持仓，不算集中度。",
        }
    weights = [item / total for item in shares]
    hhi = sum(weight * weight for weight in weights)
    top = max(confirmed_rows, key=lambda row: row["shares"])
    return {
        "available": True,
        "n_funds": len(confirmed_rows),
        "hhi": round(hhi, 4),
        "top1_name": top["holder_name"],
        "top1_shares": top["shares"],
        "top1_share_of_explained": round(top["shares"] / total, 4),
        "note": "HHI 只在已识别的港股通公募内部计算。未识别南下资金不在分母里，所以它不代表全部港股通的集中度。",
    }


def _known_delta(current: Holding | None, previous: Holding | None) -> tuple[int | None, str]:
    if current and previous:
        return current.shares - previous.shares, "known"
    if current and not previous:
        if current.report_scope == "top10":
            return None, "top10_gap"
        return None, "unknown_prior"
    if previous and not current:
        if previous.report_scope == "top10":
            return None, "top10_exit"
        return None, "unknown_exit"
    return None, "missing"


def quarterly_attribution(daily: list[DailyPoint], funds: list[Holding]) -> list[dict]:
    """只统计确认经港股通的好仓。缺失的前十大不按 0 处理。"""
    confirmed = [
        item for item in funds
        if item.holder_type == "public_fund" and item.counts_toward_southbound and item.position_type == "L"
    ]
    by_date: dict[str, dict[str, Holding]] = {}
    for item in confirmed:
        bucket = by_date.setdefault(item.report_date, {})
        current = bucket.get(item.holder_key)
        if current is None or item.confidence >= current.confidence:
            bucket[item.holder_key] = item
    # 只在季度末日之间做归因。页面上「截止至」若不是 3/31、6/30、9/30、12/31，
    # 那是中途披露，保留在持仓表里，但不把它写成一个新的季度。
    dates = [day for day in sorted(by_date) if day[5:] in {"03-31", "06-30", "09-30", "12-31"}]
    trading_days = [point.trade_date for point in daily]
    daily_map = {point.trade_date: point for point in daily}
    rows = []
    for left, right in zip(dates, dates[1:]):
        keys = set(by_date[left]) | set(by_date[right])
        members = []
        known_sum = 0
        known_count = 0
        unknown_count = 0
        for key in keys:
            current = by_date[right].get(key)
            previous = by_date[left].get(key)
            delta, quality = _known_delta(current, previous)
            name = (current or previous).holder_name
            if delta is not None:
                known_sum += delta
                known_count += 1
            else:
                unknown_count += 1
            members.append({
                "holder_key": key,
                "holder_name": name,
                "shares_start": None if previous is None else previous.shares,
                "shares_end": None if current is None else current.shares,
                "shares_change": delta,
                "quality": quality,
                "report_scope_end": None if current is None else current.report_scope,
            })
        align_left = align_to_trading_day(left, trading_days)
        align_right = align_to_trading_day(right, trading_days)
        sb_left = daily_map.get(align_left["aligned_date"]) if align_left["aligned_date"] else None
        sb_right = daily_map.get(align_right["aligned_date"]) if align_right["aligned_date"] else None
        sb_change = None
        if sb_left and sb_right:
            sb_change = sb_right.shares - sb_left.shares
        ratio = None
        if sb_change not in (None, 0):
            ratio = known_sum / sb_change
        members.sort(key=lambda item: abs(item["shares_change"] or 0), reverse=True)
        rows.append({
            "report_start": left,
            "report_end": right,
            "southbound_date_start": align_left.get("aligned_date"),
            "southbound_date_end": align_right.get("aligned_date"),
            "southbound_change": sb_change,
            "confirmed_known_change": known_sum,
            "known_funds": known_count,
            "unknown_funds": unknown_count,
            "explained_fraction_of_change": None if ratio is None else round(ratio, 4),
            "holders": members,
            "note": "已识别变动只加总两期都有披露的基金。前十大进出不做零填充，所以这个比例是下限式的归因，不是全部公募。",
        })
    return rows


def cross_check(
    *,
    trade_date: str | None,
    daily_shares: int | None,
    seats: list[SeatPoint],
    official_shares: int | None = None,
    panel_a00003: int | None = None,
    panel_a00004: int | None = None,
) -> dict:
    """A00003+A00004 应等于南下合计。官方页、东财席位、面板 CCASS 三者能对上的才算交叉验证。"""
    seat_map = {}
    for seat in seats:
        if trade_date and seat.trade_date != trade_date:
            continue
        seat_map[seat.participant_id] = seat
    a3 = seat_map.get("A00003")
    a4 = seat_map.get("A00004")
    result = {"trade_date": trade_date, "available": False}
    if a3 and a4 and daily_shares is not None:
        seat_sum = a3.shares + a4.shares
        result.update({
            "available": True,
            "a00003_shares": a3.shares,
            "a00003_name": a3.participant_name,
            "a00004_shares": a4.shares,
            "a00004_name": a4.participant_name,
            "seat_sum": seat_sum,
            "daily_shares": daily_shares,
            "diff_vs_daily": seat_sum - daily_shares,
            "match_daily": seat_sum == daily_shares,
            "seat_source": a3.source,
        })
    else:
        result["reason"] = "这一天没有同时拿到 A00003、A00004 和南下合计。"
    if official_shares is not None and result.get("seat_sum") is not None:
        result["official_shares"] = official_shares
        result["diff_vs_official"] = result["seat_sum"] - official_shares
        result["match_official"] = result["seat_sum"] == official_shares
    elif official_shares is not None and daily_shares is not None:
        result["official_shares"] = official_shares
        result["diff_daily_vs_official"] = daily_shares - official_shares
        result["match_official"] = daily_shares == official_shares
        result["available"] = True
    if panel_a00003 is not None and panel_a00004 is not None and daily_shares is not None:
        panel_sum = panel_a00003 + panel_a00004
        result["panel_ccass_sum"] = panel_sum
        result["diff_vs_panel"] = panel_sum - daily_shares
        result["match_panel"] = panel_sum == daily_shares
        result["available"] = True
    elif panel_a00003 is None or panel_a00004 is None:
        result["panel_ccass"] = "个股库里这一天没有 A00003/A00004 的 CCASS 席位记录，未用面板席位做交叉验证。"
    return result


def build_analysis(
    series: list[DailyPoint],
    holdings: list[Holding],
    decomposition: dict,
    *,
    seats: list[SeatPoint] | None = None,
    official: list[DailyPoint] | None = None,
    volume_by_date: dict[str, float] | None = None,
    panel_seats: dict | None = None,
) -> dict:
    seats = seats or []
    official = official or []
    confirmed = decomposition.get("holders", {}).get("confirmed") or []
    latest_date = decomposition.get("snapshot", {}).get("latest_southbound_date")
    daily_shares = decomposition.get("snapshot", {}).get("latest_southbound_shares")
    official_same = next((point.shares for point in official if point.trade_date == latest_date), None)
    panel = panel_seats or {}
    return {
        "price_relationship": price_relationship(series),
        "volume_relationship": volume_relationship(series, volume_by_date),
        "abnormal_days": abnormal_days(series),
        "quarterly_attribution": quarterly_attribution(series, holdings),
        "concentration": concentration(confirmed),
        "cross_check": cross_check(
            trade_date=latest_date,
            daily_shares=daily_shares,
            seats=seats,
            official_shares=official_same,
            panel_a00003=panel.get("A00003"),
            panel_a00004=panel.get("A00004"),
        ),
        "design_notes": [
            "股价关系用日终南下持股变动对当日涨跌幅，样本至少 30 个交易日。",
            "异常日是持股变动相对过去 60 个交易日的 z 分数，|z|≥2.5，不是成交额阈值。",
            "季度归因只比较确认经港股通的基金在两个报告期之间的持股差，并与对齐后的南下持股差放在一起。",
            "集中度是已识别港股通公募内部的 HHI。未识别部分不参与。",
            "交叉验证比较中登两个席位之和、南下合计、以及港交所官方合计。对不上就标出差值，不抹平。",
        ],
    }
