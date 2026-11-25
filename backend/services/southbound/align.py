"""基金报告期和南下持股日的对齐。报告期当天不是交易日时，取不晚于报告期的最近一个南下交易日。"""
from __future__ import annotations

import re
from datetime import date

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def parse_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def report_period_end(label: str | None, explicit: str | None = None) -> str | None:
    """优先用页面上的「截止至」日期，再用「YYYY年N季度」推季度末日。"""
    explicit_iso = parse_iso_date(explicit)
    if explicit_iso:
        return explicit_iso
    if not label:
        return None
    match = re.search(r"(\d{4})\s*年\s*([1-4])\s*季度", label)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        month, day = _QUARTER_END[quarter]
        return f"{year:04d}-{month:02d}-{day:02d}"
    if "半年" in label or "中期" in label:
        year_match = re.search(r"(\d{4})", label)
        if year_match:
            return f"{year_match.group(1)}-06-30"
    if "年报" in label:
        year_match = re.search(r"(\d{4})", label)
        if year_match:
            return f"{year_match.group(1)}-12-31"
    return parse_iso_date(label)


def quarter_of(report_date: str) -> int | None:
    month = int(report_date[5:7])
    return {3: 1, 6: 2, 9: 3, 12: 4}.get(month)


def align_to_trading_day(report_date: str, trading_days: list[str], stale_after: int = 7) -> dict:
    """
    取 <= report_date 的最近交易日。
    gap 为 0 是 exact；1–7 天是 ok（周末或短假期）；更长标 stale，覆盖率仍计算但要提示。
    """
    prior = [day for day in trading_days if day <= report_date]
    if not prior:
        return {"aligned_date": None, "gap_days": None, "quality": "missing"}
    aligned = prior[-1]
    gap = (date.fromisoformat(report_date) - date.fromisoformat(aligned)).days
    if gap == 0:
        quality = "exact"
    elif gap <= stale_after:
        quality = "ok"
    else:
        quality = "stale"
    return {"aligned_date": aligned, "gap_days": gap, "quality": quality}
