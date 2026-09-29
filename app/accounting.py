# -*- coding: utf-8 -*-
"""账务引擎：移动加权平均成本法。

规则（这是本项目的口径红线）：
  BUY : avg = (old_qty*old_avg + qty*price + fee) / (old_qty + qty)
  SELL: realized += (price - avg) * qty - fee ；avg 不变；qty 减少
  DIV : 现金分红，price 字段=总金额 → realized += price
  FEE : 单独费用，price 字段=金额 → realized -= price
  ADJ : 拆股/合股调整，qty 字段=股数增减 → 成本总额不变，avg 按比例缩放
    · 已实现盈亏（realized）与浮动盈亏（unrealized）永远分开统计
    · 币种统一 USD
  · 流水只追加；冲正通过 reverses_id 忽略被冲正的记录
"""
from collections import OrderedDict

SIDES = ("BUY", "SELL", "DIV", "FEE", "ADJ")


def _new_pos():
    return {"qty": 0.0, "avg_cost": 0.0, "realized_pnl": 0.0, "cost_basis": 0.0}


def _apply(pos, txn):
    side = (txn.get("side") or "").upper()
    price = float(txn.get("price") or 0.0)
    qty = float(txn.get("qty") or 0.0)
    fee = float(txn.get("fee") or 0.0)

    if side == "BUY":
        total_cost = pos["qty"] * pos["avg_cost"] + qty * price + fee
        new_qty = pos["qty"] + qty
        pos["qty"] = new_qty
        pos["avg_cost"] = (total_cost / new_qty) if new_qty else 0.0
        pos["cost_basis"] = pos["qty"] * pos["avg_cost"]

    elif side == "SELL":
        sell_qty = min(qty, pos["qty"])
        pos["realized_pnl"] += (price - pos["avg_cost"]) * sell_qty - fee
        pos["qty"] = pos["qty"] - sell_qty
        if pos["qty"] <= 1e-9:          # 清仓：均价归零，避免残值污染
            pos["qty"] = 0.0
            pos["avg_cost"] = 0.0
        pos["cost_basis"] = pos["qty"] * pos["avg_cost"]

    elif side == "DIV":
        pos["realized_pnl"] += price

    elif side == "FEE":
        pos["realized_pnl"] -= price

    elif side == "ADJ":
        old_qty = pos["qty"]
        new_qty = old_qty + qty
        if new_qty > 0 and old_qty > 0:
            pos["avg_cost"] = pos["avg_cost"] * old_qty / new_qty
        pos["qty"] = max(new_qty, 0.0)
        pos["cost_basis"] = pos["qty"] * pos["avg_cost"]

    else:
        raise ValueError(f"未知 side: {side!r}")

    return pos


def compute_positions(transactions):
    """输入流水列表（已按时间排序），返回 {code: pos}。被冲正的记录会被跳过。"""
    reversed_ids = {t["reverses_id"] for t in transactions if t.get("reverses_id")}
    positions = OrderedDict()
    for t in transactions:
        if t.get("id") in reversed_ids:
            continue
        code = t["code"]
        pos = positions.setdefault(code, _new_pos())
        _apply(pos, t)
    return positions


def mark_to_market(positions, prices):
    """附上最新价并计算浮动盈亏。prices: {code: last_price}"""
    out = OrderedDict()
    for code, pos in positions.items():
        last = prices.get(code)
        row = dict(pos)
        row["code"] = code
        row["last"] = last
        if last and pos["qty"]:
            row["market_value"] = pos["qty"] * last
            row["unrealized_pnl"] = (last - pos["avg_cost"]) * pos["qty"]
            row["unrealized_pct"] = ((last / pos["avg_cost"] - 1) * 100) if pos["avg_cost"] else None
        else:
            row["market_value"] = 0.0 if not pos["qty"] else None
            row["unrealized_pnl"] = 0.0 if not pos["qty"] else None
            row["unrealized_pct"] = None
        out[code] = row
    return out


def portfolio_totals(rows):
    """组合汇总（只统计有持仓与价格的）"""
    market_value = sum(r["market_value"] or 0.0 for r in rows)
    cost_basis = sum(r["cost_basis"] or 0.0 for r in rows)
    unrealized = sum(r["unrealized_pnl"] or 0.0 for r in rows)
    realized = sum(r["realized_pnl"] or 0.0 for r in rows)
    return {
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "unrealized_pct": (unrealized / cost_basis * 100) if cost_basis else None,
    }