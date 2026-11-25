"""持股单位。天天基金/AKShare 的「持股数」是万股，市值是万元人民币。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# 万元 / 万股 = 元/股。港股通持仓里的正股价格落在这个区间；
# 超出则多半是把万股当成了股，或把股当成了万股。
_PRICE_MIN = Decimal("0.05")
_PRICE_MAX = Decimal("20000")


def parse_number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace(" ", "")
    if text in {"", "-", "—", "None", "null", "NaN", "nan"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def wan_to_shares(wan: float) -> int:
    """万股 → 股，按四舍五入到整数股。用字符串避免二进制浮点误差。"""
    quantized = (Decimal(str(wan)) * Decimal(10000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantized)


def shares_to_count(raw: float, raw_unit: str) -> int:
    if raw_unit == "wan_shares":
        return wan_to_shares(raw)
    if raw_unit == "shares":
        return int((Decimal(str(raw))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    raise ValueError(f"unknown raw_unit {raw_unit}")


def implied_price_cny(raw_shares: float, raw_unit: str, market_value_wan: float) -> float | None:
    """按声明的单位，用持仓市值（万元）反推每股人民币价格。"""
    if raw_shares is None or raw_shares <= 0 or market_value_wan is None or market_value_wan <= 0:
        return None
    if raw_unit == "wan_shares":
        return float(Decimal(str(market_value_wan)) / Decimal(str(raw_shares)))
    if raw_unit == "shares":
        return float(Decimal(str(market_value_wan)) * Decimal(10000) / Decimal(str(raw_shares)))
    return None


def assess_unit(raw_shares: float | None, raw_unit: str, market_value_wan: float | None) -> str:
    """
    ok：声明单位和市值对得上。
    unchecked：没有市值，无法交叉验证，调用方仍可使用但应降低置信度。
    mismatch：隐含价格不合理，不能计入南下解释。
    invalid：股数不是正数。
    """
    if raw_shares is None or raw_shares <= 0:
        return "invalid"
    price = implied_price_cny(raw_shares, raw_unit, market_value_wan) if market_value_wan else None
    if price is None:
        return "unchecked"
    if _PRICE_MIN <= Decimal(str(price)) <= _PRICE_MAX:
        return "ok"
    return "mismatch"
