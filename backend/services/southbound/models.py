"""南下拆解用的内存结构。入库前一律先换成股，而不是万股。"""
from __future__ import annotations

from dataclasses import dataclass


CONFIRMED = "confirmed_connect"
POSSIBLE = "possible_connect"
NON_CONNECT = "non_connect"

CHANNEL_LABELS = {
    CONFIRMED: "确认经港股通",
    POSSIBLE: "可能经港股通",
    NON_CONNECT: "非港股通",
}


@dataclass
class DailyPoint:
    trade_date: str
    shares: int
    pct_of_issued: float | None = None
    hold_value: float | None = None
    close_price: float | None = None
    change_pct: float | None = None
    share_change: int | None = None
    source: str = ""
    provider_id: str = ""


@dataclass
class SeatPoint:
    trade_date: str
    participant_id: str
    participant_name: str
    shares: int
    pct_of_issued: float | None = None
    source: str = ""
    provider_id: str = ""


@dataclass
class Holding:
    holder_key: str
    holder_name: str
    holder_type: str
    channel_tag: str
    report_date: str
    shares: int
    raw_shares: float
    raw_unit: str
    source: str
    provider_id: str
    confidence: float
    counts_toward_southbound: bool
    report_label: str = ""
    pct_nav: float | None = None
    pct_issued: float | None = None
    position_type: str = "L"
    market_value_wan: float | None = None
    source_ref: str = ""
    report_scope: str = "unknown"
    unit_status: str = "unchecked"
    notes: str = ""
    fund_type: str = ""
