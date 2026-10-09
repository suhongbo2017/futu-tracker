# futu-tracker 项目状态记录

更新时间：2026-10-10

## 当前版本

- 本地分支：`master`
- 最新提交：`7d396ec`
- 远程仓库：`https://github.com/suhongbo2017/futu-tracker.git`
- 本地工作树：干净
- 本地测试：`27 passed`
- 编译检查：`python -m py_compile app/*.py` 通过
- 补丁检查：`git diff --check` 通过

## 已完成

### 核心功能

- SQLite 账本与移动加权平均成本计算
- 买入、卖出、分红、费用、拆合股流水
- 超卖拦截，不支持卖空
- 流水冲正与重复冲正保护
- Web 页面总览、记账、历史回溯和 JSON API
- 新增标的入口：代码自动规范化为 `US.XXX`
- 记账时直接输入新代码自动注册
- 腾讯主行情、 新浪备行情及数据源价差校验
- 历史日 K 回填与每日组合快照
- 飞书收盘简报与失败告警
- SQLite 自动备份

### 本轮优化（`7d396ec`）

- 严格校验价格、数量、手续费，拒绝 `NaN`、`Inf` 和非法数值
- 严格校验真实成交时间
- 限制 `DIV`/`FEE` 股数必须为 0
- 防止 `ADJ` 调整后持仓变成负数
- 增加重复冲正的数据库唯一约束
- Basic Auth 使用恒定时间比较
- 飞书只有收到明确成功响应才视为发送成功
- 总览和简报提示缺失行情
- 快照统一估值日期，行情不足时标记为 `partial`
- 兼容 `BRK.B` / `BRK_B` 行情键
- 修复备份 `keep=0` 边界
- 备份和每日简报结果写入 `runs`，服务重启后保持幂等
- Docker Compose 补齐运行时环境变量
- README 增加部署、更新和行情完整性说明

## Git 与部署状态

### GitHub

代码已推送到 `master`，最新远程提交为：

```text
7d396ec fix: 提升账务校验、行情完整性与调度可靠性
```

### 服务器

服务器地址：`192.168.99.15`

服务器项目目录：`/home/docker/futu-tracker`

已确认服务器曾更新到：

```text
fd35fe5 feat(web): 记账页新增「新增标的」入口
```

注意：本轮 `7d396ec` 优化代码已经推送到 GitHub，但目前没有证据表明服务器已经拉取并重建到该版本。后续更新命令：

```bash
cd /home/docker/futu-tracker
ls -lh data/futu.db data/backups/
git pull origin master
docker compose up -d --build
docker compose ps
docker compose logs --tail=50
```

更新前必须确认数据库备份存在。`.env` 和 `./data` 数据卷不应被 Git 更新或镜像重建覆盖。

## 待调查问题

### 21:30 飞书异常告警

现象：每天约 21:30 收到任务名为 `collect_quotes` 的异常告警，内容大部分乱码，并出现 `ESP32-C3`。

当前判断：

- 本地代码、测试、README 和配置中没有 `ESP32-C3` 或 `ESP32` 字样
- `collect_quotes` 的跳过标的来自服务器 SQLite 数据库 `instruments.symbol`
- `ESP32-C3` 更可能来自服务器数据库中的异常标的记录，或服务器旧版本采集数据
- 乱码可能来自服务器数据库历史编码、旧版行情解析，或飞书消息链路编码

后续检查命令：

```bash
cd /home/docker/futu-tracker

docker compose exec -T futu-tracker python - <<'PY'
import sqlite3
conn = sqlite3.connect('/data/futu.db')
print('=== instruments ===')
for row in conn.execute('SELECT code, symbol, name, active FROM instruments ORDER BY code'):
    print(row)
print('=== recent collect_quotes runs ===')
for row in conn.execute('''
    SELECT id, status, started_at, finished_at, message
    FROM runs
    WHERE kind = 'collect_quotes'
    ORDER BY id DESC
    LIMIT 20
'''):
    print(row)
PY
```

检查重点：

- 是否存在 `ESP32-C3` 或 `ESP32_C3`
- `instruments.name` 是否包含乱码替换字符
- `runs.message` 中是“跳过”还是“数据源分歧”
- 容器实际运行版本是否为 `7d396ec`

## 已知限制

- Basic Auth 默认通过 HTTP 传输，公网必须前置 TLS 反代
- 写接口没有 CSRF 防护，只适合内网或可信访问环境
- SQLite 本地备份仍需要额外复制到异机、NAS 或对象存储
- 历史回溯遇到冲正流水时存在口径限制，页面已有提示
- 当前只支持美股和 USD

## 下一步顺序

1. 在服务器确认数据库备份后，拉取 `7d396ec` 并重建容器
2. 检查服务器 `instruments` 和最近 `collect_quotes` 记录
3. 根据检查结果清理异常标的或修复行情源映射
4. 验证 21:30 告警内容是否变为正常中文且不再出现 `ESP32-C3`
5. 增加异机数据库备份策略
6. 视需要增加服务器健康状态页和告警历史查询
