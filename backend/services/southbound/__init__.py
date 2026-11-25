"""南下资金拆解。数据源通过 provider 注入，计算不绑定某一家网站。"""

from .service import build_view, collect_stock

__all__ = ["build_view", "collect_stock"]
