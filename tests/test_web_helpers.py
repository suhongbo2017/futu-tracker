# -*- coding: utf-8 -*-
"""Web 录入辅助函数测试：标的代码规范化、超卖库存口径（配合 app/web.py）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.accounting import portfolio_totals
from app.collector import snapshot
from app.sources import _canonical_symbol
from app.web import _available_qty, _normalize_trade_time, normalize_code


def _conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.ensure_instrument(conn, "US.NVDA")
    return conn


def test_normalize_code():
    assert normalize_code("nvda") == "US.NVDA"          # 小写裸代码 → 大写 + US. 前缀
    assert normalize_code(" AAPL ") == "US.AAPL"        # 去空白
    assert normalize_code("US.NFLX") == "US.NFLX"       # 已带前缀不变
    assert normalize_code("brk.b") == "US.BRK.B"        # 含点代码也补 US. 前缀
    assert normalize_code("") is None                   # 空 → 非法
    assert normalize_code("A-B") is None                # 非法字符
    assert normalize_code("US.A") == "US.A"
    assert normalize_code("US.US.A") is None
    assert normalize_code("A..B") is None


def test_source_symbol_aliases():
    requested = {"AAPL", "BRK.B"}
    assert _canonical_symbol("AAPL", requested) == "AAPL"
    assert _canonical_symbol("BRK_B", requested) == "BRK.B"
    assert _canonical_symbol("UNKNOWN", requested) == "UNKNOWN"


def test_normalize_trade_time():
    assert _normalize_trade_time("2026-01-02T03:04") == "2026-01-02T03:04:00"
    assert _normalize_trade_time("2026-01-02 03:04:05") == "2026-01-02T03:04:05"
    assert _normalize_trade_time("2026-02-30T03:04") is None
    assert _normalize_trade_time("2026-01-02T25:04") is None
    assert _normalize_trade_time("") is None


def test_reversal_index_allows_only_one_reversal():
    conn = _conn()
    try:
        first = db.insert_transaction(conn, "US.NVDA", "BUY", 100, 1)
        conn.commit()
        db.insert_transaction(conn, "US.NVDA", "BUY", 0, 0, reverses_id=first)
        conn.commit()
        import sqlite3
        try:
            db.insert_transaction(conn, "US.NVDA", "BUY", 0, 0, reverses_id=first)
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
        else:
            raise AssertionError("同一流水允许重复冲正")
    finally:
        conn.close()


def test_portfolio_totals_reports_missing_prices():
    rows = [
        {"qty": 2, "cost_basis": 200, "market_value": 220,
         "unrealized_pnl": 20, "realized_pnl": 0, "has_price": True},
        {"qty": 3, "cost_basis": 300, "market_value": None,
         "unrealized_pnl": None, "realized_pnl": 5, "has_price": False},
    ]
    totals = portfolio_totals(rows)
    assert totals["missing_price_count"] == 1
    assert totals["held_count"] == 2
    assert totals["price_coverage_pct"] == 50.0


def test_snapshot_uses_common_valuation_date():
    conn = db.connect(":memory:")
    db.init_db(conn)
    try:
        db.ensure_instrument(conn, "US.AAPL")
        db.ensure_instrument(conn, "US.NVDA")
        db.insert_transaction(conn, "US.AAPL", "BUY", 100, 1)
        db.insert_transaction(conn, "US.NVDA", "BUY", 200, 1)
        db.upsert_quote(conn, "US.AAPL", "2026-01-02", 101, 101, 101, 101, 1, "test")
        db.upsert_quote(conn, "US.NVDA", "2026-01-01", 201, 201, 201, 201, 1, "test")
        result = snapshot(conn)
        assert result["date"] == "2026-01-01"
        assert result["status"] == "partial"
        assert result["missing_price_count"] == 1
    finally:
        conn.close()


def test_available_qty_respects_trade_time():
    conn = _conn()
    try:
        db.insert_transaction(conn, "US.NVDA", "BUY", 100, 10,
                              trade_time="2026-01-01T10:00:00")
        db.insert_transaction(conn, "US.NVDA", "SELL", 110, 3,
                              trade_time="2026-01-02T10:00:00")
        assert _available_qty(conn, "US.NVDA", "2026-01-01T12:00:00") == 10.0
        assert _available_qty(conn, "US.NVDA", "2026-01-02T12:00:00") == 7.0
        assert _available_qty(conn, "US.NVDA", "2026-01-03T12:00:00") == 7.0
        assert _available_qty(conn, "US.NVDA", None) == 7.0    # 缺省 = 现在
    finally:
        conn.close()


def test_available_qty_empty():
    conn = db.connect(":memory:")
    db.init_db(conn)
    try:
        assert _available_qty(conn, "US.UNKNOWN", None) == 0.0
    finally:
        conn.close()


def test_oversell_never_goes_negative():
    """web 层政策（不支持卖空）：任何时点可用持仓不会为负，超卖由 do_POST 拒绝。"""
    conn = _conn()
    try:
        db.insert_transaction(conn, "US.NVDA", "BUY", 100, 10)
        assert _available_qty(conn, "US.NVDA", None) == 10.0
        assert 99 > _available_qty(conn, "US.NVDA", None)     # 99 股超卖请求必然超过可用
    finally:
        conn.close()