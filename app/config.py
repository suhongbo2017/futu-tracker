# -*- coding: utf-8 -*-
"""集中配置：一律走环境变量，便于容器部署与密钥隔离。"""
import os

def _env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default

# 数据与输出
DB_PATH = _env("FUTU_DB", "/data/futu.db")
STOCKS_FILE = _env("FUTU_STOCKS_FILE", "")          # 可选：从旧 stocks.txt 播种
DATA_DIR = os.path.dirname(DB_PATH) or "."

# 时区与调度（美股收盘：北京 04:00 夏令时 / 05:00 冬令时）
TZ = _env("FUTU_TZ", "Asia/Shanghai")
BRIEF_HOUR = int(_env("FUTU_BRIEF_HOUR", "7"))      # 收盘后简报小时（北京时间）
BRIEF_MINUTE = int(_env("FUTU_BRIEF_MINUTE", "30"))

# 推送：飞书自定义机器人
FEISHU_WEBHOOK = _env("FEISHU_WEBHOOK", "")
FEISHU_SECRET = _env("FEISHU_SECRET", "")           # 可选：签名校验
ALERT_ON_FAILURE = _env("FUTU_ALERT_ON_FAILURE", "1") == "1"

# 数据源
CROSS_CHECK_TOLERANCE_PCT = float(_env("FUTU_XCHECK_PCT", "0.5"))   # 双源价差告警阈值
HTTP_TIMEOUT = float(_env("FUTU_HTTP_TIMEOUT", "12"))
HTTP_MIN_INTERVAL = float(_env("FUTU_HTTP_INTERVAL", "1.0"))        # 请求最小间隔（秒）
HISTORY_BARS = int(_env("FUTU_HISTORY_BARS", "400"))                # 每票回填的日K根数（约 1.5 年）
BACKUP_KEEP = int(_env("FUTU_BACKUP_KEEP", "7"))                   # 保留最近 N 份数据库备份

# Web
APP_HOST = _env("FUTU_HOST", "0.0.0.0")
APP_PORT = int(_env("FUTU_PORT", "8300"))
AUTH_USER = _env("FUTU_USER", "admin")
AUTH_PASSWORD = _env("FUTU_PASSWORD", "")           # 为空则拒绝启动（避免无鉴权暴露）