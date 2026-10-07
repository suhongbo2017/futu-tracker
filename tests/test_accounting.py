# -*- coding: utf-8 -*-
"""账务引擎单元测试：口径红线必须被证明。

运行：  python -m pytest tests/ -q   （pytest 兼容 unittest.TestCase 风格）
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.accounting import compute_positions, mark_to_market, portfolio_totals  # noqa: E402


def txn(i, side, price=0.0, qty=0.0, fee=0.0, trade_time=None, reverses_id=None, code="US.TEST"):
    return {"id": i, "code": code, "side": side, "price": price, "qty": qty, "fee": fee,
            "trade_time": trade_time or f"2026-01-{i:02d}T10:00:00", "reverses_id": reverses_id}


class TestWeightedAverage(unittest.TestCase):

    def test_simple_buy(self):
        p = compute_positions([txn(1, "BUY", 100, 10)])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["qty"], 10)
        self.assertAlmostEqual(pos["avg_cost"], 100)
        self.assertAlmostEqual(pos["cost_basis"], 1000)

    def test_buy_includes_fee_in_cost(self):
        p = compute_positions([txn(1, "BUY", 100, 10, fee=5)])
        self.assertAlmostEqual(p["US.TEST"]["avg_cost"], 100.5)

    def test_two_buys_average(self):
        # 100×10 + 200×10 = 3000 / 20 = 150
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "BUY", 200, 10)])
        self.assertAlmostEqual(p["US.TEST"]["avg_cost"], 150)

    def test_partial_sell_realized_and_avg_unchanged(self):
        # 买 100×10，卖 150×4（费 2）→ 已实现 (150-100)*4-2 = 198；均价仍 100；剩 6 股
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "SELL", 150, 4, fee=2)])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["avg_cost"], 100)
        self.assertAlmostEqual(pos["qty"], 6)
        self.assertAlmostEqual(pos["realized_pnl"], 198)

    def test_full_sell_resets_cost_basis(self):
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "SELL", 120, 10)])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["qty"], 0)
        self.assertAlmostEqual(pos["avg_cost"], 0)
        self.assertAlmostEqual(pos["realized_pnl"], 200)
        self.assertAlmostEqual(pos["cost_basis"], 0)

    def test_dividend_and_fee_affect_realized_only(self):
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "DIV", 30), txn(3, "FEE", 4)])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["realized_pnl"], 26)
        self.assertAlmostEqual(pos["avg_cost"], 100)   # 分红不改成本

    def test_split_adjustment_keeps_cost_total(self):
        # 买 100×10 = 1000；2:1 拆股，股数 +10 → 20 股，均价 50
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "ADJ", 0, 10)])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["qty"], 20)
        self.assertAlmostEqual(pos["avg_cost"], 50)
        self.assertAlmostEqual(pos["cost_basis"], 1000)

    def test_reversal_is_ignored(self):
        # 流水 2 冲正流水 1 → 流水 1 不生效
        p = compute_positions([
            txn(1, "BUY", 100, 10),
            txn(2, "SELL", 0, 0, reverses_id=1),
            txn(3, "BUY", 50, 2),
        ])
        pos = p["US.TEST"]
        self.assertAlmostEqual(pos["qty"], 2)
        self.assertAlmostEqual(pos["avg_cost"], 50)

    def test_mark_to_market(self):
        p = compute_positions([txn(1, "BUY", 100, 10)])
        rows = mark_to_market(p, {"US.TEST": 130})
        r = rows["US.TEST"]
        self.assertAlmostEqual(r["market_value"], 1300)
        self.assertAlmostEqual(r["unrealized_pnl"], 300)
        self.assertAlmostEqual(r["unrealized_pct"], 30)

    def test_totals_are_usd_sums(self):
        p = compute_positions([txn(1, "BUY", 100, 10), txn(2, "SELL", 120, 4)])
        rows = mark_to_market(p, {"US.TEST": 110})
        tot = portfolio_totals(list(rows.values()))
        self.assertAlmostEqual(tot["market_value"], 660)     # 6 股 × 110
        self.assertAlmostEqual(tot["cost_basis"], 600)       # 6 股 × 100
        self.assertAlmostEqual(tot["unrealized_pnl"], 60)
        self.assertAlmostEqual(tot["realized_pnl"], 80)      # (120-100)*4


if __name__ == "__main__":
    unittest.main(verbosity=2)