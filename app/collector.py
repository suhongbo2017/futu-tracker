# -*- coding: utf-8 -*-
"""采集任务：行情 → 入库 → 快照。

用法：
    python -m app.collector --quotes      # 只采实时快照（每轮 2 个请求）
    python -m app.collector --history     # 回填/增量日K（每票 1 个请求）
    python -m app.collector --snapshot    # 按当前库内数据生成组合快照
    python -m app.collector --all         # 全部执行
"""
import argparse
import datetime as dt
import sys

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:                                  # pragma: no cover
    _NY = None

from . import config, db, sources
from .accounting import compute_positions, mark_to_market, portfolio_totals


# --------------------------------------------------------------------------
# 美股交易日判定
# --------------------------------------------------------------------------
def ny_now():
    """美东当前时间（naive datetime）；时区数据缺失时回退系统本地时间。"""
    return dt.datetime.now(_NY) if _NY else dt.datetime.now()


def us_session_date(quote):
    """从数据源时间戳推导"美股交易日"。优先用源给的时间，避免时区/夏令时判断出错。"""
    qt = (quote or {}).get("quote_time") or ""
    # 腾讯：2026-09-28 13:09:19（美东时间）
    if len(qt) >= 10 and qt[4] == "-" and qt[7] == "-":
        return qt[:10]
    # 新浪：Sep 28 01:09PM EDT
    try:
        parts = qt.split()
        if len(parts) >= 2 and parts[1].isdigit():
            month = dt.datetime.strptime(parts[0], "%b").month
            day = int(parts[1])
            year = dt.date.today().year
            d = dt.date(year, month, day)
            if d > dt.date.today() + dt.timedelta(days=1):   # 跨年保护
                d = d.replace(year=year - 1)
            return d.isoformat()
    except Exception:
        pass
    # 兜底：当前美东日期
    if _NY:
        return dt.datetime.now(_NY).date().isoformat()
    return dt.date.today().isoformat()


def is_trading_day(date_str):
    try:
        d = dt.date.fromisoformat(date_str)
    except Exception:
        return False
    return d.weekday() < 5        # 周末跳过；节假日由"源日期不变"自然去重


def market_closed(date_str):
    """该交易日是否已收盘（美东 16:00 之后）"""
    if not _NY:
        return True
    now = dt.datetime.now(_NY)
    try:
        d = dt.date.fromisoformat(date_str)
    except Exception:
        return True
    if d < now.date():
        return True
    return now.hour >= 16


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------
def collect_quotes(conn):
    insts = db.list_instruments(conn)
    if not insts:
        raise RuntimeError("instruments 为空，请先播种标的清单")
    codes = [i["code"] for i in insts]

    run_id = db.run_start(conn, "collect_quotes")
    try:
        quotes, conflicts, used = sources.fetch_quotes(codes)
    except Exception as e:
        db.run_finish(conn, run_id, "failed", str(e))
        raise

    saved, skipped = 0, []
    sym2code = {inst["symbol"]: inst["code"] for inst in insts}   # 冲突记录用内部代码
    with db.tx(conn):
        for inst in insts:
            sym = inst["symbol"]
            q = quotes.get(sym)
            if not q or not q.get("last"):
                skipped.append(sym)
                continue
            d = us_session_date(q)
            if not is_trading_day(d):
                skipped.append(f"{sym}(非交易日 {d})")
                continue
            db.upsert_quote(
                conn, inst["code"], d,
                q.get("open"), q.get("high"), q.get("low"), q.get("last"), q.get("volume"),
                q.get("source", "unknown"), 1 if market_closed(d) else 0,
            )
            if not inst.get("name") or inst["name"] == sym:
                db.upsert_instrument(conn, inst["code"], sym, q.get("name") or sym)
            saved += 1
            for c in conflicts:                 # 给分歧记录带上真实交易日
                if c["symbol"] == sym:
                    c["date"] = d
        for c in conflicts:
            conn.execute(
                """INSERT INTO source_conflicts(code, date, price_a, source_a, price_b, source_b, diff_pct)
                   VALUES (?,?,?,?,?,?,?)""",
                (sym2code.get(c["symbol"], c["symbol"]), c.get("date") or us_session_date({}), c["price_a"], c["source_a"],
                 c["price_b"], c["source_b"], c["diff_pct"]),
            )

    status = "ok" if not skipped and not conflicts else "partial"
    msg = f"源={','.join(used)} 写入={saved} 跳过={len(skipped)} 分歧={len(conflicts)}"
    if skipped:
        msg += " | " + ",".join(skipped[:5])
    db.run_finish(conn, run_id, status, msg)
    return {"status": status, "saved": saved, "skipped": skipped,
            "conflicts": conflicts, "sources": used, "message": msg}


def collect_history(conn):
    insts = db.list_instruments(conn)
    run_id = db.run_start(conn, "collect_history")
    ok, fail = 0, []
    with db.tx(conn):
        for inst in insts:
            try:
                bars = sources.fetch_sina_history(inst["code"], config.HISTORY_BARS)
            except Exception as e:
                fail.append(f"{inst['symbol']}:{type(e).__name__}")
                continue
            for b in bars:
                if not b.get("date") or not b.get("close") or b["close"] <= 0:
                    continue                   # 停牌/无效行不入库，避免污染最新价
                db.upsert_quote(conn, inst["code"], b["date"], b["open"], b["high"],
                                b["low"], b["close"], b["volume"], b["source"], 1)
            ok += 1
    status = "ok" if not fail else ("partial" if ok else "failed")
    msg = f"成功={ok} 失败={len(fail)}" + ((" | " + ",".join(fail[:5])) if fail else "")
    db.run_finish(conn, run_id, status, msg)
    return {"status": status, "ok": ok, "fail": fail, "message": msg}


def snapshot(conn, date=None):
    """按库内最新收盘价生成组合快照与汇总。"""
    insts = db.list_instruments(conn)
    txns = db.all_transactions(conn)
    if not txns:
        return {"status": "skipped", "message": "无流水，跳过快照"}

    prices, used_date = {}, None
    for inst in insts:
        q = db.quote_on(conn, inst["code"], date) if date else db.latest_quote(conn, inst["code"])
        if q:
            prices[inst["code"]] = q["close"]
            used_date = max(used_date or q["date"], q["date"])
    if not prices:
        return {"status": "failed", "message": "库内无任何行情，无法生成快照"}

    positions = compute_positions(txns)
    rows = mark_to_market(positions, prices)
    totals = portfolio_totals(list(rows.values()))

    run_id = db.run_start(conn, "snapshot")
    with db.tx(conn):
        for code, r in rows.items():
            if not r["qty"]:
                continue
            conn.execute(
                """INSERT INTO portfolio_snapshots(date, code, qty, avg_cost, close, market_value, unrealized_pnl)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(date, code) DO UPDATE SET
                     qty=excluded.qty, avg_cost=excluded.avg_cost, close=excluded.close,
                     market_value=excluded.market_value, unrealized_pnl=excluded.unrealized_pnl""",
                (used_date, code, r["qty"], r["avg_cost"], r["last"],
                 r["market_value"], r["unrealized_pnl"]),
            )
        prev = conn.execute(
            "SELECT market_value FROM portfolio_summary WHERE date<? ORDER BY date DESC LIMIT 1",
            (used_date,)).fetchone()
        prev_mv = prev["market_value"] if prev else None
        day_pct = ((totals["market_value"] / prev_mv - 1) * 100) if prev_mv else None
        conn.execute(
            """INSERT INTO portfolio_summary(date, market_value, cost_basis, unrealized_pnl, realized_pnl, day_change_pct)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 market_value=excluded.market_value, cost_basis=excluded.cost_basis,
                 unrealized_pnl=excluded.unrealized_pnl, realized_pnl=excluded.realized_pnl,
                 day_change_pct=excluded.day_change_pct""",
            (used_date, totals["market_value"], totals["cost_basis"],
             totals["unrealized_pnl"], totals["realized_pnl"], day_pct),
        )
    db.run_finish(conn, run_id, "ok", f"date={used_date} 市值={totals['market_value']:.2f}")
    totals.update({"status": "ok", "date": used_date, "rows": rows})
    return totals


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="futu-tracker 采集器")
    ap.add_argument("--quotes", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--date")
    args = ap.parse_args(argv)

    if not any([args.quotes, args.history, args.snapshot, args.all]):
        ap.print_help()
        return 1

    conn = db.connect()
    db.init_db(conn)
    try:
        if args.quotes or args.all:
            print("行情:", collect_quotes(conn))
        if args.history or args.all:
            print("历史:", collect_history(conn))
        if args.snapshot or args.all:
            r = snapshot(conn, args.date)
            print("快照:", {k: v for k, v in r.items() if k != "rows"})
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())