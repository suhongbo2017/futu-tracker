# futu-tracker

自托管的美股持仓跟踪服务：采集行情 → 存入轻量数据库 → 网页录入买卖 → 计算盈亏 → 飞书推送。

> 取代原先「本地 Windows 定时脚本生成静态 HTML」的做法（`~/.pi/agent/skills/futu-daily-report`）。

## 设计要点

| 主题 | 决定 |
|---|---|
| 用途 | **只记账**（手工录入成交），不接真实下单 |
| 币种 / 市场 | 统一 **USD**，仅美股（结构可扩展） |
| 成本法 | **移动加权平均**；已实现 / 浮动盈亏分开统计 |
| 账本 | **只追加**（append-only），纠错用反向流水（冲正），不支持卖空 |
| 数据源 | 腾讯（主）+ 新浪（备/校验），批量一次拉全部标的 |
| 历史日K | 新浪 `US_MinKService.getDailyK`（未复权，全历史） |
| 数据库 | **SQLite**（WAL），每日自动备份（VACUUM INTO，保留 N 份） |
| 推送 | 飞书自定义机器人；默认**北京 7:30**（美股收盘后 04:00/05:00）发送 |
| 访问 | `http://<host>:8300` + HTTP Basic 鉴权（**请仅在内网使用或前置 TLS 反代**） |

## 目录

```
app/
  schema.sql     数据库结构
  config.py      环境变量配置
  db.py          SQLite 访问层（含事务、备份、标的自动注册）
  sources.py     行情数据源（腾讯主 / 新浪备与历史日K）+ 双源交叉校验
  accounting.py  移动加权平均账务引擎
  collector.py   采集任务（实时快照 / 历史回填 / 组合快照）
  web.py         Web 页面、JSON API 与内置调度器（仅标准库）
  notify.py      飞书推送（HMAC 加签）
  healthcheck.py 容器健康检查
scripts/         从旧 stocks.txt 播种（seed_from_stocks_txt.py）
tests/           单元测试（python -m pytest tests/ -q）
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
| `FUTU_XCHECK_PCT` | 双源价差告警阈值(%) | `0.5` |
| `FUTU_ALERT_ON_FAILURE` | 采集/推送失败是否告警 | `1` |
| `FUTU_HISTORY_BARS` | 每票回填日K根数 | `400` |
| `FUTU_BACKUP_KEEP` | 保留最近备份份数 | `7` |

## 运行

```bash
python -m app.web                    # 启动 Web + 内置调度器（默认 :8300，含每 30 分钟采集与每日简报）
python -m app.collector --quotes     # 采集一次实时行情
python -m app.collector --history    # 回填/增量日K
python -m app.collector --snapshot   # 按库内最新收盘价生成组合快照
python -m app.collector --all        # 以上全部执行

docker compose up -d --build         # 或容器部署（./data 挂载为 /data）
```

## 部署与更新（服务器）

首次部署：

```bash
git clone https://github.com/suhongbo2017/futu-tracker.git
cd futu-tracker
echo 'FUTU_PASSWORD=你的密码' >> .env        # 必填，其余变量可选（见上表）
docker compose up -d --build
```

日常更新（代码只走 Git，数据库与配置不受影响）：

```bash
git pull
# 或 git reset --hard origin/master（丢弃本地代码改动时）
docker compose up -d --build     # 自动重建容器，./data 数据卷与 .env 配置原样保留
```

> 注意：仓库为 public，部署机仅凭 Basic 鉴权，请勿将 8300 端口暴露到公网；
> `docker-compose.yml` 属于代码仓库，个性化配置请写入 `.env`（在 `.gitignore` 中），避免被更新覆盖。

## 记账与标的

- **新增标的**：记账页顶部「➕ 新增标的」可主动注册（代码 + 可选名称；采集到行情后自动补真实名称）；也可直接在记账单「标的」框输入新代码提交，自动注册并记账。
- **记账**：买入/卖出/分红（DIV）/费用（FEE）/拆合股（ADJ），冲正纠错；卖出不允许超过当前持仓（不支持卖空）。

## 开发与测试

```bash
python -m pytest tests/ -q           # 全部测试（项目零第三方运行时依赖，pytest 仅开发用）
```

## 已知限制与安全说明

- **鉴权**：HTTP Basic 走明文，公网部署必须前置 TLS 反代（Caddy/nginx）或仅限内网；写接口无 CSRF 防护，勿暴露到不可信网络。
- **代码规范**：内部代码统一 `US.XXX`（如 `US.AAPL`）；网页录入裸代码（如 `AAPL`）会自动补全 `US.` 前缀。特殊如 `BRK.B` 请按 `US.BRK.B` 录入。
- **拆合股**：历史日K为未复权，拆股/合股后请务必记一条 `ADJ` 流水，否则浮动盈亏会出现幽灵波动。
- **回溯口径**：被冲正的交易在「冲正日之前」的历史回溯中可能复现，页面会给出提示；精确数据以当日收盘快照为准。
- **不支持卖空**：Web 录入超卖会被拒绝；账务引擎对历史异常流水仍有截断兜底。