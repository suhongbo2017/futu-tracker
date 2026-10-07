# -*- coding: utf-8 -*-
"""Web 记账录入路径：新代码自动注册标的，避免外键约束失败。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db


def _conn(tmp):
    path = os.path.join(tmp, "t.db")
    c = db.connect(path)
    db.init_db(c)
    return c


def test_web_transaction_auto_registers_instrument(tmp_path):
    conn = _conn(str(tmp_path))
    try:
        with db.tx(conn):
            db.ensure_instrument(conn, "NVDA")
            db.insert_transaction(conn, "NVDA", "BUY", 130.5, 10, fee=1.0,
                                  source="web")
        insts = db.list_instruments(conn)
        assert [i["code"] for i in insts] == ["NVDA"]
        assert insts[0]["symbol"] == "NVDA"
        assert insts[0]["name"] == "NVDA"          # 采集后会用真实名称覆盖
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    finally:
        conn.close()


def test_ensure_instrument_keeps_existing_name(tmp_path):
    conn = _conn(str(tmp_path))
    try:
        db.upsert_instrument(conn, "NVDA", "NVDA", "英伟达 NVIDIA")
        with db.tx(conn):
            db.ensure_instrument(conn, "NVDA", name="不应覆盖")
        insts = db.list_instruments(conn)
        assert insts[0]["name"] == "英伟达 NVIDIA"
    finally:
        conn.close()


def test_ensure_instrument_symbol_strips_us_prefix(tmp_path):
    """S1 回归：注册 US.AAPL 时 symbol 必须是 AAPL（剥离前缀并大写），
    否则采集端用 quotes.get(symbol) 匹配行情键必然 miss，新标的水远无价格。"""
    conn = _conn(str(tmp_path))
    try:
        with db.tx(conn):
            db.ensure_instrument(conn, "US.AAPL", name="苹果")
        inst = db.list_instruments(conn)[0]
        assert inst["code"] == "US.AAPL"
        assert inst["symbol"] == "AAPL"
        assert inst["name"] == "苹果"
    finally:
        conn.close()


def test_ensure_instrument_plain_code_symbol(tmp_path):
    """裸代码（db 层直接调用）symbol 即自身大写。"""
    conn = _conn(str(tmp_path))
    try:
        with db.tx(conn):
            db.ensure_instrument(conn, "nvda")
        inst = db.list_instruments(conn)[0]
        assert inst["code"] == "nvda"
        assert inst["symbol"] == "NVDA"
    finally:
        conn.close()