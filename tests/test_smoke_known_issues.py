# -*- coding: utf-8 -*-
"""QA 冒烟遗留场景的回归保护测试。

来源：测试者子代理 QA 验证（2026-10-07），H:/VSCODE/futu-tracker。
后续由管理者审查会修复为「assert 通过」口径：
- test_oversell_silent_truncation：固化「超额卖出被静默截断、但手续费全额计入
  已实现盈亏」的账务引擎兜底行为（accounting.py SELL 分支；Web 录入层会拒绝超卖）。
- test_brief_tolerates_missing_price：回归「持仓无价格（last=None）时收盘简报
  不再崩溃」——notify.build_daily_brief 对 None 显示 '-'（原缺陷：TypeError
  导致当天简报静默丢失且不重试）。
- test_backup_same_second_twice_ok：回归「同一秒内连续两次备份均成功」——
  db.backup 文件名带毫秒粒度（原缺陷：futu_%Y%m%d_%H%M%S.db 同秒重名，
  VACUUM INTO 不覆盖已存在文件而抛 OperationalError）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, notify


def _txn(id, code, side, price, qty=0, fee=0, reverses_id=None):
    return {"id": id, "code": code, "side": side, "price": price,
            "qty": qty, "fee": fee, "reverses_id": reverses_id}


def test_oversell_silent_truncation():
    """超额卖空（账务引擎兜底）：只按持仓数成交，但 fee 全额计入 realized。"""
    from app.accounting import compute_positions
    txns = [_txn(1, "A", "BUY", 100, 10), _txn(2, "A", "SELL", 110, 100, fee=1)]
    pos = compute_positions(txns)["A"]
    assert pos["qty"] == 0.0                 # 只扣 10 股
    assert pos["realized_pnl"] == 99.0       # (110-100)*10 - 1，fee 按整笔扣
    assert pos["cost_basis"] == 0.0


def test_brief_tolerates_missing_price():
    """持仓无价格（新标的尚未采集）时简报不崩溃，价格与盈亏显示 '-'。"""
    rows = [{"code": "US.NVDA", "name": "英伟达", "qty": 10.0, "avg_cost": 100.0,
             "last": None, "unrealized_pnl": None, "realized_pnl": 0.0, "has_price": False}]
    totals = {"market_value": 0, "cost_basis": 1000.0,
              "unrealized_pnl": 0.0, "realized_pnl": 0.0}
    text = notify.build_daily_brief("2026-10-07", rows, totals)
    assert "NVDA" in text and "浮盈 - USD" in text    # 显示规范用尾段代号，无价格显示 '-'


def test_backup_same_second_twice_ok(tmp_path):
    """同一秒内连续两次备份均成功（文件名毫秒粒度，互不冲突）。"""
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_db(conn)
    try:
        d = str(tmp_path / "bk")
        first = db.backup(dest_dir=d, conn=conn)
        second = db.backup(dest_dir=d, conn=conn)
        assert first["path"] != second["path"]
        import glob
        assert len(glob.glob(d + "/futu_*.db")) == 2
    finally:
        conn.close()


def test_backup_keep_zero_removes_all_backups(tmp_path):
    """keep=0 明确表示不保留备份文件。"""
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_db(conn)
    try:
        result = db.backup(dest_dir=str(tmp_path / "bk"), keep=0, conn=conn)
        assert not os.path.exists(result["path"])
        assert result["kept"] == 0
    finally:
        conn.close()
