# -*- coding: utf-8 -*-
"""价格提醒测试：评估逻辑、一次性触发、推送失败重试、文案格式。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import alerts, db


def _conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.ensure_instrument(conn, "US.SIMO", "慧荣科技")
    db.ensure_instrument(conn, "US.NFLX", "奈飞")
    return conn


def _alert(conn, code, direction, target, note=None):
    return db.add_alert(conn, code, direction, target, note)


def test_evaluate_below_and_above():
    conn = _conn()
    _alert(conn, "US.SIMO", "below", 123)
    _alert(conn, "US.NFLX", "above", 70)
    rows = db.list_alerts(conn, active_only=True)

    fired = alerts.evaluate(rows, {"US.SIMO": 120.0, "US.NFLX": 72.0})
    assert {f["code"] for f in fired} == {"US.SIMO", "US.NFLX"}

    # 价格未到：不触发
    assert alerts.evaluate(rows, {"US.SIMO": 130.0, "US.NFLX": 60.0}) == []

    # 边界：等于目标价即触发
    assert len(alerts.evaluate(rows, {"US.SIMO": 123.0})) == 1


def test_missing_price_is_skipped():
    conn = _conn()
    _alert(conn, "US.SIMO", "below", 123)
    assert alerts.evaluate(db.list_alerts(conn, active_only=True), {}) == []


def test_one_shot_trigger_and_no_resend():
    conn = _conn()
    _alert(conn, "US.SIMO", "below", 123, "第一档加仓")
    sent = []

    r1 = alerts.check_and_notify(conn, {"US.SIMO": 120.0}, send=lambda t: (sent.append(t), (True, "ok"))[1])
    assert r1["sent"] is True and len(r1["fired"]) == 1
    assert db.list_alerts(conn, active_only=True) == []          # 已置为触发
    a = db.list_alerts(conn)[0]
    assert a["active"] == 0 and a["triggered_price"] == 120.0

    r2 = alerts.check_and_notify(conn, {"US.SIMO": 118.0}, send=lambda t: (sent.append(t), (True, "ok"))[1])
    assert r2["fired"] == [] and len(sent) == 1                  # 不重复推送


def test_send_failure_keeps_alert_active():
    conn = _conn()
    _alert(conn, "US.SIMO", "below", 123)

    r = alerts.check_and_notify(conn, {"US.SIMO": 120.0}, send=lambda t: (False, "webhook 500"))
    assert r["sent"] is False
    assert len(db.list_alerts(conn, active_only=True)) == 1      # 保留，下一轮重试


def test_format_contains_key_facts():
    conn = _conn()
    _alert(conn, "US.SIMO", "below", 123, "第一档加仓")
    fired = alerts.evaluate(db.list_alerts(conn, active_only=True), {"US.SIMO": 120.0})
    text = alerts.format_triggered(fired, {"US.SIMO": "慧荣科技"})
    assert "SIMO" in text and "慧荣科技" in text
    assert "120.00" in text and "123.00" in text
    assert "第一档加仓" in text and "≤" in text


def test_add_alert_validates():
    conn = _conn()
    for bad in (("US.SIMO", "sideways", 100), ("US.SIMO", "below", 0)):
        try:
            db.add_alert(conn, *bad)
        except ValueError:
            continue
        raise AssertionError(f"应当拒绝非法参数 {bad}")


def test_load_json_is_idempotent(tmp_path, monkeypatch):
    import json
    from app import config
    from scripts import watch

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({
        "instruments": [{"code": "US.SIMO", "name": "慧荣科技"}],
        "alerts": [{"code": "US.SIMO", "direction": "below", "target": 123, "note": "a"},
                   {"code": "US.SIMO", "direction": "below", "target": 102}],
    }), encoding="utf-8")

    assert watch.main(["load", str(path)]) == 0
    conn = db.connect()
    assert len(db.list_alerts(conn, active_only=True)) == 2

    # 再跑一次：幂等，不产生重复
    assert watch.main(["load", str(path)]) == 0
    assert len(db.list_alerts(conn, active_only=True)) == 2


def test_reminders_due_and_one_shot():
    conn = _conn()
    rid = db.add_reminder(conn, "2026-12-02", "CRM 财报", "US.CRM")
    db.add_reminder(conn, "2026-12-31", "年底复盘", None)

    assert db.due_reminders(conn, "2026-12-01") == []
    due = db.due_reminders(conn, "2026-12-02")
    assert len(due) == 1 and due[0]["id"] == rid

    sent = []
    r = alerts.check_reminders(conn, today="2026-12-02", send=lambda t: (sent.append(t), (True, "ok"))[1])
    assert r["sent"] is True and len(r["fired"]) == 1
    assert db.due_reminders(conn, "2026-12-02") == []          # 已标记，不重发
    assert "CRM" in sent[0] and "财报" in sent[0]

    # 逾期未发的一次性补发
    assert len(db.due_reminders(conn, "2027-01-05")) == 1


def test_load_reminders_idempotent(tmp_path, monkeypatch):
    import json
    from app import config
    from scripts import watch

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t2.db"))
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({
        "instruments": [{"code": "US.CRM", "name": "Salesforce"}],
        "alerts": [],
        "reminders": [{"date": "2026-12-02", "code": "US.CRM", "note": "财报"}],
    }), encoding="utf-8")

    assert watch.main(["load", str(path)]) == 0
    conn = db.connect()
    assert len(db.list_reminders(conn)) == 1
    assert watch.main(["load", str(path)]) == 0
    assert len(db.list_reminders(conn)) == 1
