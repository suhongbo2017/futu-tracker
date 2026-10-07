# -*- coding: utf-8 -*-
"""飞书推送：每日收盘简报 + 异常告警。

Webhook 来自环境变量 `FEISHU_WEBHOOK`（不硬编码）。
若配置了 `FEISHU_SECRET`，按飞书自定义机器人签名规则加签。
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.request

from . import config


def _sign(timestamp, secret):
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_text(text, webhook=None, secret=None):
    """发送纯文本消息。返回 (ok, detail)。"""
    webhook = webhook or config.FEISHU_WEBHOOK
    secret = secret or config.FEISHU_SECRET
    if not webhook:
        return False, "未配置 FEISHU_WEBHOOK，已跳过推送"

    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(ts, secret)

    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="ignore")
        obj = json.loads(body) if body.strip().startswith("{") else {}
        ok = obj.get("code", 0) == 0 or obj.get("StatusCode", 0) == 0
        return ok, body[:300]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# 内容组装
# --------------------------------------------------------------------------
def build_daily_brief(date, rows, totals, conflicts=None, top_n=3):
    """rows: mark_to_market 的结果（含 code/name/qty/avg_cost/last/unrealized_pnl/…）"""
    held = [r for r in rows if r.get("qty")]
    held.sort(key=lambda r: (r.get("unrealized_pnl") or 0), reverse=True)

    def line(r):
        sign = "+" if (r.get("unrealized_pnl") or 0) >= 0 else ""
        last = r.get("last")
        pnl = r.get("unrealized_pnl")
        last_s = f"{last:,.2f}" if last is not None else "-"    # 无行情时显示 -
        pnl_s = f"{sign}{pnl:,.2f}" if pnl is not None else "-"
        return (f"{r.get('name') or r['code']}({r['code'].split('.')[-1]}) "
                f"{last_s} | 浮盈 {pnl_s} USD")

    parts = [f"📈 持仓1 收盘简报 {date}", ""]
    parts.append(f"市值 {totals['market_value']:,.2f} USD")
    parts.append(f"成本 {totals['cost_basis']:,.2f} USD")
    up = totals.get("unrealized_pnl") or 0.0
    pct = totals.get("unrealized_pct")
    parts.append(f"浮动盈亏 {'+' if up >= 0 else ''}{up:,.2f} USD"
                 + (f" ({pct:+.2f}%)" if pct is not None else ""))
    realized = totals.get("realized_pnl") or 0.0
    if abs(realized) > 0.005:
        parts.append(f"已实现盈亏 {'+' if realized >= 0 else ''}{realized:,.2f} USD")
        total = up + realized
        parts.append(f"总盈亏 {'+' if total >= 0 else ''}{total:,.2f} USD")
    if totals.get("day_change_pct") is not None:
        parts.append(f"较前一交易日 {totals['day_change_pct']:+.2f}%")
    parts.append("")
    parts.append(f"🟢 持仓最佳 {len(held[:top_n])} 只：")
    parts += ["  " + line(r) for r in held[:top_n]]
    parts.append(f"🔴 持仓最差 {len(held[-top_n:])} 只：")
    parts += ["  " + line(r) for r in held[-top_n:][::-1]]
    closed = [r for r in rows if not r.get("qty") and abs(r.get("realized_pnl") or 0) > 0.005]
    if closed:
        parts.append("")
        parts.append(f"💵 已清仓 {len(closed)} 只：")
        for r in sorted(closed, key=lambda x: x["realized_pnl"]):
            parts.append(f"  {r.get('name') or r['code']} 已实现 {r['realized_pnl']:+,.2f} USD")
    if conflicts:
        parts.append("")
        parts.append(f"⚠️ 数据源分歧 {len(conflicts)} 条（价差 >{config.CROSS_CHECK_TOLERANCE_PCT}%）")
    return "\n".join(parts)


def build_failure_alert(kind, message):
    return (f"🚨 futu-tracker 异常告警\n"
            f"任务: {kind}\n"
            f"详情: {message}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")