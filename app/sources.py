# -*- coding: utf-8 -*-
"""行情数据源层。

实测结论（2026-09-29，阿里云大陆节点）：
  * 腾讯 qt.gtimg.cn       批量、200、~124ms、含币种     → 主源
  * 新浪 hq.sinajs.cn      批量、200、~114ms、需 Referer → 备源/交叉校验
  * 新浪 US_MinKService.getDailyK  全历史日K（未复权）   → 历史源
  * 东方财富              突发请求即被 WAF 拦（curl 52）  → 仅极低频兜底
  * Yahoo 不通 · Stooq 被 JS 反爬拦截 · 腾讯美股日K只有2根

字段索引均以「锚点定位」解析，避免空字段造成的位移错误。
"""
import json
import re
import time
import urllib.request

from . import config

UA = {"User-Agent": "Mozilla/5.0 (futu-tracker)"}
_last_request = [0.0]


import math


class SourceError(RuntimeError):
    pass


def _get(url, headers=None, encoding="gbk"):
    """带最小间隔与超时的 GET。"""
    gap = config.HTTP_MIN_INTERVAL - (time.time() - _last_request[0])
    if gap > 0:
        time.sleep(gap)
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            raw = r.read()
    except Exception as e:
        raise SourceError(f"请求失败 {url}: {type(e).__name__}: {e}") from e
    finally:
        _last_request[0] = time.time()
    return raw.decode(encoding, errors="ignore")


def to_symbol(code):
    """内部代码 -> 交易所符号：US.AAPL -> AAPL；US.BRK.B -> BRK.B（仅去前缀，不取尾段）。"""
    c = (code or "").strip().upper()
    return c[3:] if c.startswith("US.") else c


def _canonical_symbol(raw, requested):
    """将行情源的符号键映射回请求符号，兼容 BRK.B / BRK_B。"""
    symbol = (raw or "").strip().upper()
    if symbol in requested:
        return symbol
    dotted = symbol.replace("_", ".")
    return dotted if dotted in requested else symbol


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None        # 拒绝 NaN/Inf 污染行情库


# --------------------------------------------------------------------------
# 腾讯（主源，批量）
# --------------------------------------------------------------------------
def fetch_tencent(codes):
    """返回 {symbol: {...}}。字段：4=现价 5=昨收 6=开盘；锚点(时间)后 +1=涨跌额 +2=涨跌% +3=最高 +4=最低 +5=币种"""
    syms = [to_symbol(c) for c in codes]
    requested = set(syms)
    url = "https://qt.gtimg.cn/q=" + ",".join("us" + s for s in syms)
    text = _get(url)
    out = {}
    for m in re.finditer(r'v_us([A-Za-z0-9_.]+)="([^"]*)"', text):
        sym, body = _canonical_symbol(m.group(1), requested), m.group(2)
        f = body.split("~")
        anchor = next((i for i, v in enumerate(f)
                       if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", v)), None)
        if anchor is None or _f(f[3]) is None:
            continue
        out[sym] = {
            "symbol": sym,
            "name": f[1],
            "last": _f(f[3]),
            "prev_close": _f(f[4]),
            "open": _f(f[5]),
            "volume": _f(f[6]),
            "change": _f(f[anchor + 1]),
            "change_pct": _f(f[anchor + 2]),
            "high": _f(f[anchor + 3]),
            "low": _f(f[anchor + 4]),
            "currency": f[anchor + 5],
            "amount": _f(f[anchor + 7]),
            "quote_time": f[anchor],
            "source": "tencent",
        }
    if not out:
        raise SourceError("腾讯返回为空或格式变更")
    return out


# --------------------------------------------------------------------------
# 新浪（备源 / 交叉校验，批量）
# --------------------------------------------------------------------------
def fetch_sina(codes):
    """字段：1=名称 2=现价 3=涨跌% 4=时间 5=涨跌额 6=开盘 7=最高 8=最低
    锚点(EDT/EST 天文时间)后 +1 = 昨收"""
    syms = [to_symbol(c) for c in codes]
    requested = set(syms)
    url = "https://hq.sinajs.cn/list=" + ",".join("gb_" + s.lower() for s in syms)
    text = _get(url, headers={"Referer": "https://finance.sina.com.cn"})
    out = {}
    for m in re.finditer(r'var hq_str_gb_([A-Za-z0-9_.]+)="([^"]*)"', text):
        sym, body = _canonical_symbol(m.group(1), requested), m.group(2)
        f = body.split(",")
        if _f(f[1]) is None:
            continue
        anchor = next((i for i, v in enumerate(f) if re.search(r"EDT|EST", v)), None)
        out[sym] = {
            "symbol": sym,
            "name": f[0],
            "last": _f(f[1]),
            "change_pct": _f(f[2]),
            "quote_time": f[3],
            "change": _f(f[4]),
            "open": _f(f[5]),
            "high": _f(f[6]),
            "low": _f(f[7]),
            "volume": _f(f[10]),
            "prev_close": _f(f[anchor + 1]) if anchor is not None else None,
            "currency": "USD",
            "source": "sina",
        }
    if not out:
        raise SourceError("新浪返回为空或被限流")
    return out


# --------------------------------------------------------------------------
# 历史日K（新浪，未复权；返回全部历史，取尾部 N 根）
# --------------------------------------------------------------------------
def fetch_sina_history(code, bars=None):
    bars = bars or config.HISTORY_BARS
    sym = to_symbol(code)
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/x/"
           f"US_MinKService.getDailyK?symbol={sym}&___qn=1")
    text = _get(url, headers={"Referer": "https://finance.sina.com.cn"}, encoding="utf-8")
    m = re.search(r"\((\[.*\])\)", text, re.S)
    if not m:
        raise SourceError(f"新浪历史K解析失败: {sym}")
    data = json.loads(m.group(1))
    rows = []
    for r in data[-bars:]:
        rows.append({
            "date": r.get("d"),
            "open": _f(r.get("o")),
            "high": _f(r.get("h")),
            "low": _f(r.get("l")),
            "close": _f(r.get("c")),
            "volume": _f(r.get("v")),
            "source": "sina",
        })
    return rows


# --------------------------------------------------------------------------
# 东方财富（极低频兜底，可能被 WAF 拦）
# --------------------------------------------------------------------------
def fetch_eastmoney(codes):
    secids = ",".join(_secid(c) for c in codes)
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get"
           f"?secids={secids}&fields=f2,f3,f4,f12,f13,f14,f18&fltt=2")
    text = _get(url, encoding="utf-8")
    obj = json.loads(text)
    out = {}
    for d in (obj.get("data") or {}).get("diff") or []:
        sym = (d.get("f12") or "").upper()
        out[sym] = {
            "symbol": sym,
            "name": d.get("f14"),
            "last": _f(d.get("f2")),
            "change_pct": _f(d.get("f3")),
            "change": _f(d.get("f4")),
            "prev_close": _f(d.get("f18")),
            "currency": "USD",
            "source": "eastmoney",
        }
    if not out:
        raise SourceError("东财返回为空（可能被限流）")
    return out


def _secid(code):
    """东财 secid：105=NASDAQ 106=NYSE 107=AMEX（映射来自 searchapi 实测）"""
    return _SECID_CACHE.get(to_symbol(code)) or f"105.{to_symbol(code)}"


_SECID_CACHE = {
    "NFLX": "105.NFLX", "SKHY": "105.SKHY", "QCOM": "105.QCOM", "ORCL": "106.ORCL",
    "SPCX": "105.SPCX", "NOK": "106.NOK", "NVTS": "105.NVTS", "RKLB": "105.RKLB",
    "OKLO": "106.OKLO", "KTOS": "105.KTOS", "MSFT": "105.MSFT", "NVDA": "105.NVDA",
    "GOOGL": "105.GOOGL", "ASX": "106.ASX",
}


# --------------------------------------------------------------------------
# 组合：多源取数 + 交叉校验
# --------------------------------------------------------------------------
def fetch_quotes(codes):
    """主源腾讯；备源新浪；返回 (quotes, conflicts, sources_used)

    quotes: {symbol: {...}}；conflicts: [ {...} ] 价差超阈值
    任一源失败不致命——有结果就返回，并在 runs 里体现 partial。
    """
    quotes, conflicts, used = {}, [], []
    try:
        quotes.update(fetch_tencent(codes)); used.append("tencent")
    except SourceError:
        pass
    try:
        sina = fetch_sina(codes)
        used.append("sina")
        for sym, s in sina.items():
            if sym not in quotes:
                quotes[sym] = s
            else:
                a, b = quotes[sym].get("last"), s.get("last")
                if a and b and b != 0:
                    diff = abs(a - b) / b * 100
                    if diff > config.CROSS_CHECK_TOLERANCE_PCT:
                        conflicts.append({
                            "symbol": sym,
                            "price_a": a, "source_a": quotes[sym]["source"],
                            "price_b": b, "source_b": "sina",
                            "diff_pct": round(diff, 4),
                        })
    except SourceError:
        pass
    if not quotes:
        raise SourceError("全部数据源均失败")
    return quotes, conflicts, used