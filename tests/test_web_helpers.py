# -*- coding: utf-8 -*-
"""Web 录入辅助函数测试：标的代码规范化、超卖库存口径（配合 app/web.py）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.web import _available_qty, normalize_code


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