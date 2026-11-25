"""
南下相关页面的抓取。

这里只负责 HTTP。解析在 services/southbound。换持牌 CSV 时不要走这个文件。
港交所 CCASS/港股通页面条款限制个人非商业使用，调用方必须自己判断能不能抓。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from scrapers.anti_ban import (
    make_eastmoney_client,
    make_eastmoney_fund_client,
    make_hkex_di_client,
    make_hkex_mutualmarket_client,
)

logger = logging.getLogger(__name__)

_EASTMONEY = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_FUND = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
_DI = "https://di.hkex.com.hk/di/summary/DSM{day}{kind}.htm"
_MUTUAL = "https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk"


def fetch_eastmoney_daily(stock_code: str) -> list[dict]:
    """个股南下合计。pageSize 500，按返回页数翻页。"""
    client = make_eastmoney_client()
    code = stock_code.zfill(5)
    rows: list[dict] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        params = {
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": str(page),
            "reportName": "RPT_MUTUAL_STOCK_HOLDRANKS",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECUCODE="{code}.HK")(MUTUAL_TYPE="002")',
        }
        response = client.get(_EASTMONEY, params=params)
        payload = response.json()
        result = payload.get("result") or {}
        batch = result.get("data") or []
        rows.extend(batch)
        total_pages = int(result.get("pages") or 1)
        if not batch:
            break
        page += 1
    return rows


def fetch_eastmoney_seats(stock_code: str, hold_date: str) -> list[dict]:
    client = make_eastmoney_client()
    code = stock_code.zfill(5)
    params = {
        "sortColumns": "HOLD_DATE",
        "sortTypes": "-1",
        "pageSize": "20",
        "pageNumber": "1",
        "reportName": "RPT_MUTUAL_HOLD_DET",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(SECURITY_CODE=\"{code}\")(HOLD_DATE='{hold_date}')",
    }
    response = client.get(_EASTMONEY, params=params)
    result = (response.json().get("result") or {})
    return result.get("data") or []


def fetch_fund_archive(fund_code: str, year: str) -> str:
    client = make_eastmoney_fund_client()
    # Referer 必须带这只基金，否则天天基金返回 404。
    response = client.get(
        _FUND,
        params={"type": "jjcc", "code": fund_code, "topline": "100", "year": year, "month": ""},
        headers={
            "Referer": f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    return response.text


def fetch_di_summary(day: date, kind: str = "C1") -> str:
    client = make_hkex_di_client()
    url = _DI.format(day=day.strftime("%Y%m%d"), kind=kind)
    response = client.get(url)
    return response.text


def fetch_mutualmarket() -> str:
    """只拉页面默认的最近一个持股日，不回放历史。"""
    client = make_hkex_mutualmarket_client()
    response = client.get(_MUTUAL)
    return response.text


def recent_weekdays(n: int, today: date | None = None) -> list[date]:
    today = today or date.today()
    days = []
    cursor = today
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return days
