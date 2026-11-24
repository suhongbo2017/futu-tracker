# -*- coding: utf-8 -*-
"""价格提醒：评估 + 飞书推送。

设计要点：
- **一次性**：触发后 active 置 0，避免同一价位每 30 分钟刷屏；
- **纯函数评估**：`evaluate()` 不碰数据库、不发网络，便于单测；
- **失败不丢提醒**：推送失败时保持 active=1，下一轮采集会重试；
- **永不阻塞采集**：`check_and_notify()` 内部吞掉异常，只返回结果。
"""
import time

from . import config, db


def evaluate(alerts, prices):
    """返回本次应触发的提醒列表（纯函数，无副作用）。

    alerts: db.list_alerts(active_only=True) 的结果
    prices: {code: last_price}
    """
    fired = []
    for a in alerts:
        price = prices.get(a["code"])
        if price is None:
            continue
        target = a["target"]
        hit = (a["direction"] == "below" and price <= target) or \
              (a["direction"] == "above" and price >= target)
        if not hit:
            continue
        item = dict(a)
        item["price"] = float(price)
        item["distance_pct"] = (price / target - 1) * 100
        fired.append(item)
    return fired


def format_triggered(items, names=None):
    names = names or {}
    lines = ["🔔 价格提醒触发", ""]
    for a in items:
        name = names.get(a["code"]) or ""
        symbol = a["code"][3:] if a["code"].startswith("US.") else a["code"]
        label = f"{symbol} {name}".strip()
        op = "≤" if a["direction"] == "below" else "≥"
        lines.append(f"{label}")
        lines.append(f"现价 {a['price']:,.2f}（目标 {op} {a['target']:,.2f}，偏离 {a['distance_pct']:+.2f}%）")
        if a.get("note"):
            lines.append(f"备注：{a['note']}")
        lines.append("")
    lines.append(time.strftime("时间：%Y-%m-%d %H:%M"))
    return "\n".join(lines).rstrip()


def check_and_notify(conn, prices, send=None):
    """评估当前价并推送；返回 {'fired': [...], 'sent': bool, 'detail': str}。"""
    if not config.PRICE_ALERTS_ENABLED:
        return {"fired": [], "sent": False, "detail": "价格提醒已关闭（FUTU_PRICE_ALERTS=0）"}

    try:
        alerts = db.list_alerts(conn, active_only=True)
        fired = evaluate(alerts, prices)
    except Exception as e:                     # 提醒逻辑绝不拖垮采集
        return {"fired": [], "sent": False, "detail": f"评估失败 {type(e).__name__}: {e}"}
    if not fired:
        return {"fired": [], "sent": False, "detail": ""}

    names = {i["code"]: i["name"] for i in db.list_instruments(conn, active_only=False)}
    text = format_triggered(fired, names)
    sender = send or _default_send
    try:
        ok, detail = sender(text)
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"

    if ok:                                     # 只有推送成功才置为已触发（失败下轮重试）
        for a in fired:
            db.mark_alert_triggered(conn, a["id"], a["price"])
    return {"fired": fired, "sent": bool(ok), "detail": detail[:300]}


def _default_send(text):
    from . import notify
    return notify.send_text(text)


# --------------------------------------------------------------------------
# 日期提醒（财报/事件）
# --------------------------------------------------------------------------
def format_reminders(items, names=None):
    names = names or {}
    lines = ["⏰ 提醒", ""]
    for r in items:
        label = ""
        if r.get("code"):
            sym = r["code"][3:] if r["code"].startswith("US.") else r["code"]
            label = f"{sym} {names.get(r['code'], '')}".strip() + " · "
        lines.append(f"{label}{r['due_date']}")
        lines.append(r["note"])
        lines.append("")
    lines.append(time.strftime("时间：%Y-%m-%d %H:%M"))
    return "\n".join(lines).rstrip()


def check_reminders(conn, today=None, send=None):
    """推送今天到期（含逾期未发）的提醒；成功才标记已发。"""
    import datetime as _dt
    if not config.REMINDERS_ENABLED:
        return {"fired": [], "sent": False, "detail": "日期提醒已关闭（FUTU_REMINDERS=0）"}
    today = today or _dt.date.today().isoformat()
    try:
        items = db.due_reminders(conn, today)
    except Exception as e:
        return {"fired": [], "sent": False, "detail": f"查询失败 {type(e).__name__}: {e}"}
    if not items:
        return {"fired": [], "sent": False, "detail": ""}

    names = {i["code"]: i["name"] for i in db.list_instruments(conn, active_only=False)}
    text = format_reminders(items, names)
    sender = send or _default_send
    try:
        ok, detail = sender(text)
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    if ok:
        for r in items:
            db.mark_reminder_sent(conn, r["id"])
    return {"fired": items, "sent": bool(ok), "detail": detail[:300]}
