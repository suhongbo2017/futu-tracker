# -*- coding: utf-8 -*-
"""Web 界面 + 内置调度器（仅用标准库，零外部依赖）。

为什么不用 FastAPI：目标服务器 **pypi 不可达**（实测 8s 超时），
零依赖 = 零安装风险，个人单用户场景完全够用。

页面：
    /              持仓总览（汇总卡片 + 可排序明细表）
    /tx            记账（买入/卖出/分红/费用）+ 最近流水
    /history       回溯：查看任意历史日期的持仓与盈亏
    /api/positions JSON 接口

鉴权：HTTP Basic（密码来自 FUTU_PASSWORD；为空则拒绝启动）
"""
import base64
import datetime as dt
import hmac
import html
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db
from .accounting import compute_positions, mark_to_market, portfolio_totals
from .collector import (collect_history, collect_quotes, snapshot, market_closed,
                        us_session_date, is_trading_day, ny_now)
from . import notify

# --------------------------------------------------------------------------
# 样式（护眼配色：米绿底 + 低饱和）
# --------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#eef2e7;color:#3d4a3c;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.7;font-size:16px}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 60px}
header{background:linear-gradient(135deg,#dfe8d6,#d3e0cc);border:1px solid #cdd6c4;border-radius:14px;padding:18px 22px;margin-bottom:18px}
h1{font-size:24px;color:#4f6e4d}
.sub{color:#5b6b58;font-size:14px;margin-top:4px}
nav{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}
nav a{background:#f7f4ea;border:1px solid #cdd6c4;border-radius:999px;padding:5px 16px;color:#4f6e4d;text-decoration:none;font-size:14px}
nav a:hover{background:#e2e9d9}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:18px}
.card{background:#f7f4ea;border:1px solid #cdd6c4;border-radius:12px;padding:14px 16px}
.card .k{font-size:13px;color:#5b6b58}
.card .v{font-size:22px;font-weight:700;margin-top:2px}
.up{color:#c0392b}.down{color:#2f9e44}.flat{color:#868e96}
table{width:100%;border-collapse:collapse;background:#f7f4ea;border:1px solid #cdd6c4;border-radius:10px;overflow:hidden;font-size:15px}
th{background:#e2e9d9;color:#4f6e4d;text-align:left;padding:9px 10px;font-weight:600;white-space:nowrap}
td{padding:8px 10px;border-top:1px solid #dfe6d6;white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tr:hover td{background:#f2f6ec}
.msg{background:#e8f0dd;border:1px solid #bcd0ad;border-radius:10px;padding:10px 14px;margin-bottom:14px}
.err{background:#f8e3de;border:1px solid #e2b6ab;border-radius:10px;padding:10px 14px;margin-bottom:14px}
form{background:#f7f4ea;border:1px solid #cdd6c4;border-radius:12px;padding:16px;margin-bottom:18px;display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
label{display:block;font-size:13px;color:#5b6b58;margin-bottom:3px}
input,select{background:#fff;border:1px solid #cdd6c4;border-radius:8px;padding:7px 10px;font-size:15px;color:#3d4a3c}
button{background:#6d8f6a;color:#fff;border:0;border-radius:8px;padding:9px 20px;font-size:15px;cursor:pointer}
button:hover{background:#5b7d58}
button.mini{padding:4px 10px;font-size:13px;background:#a08152}
.small{font-size:13px;color:#5b6b58}
footer{margin-top:30px;text-align:center;color:#5b6b58;font-size:13px}
.tag{font-size:12px;background:#e2e9d9;color:#4f6e4d;border-radius:6px;padding:1px 7px}
"""


def _fmt(v, spec=",.2f", dash="-"):
    if v is None:
        return dash
    try:
        return format(v, spec)
    except Exception:
        return str(v)


def _cls(v):
    if v is None:
        return "flat"
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def layout(title, body, msg="", err=""):
    msgbox = f'<div class="msg">{html.escape(msg)}</div>' if msg else ""
    errbox = f'<div class="err">{html.escape(err)}</div>' if err else ""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · 持仓1</title><style>{CSS}</style></head><body><div class="wrap">
<header><h1>📈 持仓1 · 美股持仓管理</h1>
<div class="sub">{html.escape(title)}</div>
<nav><a href="/">总览</a><a href="/tx">记账</a><a href="/history">回溯</a><a href="/api/positions">API</a></nav>
</header>{msgbox}{errbox}{body}
<footer>futu-tracker · 数据源 腾讯/新浪 · 币种 USD · 红涨绿跌</footer></div></body></html>"""


# --------------------------------------------------------------------------
# 数据装配
# --------------------------------------------------------------------------
def current_view(conn, date=None):
    insts = {i["code"]: i for i in db.list_instruments(conn)}
    txns = db.all_transactions(conn, upto=(date + "T23:59:59") if date else None)
    positions = compute_positions(txns)
    prices, price_meta = {}, {}
    for code in positions:
        q = db.quote_on(conn, code, date) if date else db.latest_quote(conn, code)
        if q:
            prices[code] = q["close"]
            price_meta[code] = q
    rows = mark_to_market(positions, prices)
    out = []
    for code, r in rows.items():
        r["name"] = insts.get(code, {}).get("name", code)
        q = price_meta.get(code) or {}
        r["quote_date"] = q.get("date")
        r["is_final"] = q.get("is_final")
        r["source"] = q.get("source")
        r["has_price"] = code in prices
        out.append(r)
    out.sort(key=lambda r: (r.get("unrealized_pnl") or 0), reverse=True)
    totals = portfolio_totals(out)
    return out, totals


def page_index(conn, msg="", err=""):
    rows, totals = current_view(conn)
    latest = max([r["quote_date"] for r in rows if r.get("quote_date")], default="-")
    prov = any((r.get("is_final") == 0) for r in rows)
    cards = f"""<div class="cards">
<div class="card"><div class="k">持仓市值</div><div class="v">{_fmt(totals['market_value'])} <span class="small">USD</span></div></div>
<div class="card"><div class="k">持仓成本</div><div class="v">{_fmt(totals['cost_basis'])} <span class="small">USD</span></div></div>
<div class="card"><div class="k">浮动盈亏</div><div class="v {_cls(totals['unrealized_pnl'])}">{'+' if (totals['unrealized_pnl'] or 0)>=0 else ''}{_fmt(totals['unrealized_pnl'])} <span class="small">{_fmt(totals['unrealized_pct'],'+.2f')}%</span></div></div>
<div class="card"><div class="k">已实现盈亏</div><div class="v {_cls(totals['realized_pnl'])}">{'+' if (totals['realized_pnl'] or 0)>=0 else ''}{_fmt(totals['realized_pnl'])} <span class="small">USD</span></div></div>
<div class="card"><div class="k">行情日期</div><div class="v">{html.escape(str(latest))} <span class="small">{'盘中暂定' if prov else '收盘正式'}</span></div></div></div>"""

    body = ['<table><thead><tr>'
            '<th>代码</th><th>名称</th><th>现价</th><th>数量</th><th>均价</th>'
            '<th>市值</th><th>浮动盈亏</th><th>盈亏%</th><th>已实现</th><th>行情日</th>'
            '</tr></thead><tbody>']
    for r in rows:
        if not r["qty"]:
            continue
        pnl = r.get("unrealized_pnl")
        body.append(
            "<tr>"
            f'<td>{html.escape(r["code"])}</td><td>{html.escape(r["name"])}</td>'
            f'<td class="num">{_fmt(r.get("last"))}</td>'
            f'<td class="num">{_fmt(r.get("qty"), ",.0f" if float(r.get("qty") or 0).is_integer() else ",.4f")}</td>'
            f'<td class="num">{_fmt(r.get("avg_cost"), ",.3f")}</td>'
            f'<td class="num">{_fmt(r.get("market_value"))}</td>'
            f'<td class="num {_cls(pnl)}">{"+" if (pnl or 0)>=0 else ""}{_fmt(pnl)}</td>'
            f'<td class="num {_cls(pnl)}">{_fmt(r.get("unrealized_pct"), "+.2f")}%</td>'
            f'<td class="num {_cls(r.get("realized_pnl"))}">{_fmt(r.get("realized_pnl"))}</td>'
            f'<td class="small">{html.escape(str(r.get("quote_date") or "-"))}</td>'
            "</tr>")
    body.append("</tbody></table>")

    runs = list(conn.execute("SELECT kind,status,started_at,message FROM runs ORDER BY id DESC LIMIT 6"))
    body.append('<h3 style="margin:22px 0 8px;color:#4f6e4d;font-size:17px">最近任务</h3><table><thead><tr>'
                '<th>任务</th><th>状态</th><th>时间</th><th>说明</th></tr></thead><tbody>')
    for r in runs:
        body.append(f"<tr><td>{html.escape(r['kind'])}</td><td>{html.escape(r['status'])}</td>"
                    f"<td class='small'>{html.escape(r['started_at'])}</td>"
                    f"<td class='small'>{html.escape((r['message'] or '')[:80])}</td></tr>")
    body.append("</tbody></table>")
    return layout("总览", cards + "".join(body), msg, err)


def page_tx(conn, msg="", err=""):
    insts = db.list_instruments(conn)
    opts = "".join(f'<option value="{html.escape(i["code"])}">{html.escape(i["code"])} · {html.escape(i["name"])}</option>' for i in insts)
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    form = f"""<form method="post" action="/tx">
<div><label>标的</label><input name="code" list="codes" placeholder="如 NVDA / US.AAPL" required style="width:200px">
<datalist id="codes">{opts}</datalist><span class="small">可输入已有代码或新代码</span></div>
<div><label>方向</label><select name="side">
  <option value="BUY">买入 BUY</option><option value="SELL">卖出 SELL</option>
  <option value="DIV">分红 DIV</option><option value="FEE">费用 FEE</option>
  <option value="ADJ">拆合股 ADJ</option></select></div>
<div><label>价格 / 金额</label><input name="price" type="number" step="any" required style="width:120px"></div>
<div><label>股数</label><input name="qty" type="number" step="any" value="0" style="width:110px"></div>
<div><label>手续费</label><input name="fee" type="number" step="any" value="0" style="width:100px"></div>
<div><label>成交时间</label><input name="trade_time" value="{now}" style="width:190px"></div>
<div><label>备注</label><input name="note" style="width:180px"></div>
<button type="submit">记一笔</button>
</form>"""

    txns = list(conn.execute(
        "SELECT t.*, i.name FROM transactions t LEFT JOIN instruments i ON i.code=t.code "
        "ORDER BY t.trade_time DESC, t.id DESC LIMIT 40"))
    body = [form, '<table><thead><tr><th>时间</th><th>标的</th><th>方向</th><th>价格</th><th>股数</th>'
                '<th>手续费</th><th>备注</th><th>操作</th></tr></thead><tbody>']
    for t in txns:
        body.append(
            f"<tr><td class='small'>{html.escape(t['trade_time'])}</td>"
            f"<td>{html.escape(t['code'])} {html.escape(t['name'] or '')}</td>"
            f"<td><span class='tag'>{html.escape(t['side'])}</span></td>"
            f"<td class='num'>{_fmt(t['price'], ',.4f')}</td>"
            f"<td class='num'>{_fmt(t['qty'], ',.4f')}</td>"
            f"<td class='num'>{_fmt(t['fee'], ',.2f')}</td>"
            f"<td class='small'>{html.escape((t['note'] or '')[:40])}</td>"
            f"<td><form method='post' action='/tx/reverse' style='padding:0;border:0;background:none;display:inline'>"
            f"<input type='hidden' name='id' value='{t['id']}'>"
            f"<button class='mini' type='submit'>冲正</button></form></td></tr>")
    body.append("</tbody></table>")
    return layout("记账", "".join(body), msg, err)


def page_history(conn, date=None, msg="", err=""):
    date = date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    rows, totals = current_view(conn, date=date)
    held = [r for r in rows if r["qty"]]
    missing = [r for r in held if not r.get("has_price")]
    if missing and not any(r.get("has_price") for r in held):
        err = err or (f"该日期（{date}）及其之前没有可用行情，无法计算市值。"
                      "请先执行历史回填：python -m app.collector --history")
    elif missing:
        err = err or f"有 {len(missing)} 只标的在该日期前无行情，这些行以 '-' 显示。"

    body = [f"""<form method="get" action="/history">
<div><label>查看日期（含当日之前的所有流水与价格）</label>
<input name="date" value="{html.escape(date)}" style="width:160px"></div>
<button type="submit">回溯</button></form>"""]
    later_rev = conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE reverses_id IS NOT NULL AND trade_time > ?",
        (date + "T23:59:59",)).fetchone()["c"]
    if later_rev:
        body.append(f'<p class="small" style="color:#a08152">⚠️ 该日之后存在 {later_rev} 笔冲正流水，'
                    '被冲正的交易在回溯口径可能复现，请以当日快照为准。</p>')
    body.append(f"""<div class="cards">
<div class="card"><div class="k">该日市值</div><div class="v">{_fmt(totals['market_value'])}</div></div>
<div class="card"><div class="k">该日成本</div><div class="v">{_fmt(totals['cost_basis'])}</div></div>
<div class="card"><div class="k">该日浮动盈亏</div><div class="v {_cls(totals['unrealized_pnl'])}">{_fmt(totals['unrealized_pnl'])}</div></div>
<div class="card"><div class="k">累计已实现</div><div class="v {_cls(totals['realized_pnl'])}">{_fmt(totals['realized_pnl'])}</div></div></div>""")
    body.append('<table><thead><tr><th>代码</th><th>名称</th><th>当日价</th><th>数量</th><th>均价</th>'
                '<th>市值</th><th>浮动盈亏</th><th>价格日期</th></tr></thead><tbody>')
    for r in held:
        last_s = _fmt(r.get("last")) if r.get("has_price") else "-"
        mv_s = _fmt(r.get("market_value")) if r.get("has_price") else "-"
        pnl_s = _fmt(r.get("unrealized_pnl")) if r.get("has_price") else "-"
        pnl_cls = _cls(r.get("unrealized_pnl")) if r.get("has_price") else "flat"
        body.append(f"<tr><td>{html.escape(r['code'])}</td><td>{html.escape(r['name'])}</td>"
                    f"<td class='num'>{last_s}</td>"
                    f"<td class='num'>{_fmt(r.get('qty'), ',.0f')}</td>"
                    f"<td class='num'>{_fmt(r.get('avg_cost'), ',.3f')}</td>"
                    f"<td class='num'>{mv_s}</td>"
                    f"<td class='num {pnl_cls}'>{pnl_s}</td>"
                    f"<td class='small'>{html.escape(str(r.get('quote_date') or '-'))}</td></tr>")
    body.append("</tbody></table>")
    body.append('<p class="small" style="margin-top:10px">回填更早历史：'
                '<code>python -m app.collector --history</code>（新浪日K，全历史）</p>')
    return layout(f"回溯 · {date}", "".join(body), msg, err)


# --------------------------------------------------------------------------
# 输入规范化与校验（供 /tx 录入与测试复用）
# --------------------------------------------------------------------------
def normalize_code(raw):
    """标的代码规范化：去空白、大写；统一补 US. 前缀（US.AAPL / AAPL / brk.b 均归一）。
    含非法字符返回 None。"""
    code = (raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.]*", code):
        return None
    return code if code.startswith("US.") else "US." + code


def _available_qty(conn, code, trade_time):
    """截至某成交时间（含）之前的可用持仓，用于拒绝超卖（本系统不支持卖空）。"""
    t = trade_time or dt.datetime.now().isoformat(timespec="seconds")
    txns = db.all_transactions(conn, code=code)
    upto = [x for x in txns if (x["trade_time"], x["id"]) < (t, float("inf"))]
    return compute_positions(upto).get(code, {"qty": 0.0})["qty"]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "futu-tracker/0.1"

    def log_message(self, fmt, *args):
        pass                                    # 静默，避免刷日志

    # ---- 工具 ----
    def _authed(self):
        if not config.AUTH_PASSWORD:
            return False
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            u, p = base64.b64decode(hdr[6:]).decode("utf-8").split(":", 1)
        except Exception:
            return False
        return u == config.AUTH_USER and p == config.AUTH_PASSWORD

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="futu-tracker"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to, msg=""):
        if msg:
            to += ("&" if "?" in to else "?") + "msg=" + urllib.parse.quote(msg)
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 路由 ----
    def do_GET(self):
        if not self._authed():
            return self._deny()
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        msg = (q.get("msg") or [""])[0]
        conn = db.connect()
        try:
            if u.path in ("/", "/index"):
                return self._send(page_index(conn, msg=msg))
            if u.path == "/tx":
                return self._send(page_tx(conn, msg=msg))
            if u.path == "/history":
                return self._send(page_history(conn, (q.get("date") or [None])[0], msg=msg))
            if u.path == "/api/positions":
                rows, totals = current_view(conn)
                return self._send(json.dumps({"totals": totals, "positions": rows},
                                             ensure_ascii=False, default=str),
                                  ctype="application/json; charset=utf-8")
            self._send(layout("未找到", "<p>404</p>"), code=404)
        finally:
            conn.close()

    def do_POST(self):
        if not self._authed():
            return self._deny()
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65536:
                return self._send(layout("拒绝", "<p>请求体过大</p>"), code=413)
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._send(layout("无效请求", "<p>请求体解析失败</p>"), code=400)
        g = lambda k, d="": (form.get(k) or [d])[0].strip()
        conn = db.connect()
        try:
            if self.path == "/tx":
                code = normalize_code(g("code"))
                if code is None:
                    return self._redirect("/tx", "❌ 标的代码格式无效（字母/数字，如 US.AAPL）")
                side = g("side").upper()
                try:
                    price = float(g("price") or 0)
                    qty = float(g("qty") or 0)
                    fee = float(g("fee") or 0)
                except ValueError:
                    return self._redirect("/tx", "❌ 价格/股数/手续费必须是数字")
                if not code or side not in ("BUY", "SELL", "DIV", "FEE", "ADJ"):
                    return self._redirect("/tx", "❌ 标的或方向无效")
                if side in ("BUY", "SELL") and (price <= 0 or qty <= 0):
                    return self._redirect("/tx", "❌ 买入/卖出必须填写价格与股数")
                if side in ("DIV", "FEE") and price < 0:
                    return self._redirect("/tx", f"❌ {side} 金额不能为负")
                if side == "ADJ" and qty == 0:
                    return self._redirect("/tx", "❌ 拆合股股数变化不能为 0")
                trade_time = g("trade_time") or None
                if trade_time and not re.match(
                        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$", trade_time):
                    return self._redirect("/tx", "❌ 成交时间格式应为 YYYY-MM-DDTHH:MM")
                if side == "SELL":
                    avail = _available_qty(conn, code, trade_time)
                    if qty > avail + 1e-9:
                        return self._redirect("/tx",
                            f"❌ 卖出 {qty:g} 股超过当前持仓 {avail:g} 股（不支持卖空）")
                try:
                    with db.tx(conn):
                        db.ensure_instrument(conn, code)     # 新代码自动注册，避免外键失败
                        db.insert_transaction(conn, code, side, price, qty, fee,
                                              trade_time=trade_time,
                                              note=g("note") or None, source="web")
                except Exception as e:
                    return self._redirect("/tx", f"❌ 写入失败：{e}")
                return self._redirect("/tx", f"✅ 已记录 {side} {code}")

            if self.path == "/tx/reverse":
                tid = g("id")
                if not tid.isdigit():
                    return self._redirect("/tx", "❌ 无效的流水号")
                orig = conn.execute("SELECT * FROM transactions WHERE id=?", (int(tid),)).fetchone()
                if not orig:
                    return self._redirect("/tx", "❌ 流水不存在")
                if conn.execute("SELECT 1 FROM transactions WHERE reverses_id=?",
                                (int(tid),)).fetchone():
                    return self._redirect("/tx", f"❌ 流水 #{tid} 已被冲正，不可重复冲正")
                with db.tx(conn):
                    conn.execute(
                        """INSERT INTO transactions(code, side, price, qty, fee, trade_time, note, source, reverses_id)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (orig["code"], orig["side"], 0, 0, 0,
                         dt.datetime.now().isoformat(timespec="seconds"),
                         f"冲正 #{tid}", "web", int(tid)))
                return self._redirect("/tx", f"✅ 已冲正流水 #{tid}")

            self._send(layout("未找到", "<p>404</p>"), code=404)
        finally:
            conn.close()


# --------------------------------------------------------------------------
# 内置调度器（采集 + 收盘简报）
# --------------------------------------------------------------------------
def scheduler_loop(stop_evt):
    last_quote_min = -1
    brief_sent_for = None
    brief_retry_slot = -1
    backup_done_for = None
    while not stop_evt.is_set():
        try:
            now = dt.datetime.now()
            conn = db.connect()
            try:
                # 每 30 分钟采集一次：仅美股交易日且尚未收盘。
                # 修复：原 `now.hour % 1 == 0` 恒真，导致周末/休市也每轮采集并
                # 以 partial 触发飞书告警刷屏。
                ny = ny_now()
                if is_trading_day(ny.date().isoformat()) \
                        and not market_closed(ny.date().isoformat()) \
                        and ny.minute // 30 != last_quote_min:
                    last_quote_min = ny.minute // 30
                    r = collect_quotes(conn)
                    print("[sched] quotes:", r.get("message"), flush=True)
                    if r.get("status") != "ok" and config.ALERT_ON_FAILURE:
                        notify.send_text(notify.build_failure_alert("collect_quotes", r.get("message", "")))
                # 每日备份（账本不可再生）：简报前一小时
                if now.hour == max(config.BRIEF_HOUR - 1, 0) and now.minute >= 30 \
                        and backup_done_for != now.date():
                    backup_done_for = now.date()
                    try:
                        info = db.backup(conn=conn, keep=config.BACKUP_KEEP)
                        print("[sched] backup:", info["path"], info["size"], "B",
                              "removed=", len(info["removed"]), flush=True)
                    except Exception as e:
                        print("[sched] backup failed:", e, flush=True)
                        if config.ALERT_ON_FAILURE:
                            notify.send_text(notify.build_failure_alert("backup", str(e)))
                # 每日简报（收盘后）：先刷新行情再快照推送；成功才置位当天已发送，
                # 失败每 10 分钟重试一次并告警（修复"拼装异常即当天静默丢失"）
                if now.hour == config.BRIEF_HOUR and now.minute >= config.BRIEF_MINUTE \
                        and brief_sent_for != now.date() \
                        and now.minute // 10 != brief_retry_slot:
                    brief_retry_slot = now.minute // 10
                    try:
                        try:                       # 简报前先刷新行情，确保用收盘价
                            collect_quotes(conn)
                        except Exception as e:
                            print("[sched] brief pre-fetch failed:", e, flush=True)
                        totals = snapshot(conn)
                        rows, _ = current_view(conn)
                        text = notify.build_daily_brief(totals.get("date", str(now.date())), rows, totals)
                        ok, detail = notify.send_text(text)
                    except Exception as e:         # 拼装/推送异常不静默放过
                        ok, detail = False, f"{type(e).__name__}: {e}"
                    print("[sched] brief:", ok, detail[:120], flush=True)
                    if ok:
                        brief_sent_for = now.date()
                    elif config.ALERT_ON_FAILURE:
                        notify.send_text(notify.build_failure_alert("daily_brief", detail))
            finally:
                conn.close()
        except Exception as e:
            print("[sched] error:", type(e).__name__, e, flush=True)
        stop_evt.wait(30)


def main():
    if not config.AUTH_PASSWORD:
        raise SystemExit("拒绝启动：必须设置 FUTU_PASSWORD（避免无鉴权公网暴露）")
    conn = db.connect()
    db.init_db(conn)
    conn.close()

    stop_evt = threading.Event()
    threading.Thread(target=scheduler_loop, args=(stop_evt,), daemon=True).start()

    srv = ThreadingHTTPServer((config.APP_HOST, config.APP_PORT), Handler)
    print(f"futu-tracker 已启动: http://{config.APP_HOST}:{config.APP_PORT}  (DB={config.DB_PATH})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        srv.server_close()


if __name__ == "__main__":
    main()