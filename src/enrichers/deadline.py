"""时效富化:根据投标截止日判断标的还能不能投。

PNCP 的 ``data_publicacao_pncp``(登记到中央门户的时间)可能**晚于**实际投标窗口
(机构追溯补登历史采购),所以判断"还能不能投"必须看 ``data_encerramento_proposta``
(投标截止),而不是发布日。

派生「时效状态」:
    expired       已过期(截止 < 今天)
    closing_soon  临近截止(今天 ≤ 截止 ≤ 今天+N 天,N 默认 5,可能来不及备标)
    open          还能投(截止 > 今天+N 天)
    open_ended    无截止(2099 哨兵值 / credenciamento 常年开放)
    unknown       截止日缺失

纯函数,不碰 DB / 网络。导出时调用,产出「时效状态 / 剩余天数」两列。
"""
from __future__ import annotations

from datetime import date, datetime

# 截止日哨兵:>= 此年份视为"无截止/常年开放"(PNCP 用 2099-12-31 表示)
_OPEN_ENDED_YEAR = 2099

STATUS_ZH = {
    "expired": "已过期",
    "closing_soon": "临近截止",
    "open": "还能投",
    "open_ended": "无截止",
    "unknown": "未知",
}


def _to_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def classify_deadline(
    encerramento: date | datetime | str | None,
    today: date,
    *,
    soon_days: int = 5,
) -> tuple[str, int | None]:
    """判断时效状态 + 剩余天数。

    Args:
        encerramento: 投标截止(``data_encerramento_proposta``)。
        today: 今天(显式传入,便于测试与避免散落 ``date.today()``)。
        soon_days: "临近截止"阈值(天)。

    Returns:
        ``(status, dias_restantes)``:status 见模块 docstring;dias_restantes 为
        距截止的天数(已过期为负,无截止/未知为 ``None``)。
    """
    de = _to_date(encerramento)
    if de is None:
        return "unknown", None
    if de.year >= _OPEN_ENDED_YEAR:
        return "open_ended", None
    dias = (de - today).days
    if dias < 0:
        return "expired", dias
    if dias <= soon_days:
        return "closing_soon", dias
    return "open", dias


def status_zh(status: str) -> str:
    """状态英文 key → 中文。"""
    return STATUS_ZH.get(status, status)


__all__ = ["classify_deadline", "status_zh", "STATUS_ZH"]
