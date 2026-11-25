"""
渠道标签。

只有「确认经港股通」计入南下解释。
「可能经港股通」单独列出，不并进覆盖率分子。
「非港股通」包括 QDII、海外股票型 ETF、外资权益披露和控股架构，不能拿来解释 A00003+A00004。
"""
from __future__ import annotations

import re

from .models import CONFIRMED, NON_CONNECT, POSSIBLE

# 这些名字出现在权益披露里时，持仓走自有托管人，不是中登的两个港股通席位。
_FOREIGN_MARKERS = (
    "blackrock", "vanguard", "jpmorgan", "jp morgan", "j.p. morgan", "hsbc",
    "citigroup", "citi ", "ubs", "goldman", "morgan stanley", "deutsche bank",
    "allianz", "state street", "fidelity", "schroder", "invesco", "norges",
    "temasek", "gic private", "baillie", "aberdeen", "bnp paribas", "societe generale",
    "bank of new york", "northern trust", "mitsubishi ufj", "capital group",
    "t. rowe", "wellington", "legal & general", "point72", "citadel",
)

# 内地险资/社保可能走港股通，也可能走 QDII。权益披露不写渠道，只标「可能」。
_MAINLAND_INSTITUTION_MARKERS = (
    "全国社会保障", "社会保障基金", "社保基金", "中央汇金", "证金公司",
    "中国人寿", "中国人保", "中国太保", "新华人寿", "新华保险", "泰康",
    "太平保险", "阳光保险", "中国平安", "中国人民保险",
)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip()


def classify_fund(name: str, fund_type: str = "") -> tuple[str, float, str]:
    name = _clean(name)
    fund_type = fund_type or ""
    if "QDII" in name or "合格境内" in name or "QDII" in fund_type:
        return (
            NON_CONNECT,
            0.9,
            "QDII 通过境外直接投资，不经过港股通席位 A00003/A00004",
        )
    if "港股通" in name:
        return (
            CONFIRMED,
            0.88,
            "产品名称含「港股通」，港股持仓走沪港通/深港通",
        )
    if "海外" in fund_type:
        return (
            NON_CONNECT,
            0.8,
            "基金类型为海外股票口径（通常是 QDII ETF），默认不计入港股通",
        )
    if "沪港深" in name:
        return (
            POSSIBLE,
            0.55,
            "沪港深基金的港股部分多经港股通，但名称不能单独证明托管路径，不计入已确认南下",
        )
    if any(token in name for token in ("恒生", "港股", "中概", "互联网")):
        return (
            POSSIBLE,
            0.45,
            "名称与港股相关，但未标明港股通，渠道不确定，不计入已确认南下",
        )
    return (
        POSSIBLE,
        0.35,
        "未标明港股通或 QDII，默认不计入已确认南下",
    )


def classify_disclosure(name: str) -> tuple[str, float, str]:
    folded = _clean(name).lower()
    if any(marker in folded for marker in _FOREIGN_MARKERS):
        return (
            NON_CONNECT,
            0.92,
            "外资机构经自有托管席位持有，不是中登港股通席位；权益百分比含衍生工具，禁止加总",
        )
    raw = _clean(name)
    if any(marker in raw for marker in _MAINLAND_INSTITUTION_MARKERS):
        return (
            POSSIBLE,
            0.4,
            "内地机构可能经港股通或 QDII，权益披露不写渠道，不计入已确认南下；百分比禁止与其他股东加总",
        )
    return (
        NON_CONNECT,
        0.7,
        "权益披露不区分港股通。未能识别为内地港股通路径，默认不计入南下解释；权益含受控法团与衍生工具，禁止加总",
    )
