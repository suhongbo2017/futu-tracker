# futu-tracker

自托管的美股持仓跟踪服务：采集行情 → 存入轻量数据库 → 网页录入买卖 → 计算盈亏 → 飞书推送。

> 取代原先「本地 Windows 定时脚本生成静态 HTML」的做法（`~/.pi/agent/skills/futu-daily-report`）。

## 设计要点

| 主题 | 决定 |
|---|---|
| 用途 | **只记账**（手工录入成交），不接真实下单 |
| 币种 / 市场 | 统一 **USD**，仅美股（结构可扩展） |
| 成本法 | **移动加权平均**；已实现 / 浮动盈亏分开统计 |
| 账本 | **只追加**（append-only），纠错用反向流水 |
| 数据源 | 腾讯（主）+ 新浪（备/校验），批量一次拉全部标的 |
| 历史日K | 新浪 `US_MinKService.getDailyK`（未复权，全历史） |
| 数据库 | **SQLite**（WAL），定时备份 |
| 推送 | 飞书自定义机器人；**美股收盘后**（北京 04:00/05:00 收盘）发送 |
| 访问 | `http://<host>:8300` + 登录鉴权（免备案） |

## 目录

```
app/
  schema.sql     数据库结构
  config.py      环境变量配置
  db.py          SQLite 访问层
  sources.py     行情数据源（腾讯/新浪/东财）+ 交叉校验
  accounting.py  移动加权平均账务引擎
  collector.py   采集任务（待建）
  notify.py      飞书推送（待建）
  api.py         Web API 与页面（待建）
tests/           单元测试
```

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `FUTU_DB` | SQLite 路径 | `/data/futu.db` |
| `FUTU_PORT` | Web 端口 | `8300` |
| `FUTU_USER` / `FUTU_PASSWORD` | 登录凭据（**密码为空则拒绝启动**） | `admin` / — |
| `FEISHU_WEBHOOK` | 飞书机器人地址 | — |
| `FEISHU_SECRET` | 飞书签名密钥（可选） | — |
| `FUTU_BRIEF_HOUR` / `FUTU_BRIEF_MINUTE` | 简报发送时间（北京时间） | 7 / 30 |
| `FUTU_XCHECK_PCT` | 双源价差告警阈值 | `0.5` |

## 开发

```bash
python -m pip install -r requirements.txt   # 服务器上需加 -i https://mirrors.aliyun.com/pypi/simple/
python -m pytest tests/ -q
python -m app.collector --once              # 采集一次
python -m uvicorn app.api:app --host 0.0.0.0 --port 8300
```