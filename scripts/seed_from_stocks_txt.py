# -*- coding: utf-8 -*-
"""从旧的 stocks.txt 播种：建立标的 + 初始持仓流水。

旧格式：`代码 名称 [买入价 持有数量]`（如 `US.NFLX 奈飞 86.28 1600`）

用法：
    python -m scripts.seed_from_stocks_txt /path/to/stocks.txt [--date 2026-01-01] [--dry-run]

说明：初始持仓会被写成**一条 BUY 流水**（qty + 成本价），因此
      移动加权均价 == stocks.txt 里的买入价，与旧报告口径一致。
      真实建仓时间未知，用 `--date` 指定并在 note 里标注。
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_stocks(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue
            code = parts[0]
            rest = parts[1:]
            cost = qty = None
            if len(rest) >= 2 and _num(rest[-1]) is not None and _num(rest[-2]) is not None:
                qty, cost = _num(rest[-1]), _num(rest[-2])
                rest = rest[:-2]
            rows.append({"code": code, "name": " ".join(rest) or code, "cost": cost, "qty": qty})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 stocks.txt 播种标的与初始持仓")
    ap.add_argument("path")
    ap.add_argument("--date", default=dt.date.today().isoformat(),
                    help="初始建仓日期（真实时间未知时的占位）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    stocks = parse_stocks(args.path)
    print(f"解析到 {len(stocks)} 只标的")

    if args.dry_run:
        for s in stocks:
            print(f"  {s['code']:<10} {s['name'][:14]:<16} 成本={s['cost']} 数量={s['qty']}")
        return 0

    conn = db.connect()
    db.init_db(conn)
    added_i = added_t = 0
    with db.tx(conn):
        for s in stocks:
            symbol = s["code"].split(".")[-1].upper()
            exists = conn.execute("SELECT 1 FROM instruments WHERE code=?", (s["code"],)).fetchone()
            db.upsert_instrument(conn, s["code"], symbol, s["name"])
            if not exists:
                added_i += 1
            if s["cost"] and s["qty"]:
                # 幂等：同一标的同一天已有初始流水则不再插
                dup = conn.execute(
                    """SELECT 1 FROM transactions
                       WHERE code=? AND side='BUY' AND source='seed' AND substr(trade_time,1,10)=?""",
                    (s["code"], args.date)).fetchone()
                if dup:
                    continue
                db.insert_transaction(
                    conn, s["code"], "BUY", price=s["cost"], qty=s["qty"], fee=0.0,
                    trade_time=f"{args.date}T00:00:00",
                    note="初始建仓（从 stocks.txt 导入，建仓时间未知）", source="seed")
                added_t += 1
    conn.close()
    print(f"完成：新增标的 {added_i}，新增初始持仓流水 {added_t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())