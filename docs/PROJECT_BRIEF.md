---
project: futu-tracker
type: Python self-hosted web service
status: active
language: Python
runtime_dependencies: none
database: SQLite
entrypoint: python -m app.web
market: US equities
currency: USD
last_verified: 2026-10-10
---

# Project Brief

## 1. One-line Summary

`futu-tracker` 是一个自托管的美股持仓记账与跟踪服务：手工录入成交，采集行情，写入 SQLite，计算持仓与盈亏，并通过 Web 页面和飞书发送结果。

边界：只记账，不接入真实交易下单；当前只支持美股和 USD。

## 2. Current Status

- 当前分支：`master`
- 文档创建前工作区：已知状态干净
- 最新状态记录：见 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
- 运行时依赖：Python 标准库
- 开发依赖：`pytest`
- Web 入口：`python -m app.web`
- 默认端口：`8300`
- 数据库：SQLite，启用 WAL；支持自动备份
- 历史状态中记录的测试结果：`27 passed`
- 历史状态中记录的编译检查：`python -m py_compile app/*.py` 通过
- 当前重点问题：服务器约 21:30 的 `collect_quotes` 飞书异常告警，疑似异常标的或旧数据导致

## 3. Repository Map

```text
app/
  __init__.py       包入口
  schema.sql        SQLite 数据库结构
  config.py         环境变量与配置
  db.py             SQLite 访问、事务、备份、标的注册
  accounting.py     移动加权平均账务引擎
  sources.py        腾讯/新浪行情及历史日 K 数据源
  collector.py      行情采集、历史回填、组合快照
  web.py            Web 页面、收益分析、JSON API、内置调度器
  notify.py         飞书推送与 HMAC 签名
  healthcheck.py    容器健康检查

scripts/
  seed_from_stocks_txt.py  从旧 stocks.txt 播种标的

tests/
  test_accounting.py          账务规则测试
  test_web_helpers.py         Web 辅助逻辑测试
  test_web_tx_path.py         Web 记账路径测试
  test_smoke_known_issues.py  已知问题回归测试

docs/
  PROJECT_BRIEF.md   AI 快速上下文和项目结构
  PROJECT_STATUS.md  部署状态、历史记录和待调查问题

Dockerfile
 docker-compose.yml
README.md
requirements.txt
```

## 4. Module Responsibilities

| 模块 | 职责 | 主要输入 | 主要输出 | 关键依赖 |
|---|---|---|---|---|
| `app.web` | Web 页面、JSON API、调度器 | HTTP 请求、数据库、配置 | HTML、JSON、任务结果 | `db`、`accounting`、`collector` |
| `app.accounting` | 计算持仓、成本、已实现与浮动盈亏 | 追加流水、行情 | 账务结果 | 纯 Python 逻辑 |
| `app.collector` | 采集行情、历史回填、生成组合快照 | 标的、数据源、数据库 | 行情、K 线、快照、运行记录 | `sources`、`db` |
| `app.sources` | 腾讯主行情、新浪备用行情和交叉校验 | 标的代码 | 标准化行情数据 | HTTP 标准库 |
| `app.db` | 数据库初始化、查询、事务、备份 | SQLite 操作请求 | 持久化记录 | `sqlite3` |
| `app.config` | 读取并校验环境变量 | 环境变量 | 配置对象/值 | `os` |
| `app.notify` | 飞书消息格式化与发送 | 结果、Webhook、密钥 | 发送结果 | HTTP、HMAC |
| `app.healthcheck` | 容器健康检查 | Web 地址 | 成功/失败退出码 | HTTP 标准库 |

## 5. Architecture and Data Flow

```text
HTTP request
  -> app.web
      -> app.config / app.db
      -> normalize and validate input
      -> append transaction
      -> app.accounting
      -> render HTML or JSON

scheduler / CLI
  -> app.collector
      -> app.db: read active instruments
      -> app.sources: Tencent primary quote
      -> app.sources: Sina fallback/cross-check
      -> app.db: save quotes and runs
      -> app.accounting: calculate portfolio
      -> app.db: save snapshots
      -> app.notify: send Feishu brief or alert
```

## 6. Core Data Model

```text
instruments
  ├── transactions       append-only ledger entries
  ├── quotes             current/historical market data
  └── portfolio_snapshots  valuation snapshots and per-symbol returns

portfolio_summary        daily close portfolio, cash, assets, and return curve
runs                     scheduler, collection, backup, notification history
```

详细字段和约束以 [`app/schema.sql`](../app/schema.sql) 为准。涉及数据模型的改动必须同时检查：

- 数据库表结构和索引
- `app/db.py` 的读写封装
- `app/accounting.py` 的计算输入
- Web/API 的输入校验
- 现有数据迁移和 Docker 持久化路径
- 对应测试和备份恢复行为

## 7. Accounting Rules and Invariants

- 账本只追加，不直接修改历史流水。
- 纠错使用反向流水；重复冲正必须被拒绝。
- 成本法为移动加权平均。
- 已实现盈亏与浮动盈亏分开计算。
- 不允许卖空；卖出数量不能超过当前持仓。
- `DIV` 和 `FEE` 的股数必须为 `0`。
- `ADJ` 调整后持仓不能变成负数。
- 价格、数量、手续费必须是有限数字，拒绝 `NaN`、`Inf` 和非法数值。
- 成交时间必须是有效的真实日期时间。
- 内部股票代码统一为 `US.XXX`；录入裸代码时自动规范化。
- `BRK.B` 与 `BRK_B` 需要兼容行情源键名。
- 缺少部分行情时，总览和简报必须明确标记；汇总只能使用已有行情。
- 同一组合快照使用统一估值日期；数据不足时标记为 `partial`。
- 收益分析和资金曲线只使用美股收盘后的日终快照。
- 现金从 `CASH_IN` / `CASH_OUT` 和交易流水重放：买入/费用减少现金，卖出回款/分红增加现金，拆合股不影响现金。
- 卖出回款进入现金和总资产，不再额外加一次收益。
- 收益分析只显示组合汇总：股票成本价值、浮动收益、现金余额、全部资产价值四条日终曲线。
- 组合恒等式：股票成本价值 + 浮动收益 = 股票市值；股票市值 + 现金余额 = 全部资产价值。
- `CASH` 是只用于 `CASH_IN` / `CASH_OUT` 的虚拟标的，不参与股票持仓和行情采集。
- 采集、备份和每日简报任务需要具备幂等性，并将结果写入 `runs`。

## 8. Runtime Flows

### Web 启动

```text
python -m app.web
  -> load and validate config
  -> initialize SQLite schema
  -> start HTTP server
  -> start periodic collector/snapshot/brief scheduler
```

### 记账

```text
HTTP form/API
  -> normalize symbol
  -> validate transaction type and numeric fields
  -> register instrument if needed
  -> append transaction
  -> calculate moving-average position
  -> render updated portfolio/result
```

### 行情采集

```text
scheduled task or CLI
  -> load active instruments
  -> request Tencent batch quotes
  -> use Sina fallback and compare sources
  -> save valid quotes
  -> write success/skip/failure to runs
```

### 历史回填和快照

```text
collector --history
  -> fetch Sina daily K data
  -> upsert historical bars

collector --snapshot
  -> use a consistent valuation date
  -> calculate positions and valuation
  -> save portfolio snapshot
```

## 9. Configuration

| 环境变量 | 用途 | 默认值 | 必填 |
|---|---|---|---|
| `FUTU_DB` | SQLite 路径 | `/data/futu.db` | 否 |
| `FUTU_PORT` | Web 端口 | `8300` | 否 |
| `FUTU_USER` | Basic Auth 用户 | `admin` | 否 |
| `FUTU_PASSWORD` | Basic Auth 密码 | 无 | 是 |
| `FEISHU_WEBHOOK` | 飞书机器人地址 | 无 | 否 |
| `FEISHU_SECRET` | 飞书签名密钥 | 无 | 否 |
| `FUTU_BRIEF_HOUR` / `FUTU_BRIEF_MINUTE` | 简报时间（北京时间） | `7` / `30` | 否 |
| `FUTU_XCHECK_PCT` | 双源价差告警阈值 | `0.5` | 否 |
| `FUTU_ALERT_ON_FAILURE` | 失败时是否告警 | `1` | 否 |
| `FUTU_HISTORY_BARS` | 每票历史 K 线根数 | `400` | 否 |
| `FUTU_BACKUP_KEEP` | 保留备份份数 | `7` | 否 |

## 10. Commands

```bash
# Start service
python -m app.web

# One-off collection jobs
python -m app.collector --quotes
python -m app.collector --history
python -m app.collector --snapshot
python -m app.collector --all

# Verification
python -m pytest tests/ -q
python -m py_compile app/*.py
git diff --check
git status --short

# Docker
docker compose up -d --build
docker compose ps
docker compose logs --tail=50
```

## 11. Verification Checklist

每次修改后优先核对：

- [ ] `git status --short` 中没有意外改动
- [ ] `python -m pytest tests/ -q` 通过
- [ ] `python -m py_compile app/*.py` 通过
- [ ] `git diff --check` 通过
- [ ] 新输入经过统一校验，不能绕过 Web/API 入口规则
- [ ] 账务不变量仍成立，尤其是不卖空和移动加权平均成本
- [ ] 新数据库字段/表有初始化、读写、迁移或兼容方案
- [ ] 调度任务重复执行不会重复记账、重复推送或破坏快照
- [ ] 缺失行情不会被当作零价格参与汇总
- [ ] 收益曲线按收盘日终结算，现金曲线按截至该日流水计算
- [ ] Docker 中 `.env` 和 `/data` 持久化路径不被覆盖
- [ ] README 或状态文档中的命令和实际入口一致

## 12. Known Issues and Operational Notes

| 问题 | 影响 | 当前判断 | 下一步 |
|---|---|---|---|
| 服务器约 21:30 出现 `collect_quotes` 飞书异常告警 | 告警内容乱码，曾出现 `ESP32-C3` | 可能是服务器 SQLite 中的异常标的、历史乱码或旧版本采集数据 | 检查 `instruments`、最近 `runs` 以及容器实际版本 |
| Basic Auth 默认是 HTTP 明文 | 不适合公网直接暴露 | 仅限内网，公网需 TLS 反代 | 使用 Caddy/nginx 或网络层限制 |
| Web 写接口没有 CSRF 防护 | 不适合不可信网络 | 项目定位为内网自托管 | 部署时限制访问范围，必要时增加 CSRF |
| SQLite 备份仍在本机 | 主机损坏可能导致备份同时丢失 | 当前只做本机保留 | 增加异机、NAS 或对象存储备份 |
| 历史回溯遇到冲正流水存在口径限制 | 历史结果可能与当前账本不同 | 页面已有提示 | 明确历史估值和冲正时间语义 |

## 13. Change History

只记录对架构、数据一致性、部署或用户行为有影响的近期变更；完整历史以 Git 为准。

| 提交 | 变更 |
|---|---|
| `d8acc3a` | 增加项目状态和部署跟踪记录 |
| `7d396ec` | 强化账务校验、行情完整性与调度可靠性 |
| `dc15918` | 补充服务器部署和更新说明 |
| `fd35fe5` | 增加 Web 新增标的入口 |

## 14. AI Change Protocol

处理新需求时按以下顺序核对：

1. 先读取本文件、相关模块和对应测试，确认现有边界。
2. 明确需求影响的是 Web、账务、数据源、数据库、调度还是部署。
3. 优先复用现有校验、事务、配置和错误处理模式。
4. 先补充能表达新行为的测试，再修改实现；对紧急修复至少增加回归测试。
5. 检查数据兼容、重复执行、缺失行情、认证和备份影响。
6. 运行验证命令，并同步更新本文件或 `PROJECT_STATUS.md` 中受影响的事实。
7. 最终说明修改文件、行为变化、验证结果和未解决风险。
