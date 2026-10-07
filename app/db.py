# -*- coding: utf-8 -*-
"""SQLite 访问层：只做连接、建表、简单查询；不做业务判断。"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect(path=None):
    p = path or config.DB_PATH
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn=None):
    """建表（幂等）。"""
    own = conn is None
    conn = conn or connect()
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        if own:
            conn.close()


@contextmanager
def tx(conn):
    """事务上下文：异常自动回滚。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- runs ----------
def run_start(conn, kind):
    cur = conn.execute(
        "INSERT INTO runs(kind, started_at, status) VALUES (?,?, 'running')",
        (kind, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def run_finish(conn, run_id, status, message=""):
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, message=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), status, message, run_id),
    )
    conn.commit()


# ---------- instruments ----------
def ensure_instrument(conn, code, name=None):
    """确保标的存在；不存在时自动注册（Web 手工录入新代码时调用）。
    - symbol 取代码尾段并大写（"US.AAPL" -> "AAPL"），保证采集端能匹配行情键；
    - 已存在的标的原样保留，避免覆盖采集器写入的真实名称；
    - 单条 INSERT ... ON CONFLICT DO NOTHING 消除并发注册竞态。"""
    symbol = code.split(".")[-1].strip().upper()
    conn.execute(
        """INSERT INTO instruments(code, symbol, name, market, currency)
           VALUES (?,?,?,?,?)
           ON CONFLICT(code) DO NOTHING""",
        (code, _symbol_of(code), name or _symbol_of(code), "US", "USD"),
    )


def _symbol_of(code):
    """内部代码 -> 交易所符号：US.AAPL -> AAPL；US.BRK.B -> BRK.B（仅去前缀，不取尾段）。"""
    c = code.strip().upper()
    return c[3:] if c.startswith("US.") else c


def upsert_instrument(conn, code, symbol, name, market="US", currency="USD"):
    conn.execute(
        """INSERT INTO instruments(code, symbol, name, market, currency)
           VALUES (?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
             symbol=excluded.symbol, name=excluded.name,
             market=excluded.market, currency=excluded.currency""",
        (code, symbol, name, market, currency),
    )


def list_instruments(conn, active_only=True):
    sql = "SELECT * FROM instruments"
    if active_only:
        sql += " WHERE active=1"
    return [dict(r) for r in conn.execute(sql + " ORDER BY code")]


# ---------- quotes ----------
def upsert_quote(conn, code, date, o, h, l, c, v, source, is_final=1):
    conn.execute(
        """INSERT INTO quotes_daily(code, date, open, high, low, close, volume, source, is_final, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(code, date) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
             volume=excluded.volume, source=excluded.source, is_final=excluded.is_final,
             updated_at=datetime('now')""",
        (code, date, o, h, l, c, v, source, is_final),
    )


def latest_quote(conn, code):
    r = conn.execute(
        "SELECT * FROM quotes_daily WHERE code=? ORDER BY date DESC LIMIT 1", (code,)
    ).fetchone()
    return dict(r) if r else None


def quote_on(conn, code, date):
    r = conn.execute(
        "SELECT * FROM quotes_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return dict(r) if r else None


# ---------- transactions ----------
def insert_transaction(conn, code, side, price, qty, fee=0.0, trade_time=None,
                       note=None, source="manual", reverses_id=None):
    trade_time = trade_time or datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO transactions(code, side, price, qty, fee, trade_time, note, source, reverses_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (code, side, price, qty, fee, trade_time, note, source, reverses_id),
    )
    return cur.lastrowid


def all_transactions(conn, code=None, upto=None):
    sql, args = "SELECT * FROM transactions WHERE 1=1", []
    if code:
        sql += " AND code=?"; args.append(code)
    if upto:
        sql += " AND trade_time<=?"; args.append(upto)
    sql += " ORDER BY trade_time, id"
    return [dict(r) for r in conn.execute(sql, args)]


# ---------- 备份（账本是唯一不可再生资产）----------
def backup(dest_dir=None, keep=7, conn=None):
    """用 SQLite 原生 VACUUM INTO 做一致性快照，并保留最近 keep 份。"""
    import glob
    dest_dir = dest_dir or os.path.join(config.DATA_DIR, "backups")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]   # 毫秒粒度，避免同秒重名
    dest = os.path.join(dest_dir, f"futu_{stamp}.db")
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("VACUUM INTO ?", (dest,))       # 无需停服，结果一致
    finally:
        if own:
            conn.close()
    files = sorted(glob.glob(os.path.join(dest_dir, "futu_*.db")))
    removed = []
    for f in files[:-keep] if keep > 0 else []:
        try:
            os.remove(f); removed.append(os.path.basename(f))
        except OSError:
            pass
    return {"path": dest, "size": os.path.getsize(dest), "removed": removed,
            "kept": len(files) - len(removed)}