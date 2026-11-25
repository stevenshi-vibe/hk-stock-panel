"""南下拆解表。每只股票写在 data/{code}.db，不放进仓库。"""
from __future__ import annotations

from .models import DailyPoint, Holding, SeatPoint

DDL = """
CREATE TABLE IF NOT EXISTS southbound_daily (
    trade_date TEXT NOT NULL,
    shares INTEGER NOT NULL,
    pct_of_issued REAL,
    hold_value REAL,
    close_price REAL,
    change_pct REAL,
    share_change INTEGER,
    source TEXT,
    provider_id TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (trade_date, provider_id)
);

CREATE TABLE IF NOT EXISTS southbound_seats (
    trade_date TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    participant_name TEXT,
    shares INTEGER NOT NULL,
    pct_of_issued REAL,
    source TEXT,
    provider_id TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (trade_date, participant_id, provider_id)
);

CREATE TABLE IF NOT EXISTS southbound_holdings (
    holder_key TEXT NOT NULL,
    holder_name TEXT NOT NULL,
    holder_type TEXT NOT NULL,
    channel_tag TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_label TEXT,
    shares INTEGER NOT NULL,
    raw_shares REAL,
    raw_unit TEXT,
    pct_nav REAL,
    pct_issued REAL,
    position_type TEXT NOT NULL DEFAULT 'L',
    market_value_wan REAL,
    source TEXT,
    source_ref TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL,
    confidence REAL,
    counts_toward_southbound INTEGER NOT NULL DEFAULT 0,
    report_scope TEXT,
    unit_status TEXT,
    fund_type TEXT,
    notes TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (holder_key, report_date, provider_id, position_type, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_sb_daily_date ON southbound_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_sb_hold_date ON southbound_holdings(report_date);
"""


def ensure_tables(conn) -> None:
    conn.executescript(DDL)


def replace_provider_rows(conn, table: str, provider_id: str) -> None:
    conn.execute(f"DELETE FROM {table} WHERE provider_id = ?", (provider_id,))


def upsert_daily(conn, points: list[DailyPoint]) -> int:
    ensure_tables(conn)
    count = 0
    for point in points:
        conn.execute(
            """
            INSERT OR REPLACE INTO southbound_daily (
                trade_date, shares, pct_of_issued, hold_value, close_price,
                change_pct, share_change, source, provider_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                point.trade_date, point.shares, point.pct_of_issued, point.hold_value,
                point.close_price, point.change_pct, point.share_change, point.source,
                point.provider_id,
            ),
        )
        count += 1
    return count


def upsert_seats(conn, seats: list[SeatPoint]) -> int:
    ensure_tables(conn)
    for seat in seats:
        conn.execute(
            """
            INSERT OR REPLACE INTO southbound_seats (
                trade_date, participant_id, participant_name, shares,
                pct_of_issued, source, provider_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seat.trade_date, seat.participant_id, seat.participant_name, seat.shares,
                seat.pct_of_issued, seat.source, seat.provider_id,
            ),
        )
    return len(seats)


def upsert_holdings(conn, holdings: list[Holding]) -> int:
    ensure_tables(conn)
    for item in holdings:
        conn.execute(
            """
            INSERT OR REPLACE INTO southbound_holdings (
                holder_key, holder_name, holder_type, channel_tag, report_date, report_label,
                shares, raw_shares, raw_unit, pct_nav, pct_issued, position_type,
                market_value_wan, source, source_ref, provider_id, confidence,
                counts_toward_southbound, report_scope, unit_status, fund_type, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.holder_key, item.holder_name, item.holder_type, item.channel_tag,
                item.report_date, item.report_label, item.shares, item.raw_shares, item.raw_unit,
                item.pct_nav, item.pct_issued, item.position_type, item.market_value_wan,
                item.source, item.source_ref, item.provider_id, item.confidence,
                1 if item.counts_toward_southbound else 0, item.report_scope, item.unit_status,
                item.fund_type, item.notes,
            ),
        )
    return len(holdings)


def _daily_from_row(row) -> DailyPoint:
    return DailyPoint(
        trade_date=row["trade_date"],
        shares=row["shares"],
        pct_of_issued=row["pct_of_issued"],
        hold_value=row["hold_value"],
        close_price=row["close_price"],
        change_pct=row["change_pct"],
        share_change=row["share_change"],
        source=row["source"] or "",
        provider_id=row["provider_id"],
    )


def _holding_from_row(row) -> Holding:
    return Holding(
        holder_key=row["holder_key"],
        holder_name=row["holder_name"],
        holder_type=row["holder_type"],
        channel_tag=row["channel_tag"],
        report_date=row["report_date"],
        shares=row["shares"],
        raw_shares=row["raw_shares"] or 0,
        raw_unit=row["raw_unit"] or "shares",
        source=row["source"] or "",
        provider_id=row["provider_id"],
        confidence=row["confidence"] or 0,
        counts_toward_southbound=bool(row["counts_toward_southbound"]),
        report_label=row["report_label"] or "",
        pct_nav=row["pct_nav"],
        pct_issued=row["pct_issued"],
        position_type=row["position_type"] or "L",
        market_value_wan=row["market_value_wan"],
        source_ref=row["source_ref"] or "",
        report_scope=row["report_scope"] or "unknown",
        unit_status=row["unit_status"] or "unchecked",
        notes=row["notes"] or "",
        fund_type=row["fund_type"] or "",
    )


def load_bundle(conn) -> dict:
    ensure_tables(conn)
    daily = [
        _daily_from_row(row)
        for row in conn.execute("SELECT * FROM southbound_daily ORDER BY trade_date, provider_id")
    ]
    seats = [
        SeatPoint(
            trade_date=row["trade_date"],
            participant_id=row["participant_id"],
            participant_name=row["participant_name"] or "",
            shares=row["shares"],
            pct_of_issued=row["pct_of_issued"],
            source=row["source"] or "",
            provider_id=row["provider_id"],
        )
        for row in conn.execute("SELECT * FROM southbound_seats ORDER BY trade_date")
    ]
    holdings = [
        _holding_from_row(row)
        for row in conn.execute("SELECT * FROM southbound_holdings")
    ]
    return {"daily": daily, "seats": seats, "holdings": holdings}


def choose_history(daily: list[DailyPoint]) -> tuple[list[DailyPoint], list[DailyPoint]]:
    """最长的一条序列当南下合计；港交所官方点单独留下做交叉验证。"""
    official = [point for point in daily if point.provider_id == "hkex_mutualmarket"]
    history_pool = [point for point in daily if point.provider_id != "hkex_mutualmarket"]
    if not history_pool:
        history_pool = list(official)
        official = []
    by_provider: dict[str, list[DailyPoint]] = {}
    for point in history_pool:
        by_provider.setdefault(point.provider_id, []).append(point)
    if not by_provider:
        return [], official
    provider_id = max(by_provider, key=lambda key: len(by_provider[key]))
    series = sorted(by_provider[provider_id], key=lambda point: point.trade_date)
    return series, official


def panel_connect_seats(conn, trade_date: str | None) -> dict[str, int]:
    if not trade_date:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT participant_id, shares FROM ccass_participants
            WHERE trade_date = ? AND participant_id IN ('A00003', 'A00004')
            """,
            (trade_date,),
        ).fetchall()
    except Exception:
        return {}
    return {row["participant_id"]: row["shares"] for row in rows}


def volume_by_date(conn) -> dict[str, float]:
    try:
        rows = conn.execute(
            "SELECT trade_date, volume FROM daily_trading WHERE volume IS NOT NULL"
        ).fetchall()
    except Exception:
        return {}
    return {row["trade_date"]: row["volume"] for row in rows if row["volume"]}
