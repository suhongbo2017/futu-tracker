# -*- coding: utf-8 -*-
"""观察池与价格提醒 CLI。

用法（在仓库根目录或容器内执行）：
    python -m scripts.watch add US.SIMO --name 慧荣科技
    python -m scripts.watch alert US.SIMO below 123 --note "第一档加仓"
    python -m scripts.watch alert US.NFLX below 50
    python -m scripts.watch list
    python -m scripts.watch rm 3
    python -m scripts.watch check          # 用库内最新价立即评估并推送
    python -m scripts.watch check --dry    # 只看会不会触发，不推送、不改状态

标的加入 instruments 后即进入行情采集范围（每 30 分钟一轮，美股盘中）；
提醒由 `collect_quotes` 自动评估，触发后一次性失效。
"""
import argparse
import os
import sys

from app import alerts, config, db


def _code(raw):
    c = raw.strip().upper()
    return c if c.startswith("US.") else f"US.{c}"


def cmd_add(args, conn):
    code = _code(args.code)
    db.ensure_instrument(conn, code, args.name or code[3:])
    conn.commit()
    print(f"已加入观察池: {code}")
    return 0


def cmd_alert(args, conn):
    code = _code(args.code)
    alert_id = db.add_alert(conn, code, args.direction, args.price, args.note)
    print(f"已创建提醒 #{alert_id}: {code} {args.direction} {args.price:g}"
          + (f"（{args.note}）" if args.note else ""))
    return 0


def cmd_list(args, conn):
    insts = {i["code"]: i for i in db.list_instruments(conn, active_only=False)}
    rows = db.list_alerts(conn)
    if not rows:
        print("（暂无提醒）")
        return 0
    print(f"{'ID':>3}  {'标的':<10} {'方向':<6} {'目标':>9} {'状态':<8} 备注")
    for a in rows:
        name = insts.get(a["code"], {}).get("name", "")
        state = "已触发" if not a["active"] else "监控中"
        target = f"{a['target']:,.2f}"
        print(f"{a['id']:>3}  {a['code']:<10} {a['direction']:<6} {target:>9} {state:<8} "
              f"{a.get('note') or ''}")
        if a.get("triggered_at"):
            print(f"      └ 触发于 {a['triggered_at']} @ {a['triggered_price']:,.2f}")
    return 0


def cmd_rm(args, conn):
    n = db.remove_alert(conn, args.id)
    print("已删除" if n else "未找到该提醒")
    return 0 if n else 1


def cmd_remind(args, conn):
    code = _code(args.code) if args.code else None
    rid = db.add_reminder(conn, args.date, args.note, code)
    print(f"已创建提醒 #{rid}: {args.date} {args.code or '（通用）'} — {args.note}")
    return 0


def cmd_reminders(args, conn):
    rows = db.list_reminders(conn)
    if not rows:
        print("（暂无日期提醒）")
        return 0
    for r in rows:
        state = f"已发 {r['sent_at']}" if r["sent_at"] else "待发"
        print(f"{r['id']:>3}  {r['due_date']}  {r['code'] or '通用':<9} {state:<20} {r['note']}")
    return 0


def cmd_due(args, conn):
    import datetime as dt
    today = args.date or dt.date.today().isoformat()
    items = db.due_reminders(conn, today)
    if not items:
        print(f"{today} 没有到期提醒")
        return 0
    names = {i["code"]: i["name"] for i in db.list_instruments(conn, active_only=False)}
    print(alerts.format_reminders(items, names))
    if args.dry:
        print("")
        print("[dry-run] 未推送、未标记")
        return 0
    r = alerts.check_reminders(conn, today=today)
    print("")
    print(f"推送结果: sent={r['sent']} {r['detail']}")
    return 0 if r["sent"] else 1


DEFAULT_ALERT_FILES = ("alerts.json", "/data/alerts.json")


def cmd_load(args, conn):
    """从 JSON 批量导入标的与提醒（幂等：相同 code/direction/target 的监控中提醒会被跳过）。"""
    import json
    path = args.path
    if not path:
        path = next((p for p in DEFAULT_ALERT_FILES if os.path.exists(p)), None)
        if not path:
            print("找不到 alerts.json：请放在当前目录或 /data/alerts.json，或显式传入路径")
            return 1
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    added_inst = 0
    for inst in data.get("instruments", []):
        db.ensure_instrument(conn, _code(inst["code"]), inst.get("name"))
        added_inst += 1
    conn.commit()

    existing = {(a["code"], a["direction"], float(a["target"]))
                for a in db.list_alerts(conn, active_only=True)}
    added, skipped = [], []
    for a in data.get("alerts", []):
        code = _code(a["code"])
        key = (code, a["direction"], float(a["target"]))
        if key in existing:
            skipped.append(key)
            continue
        db.add_alert(conn, code, a["direction"], a["target"], a.get("note"))
        existing.add(key)
        added.append(key)

    existing_rem = {(r["due_date"], r["code"], r["note"]) for r in db.list_reminders(conn)}
    added_rem, skipped_rem = [], []
    for r in data.get("reminders", []):
        code = _code(r["code"]) if r.get("code") else None
        key = (r["date"], code, r["note"])
        if key in existing_rem:
            skipped_rem.append(key)
            continue
        db.add_reminder(conn, r["date"], r["note"], code)
        existing_rem.add(key)
        added_rem.append(key)

    print(f"标的导入 {added_inst} 个；新增到价提醒 {len(added)} 条（跳过 {len(skipped)}）；"
          f"新增日期提醒 {len(added_rem)} 条（跳过 {len(skipped_rem)}）")
    for c, d, t in added:
        print(f"  + {c} {d} {t:g}")
    for d_, c_, n_ in added_rem:
        print(f"  + {d_} {c_ or '通用'} {n_}")
    return 0


def cmd_check(args, conn):
    prices = {}
    for a in db.list_alerts(conn, active_only=True):
        q = db.latest_quote(conn, a["code"])
        if q and q.get("close") is not None:
            prices[a["code"]] = q["close"]
    if not prices:
        print("库内没有可用行情，先跑一次 collect_quotes")
        return 1
    fired = alerts.evaluate(db.list_alerts(conn, active_only=True), prices)
    if not fired:
        print(f"未触发（已检查 {len(prices)} 个标的最新价）")
        return 0
    names = {i["code"]: i["name"] for i in db.list_instruments(conn, active_only=False)}
    text = alerts.format_triggered(fired, names)
    print(text)
    if args.dry:
        print("\n[dry-run] 未推送、未置为已触发")
        return 0
    result = alerts.check_and_notify(conn, prices)
    print(f"\n推送结果: sent={result['sent']} {result['detail']}")
    return 0 if result["sent"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="futu-tracker 观察池 / 价格提醒")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="加入观察池（进入行情采集）")
    p.add_argument("code")
    p.add_argument("--name")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("alert", help="创建一次性价格提醒")
    p.add_argument("code")
    p.add_argument("direction", choices=["below", "above"])
    p.add_argument("price", type=float)
    p.add_argument("--note")
    p.set_defaults(func=cmd_alert)

    p = sub.add_parser("list", help="列出全部提醒")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("rm", help="删除提醒")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("remind", help="创建日期提醒（财报/事件）")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("note")
    p.add_argument("--code")
    p.set_defaults(func=cmd_remind)

    p = sub.add_parser("reminders", help="列出日期提醒")
    p.set_defaults(func=cmd_reminders)

    p = sub.add_parser("due", help="检查并推送到期日期提醒")
    p.add_argument("--date", help="YYYY-MM-DD，默认今天")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("load", help="从 JSON 批量导入标的与提醒（幂等）")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("check", help="用库内最新价立即评估")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    conn = db.connect()
    db.init_db(conn)
    try:
        return args.func(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
