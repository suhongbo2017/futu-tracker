-- futu-tracker 数据库结构（SQLite）
-- 设计原则：流水只追加（append-only），持仓与盈亏永远由流水推导
-- 币种：统一 USD（仅美股）；日期为美股交易日（YYYY-MM-DD）

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------- 标的 ----------
CREATE TABLE IF NOT EXISTS instruments (
    code       TEXT PRIMARY KEY,              -- 内部代码，如 US.NFLX
    symbol     TEXT NOT NULL,                 -- 交易所代码，如 NFLX
    name       TEXT NOT NULL,
    market     TEXT NOT NULL DEFAULT 'US',
    currency   TEXT NOT NULL DEFAULT 'USD',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- 交易流水（只追加；纠错用反向流水）----------
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL REFERENCES instruments(code),
    side        TEXT    NOT NULL CHECK (side IN ('BUY','SELL','DIV','FEE','ADJ')),
    price       REAL    NOT NULL DEFAULT 0,   -- BUY/SELL: 成交价；DIV/FEE: 金额；ADJ: 忽略
    qty         REAL    NOT NULL DEFAULT 0,   -- BUY/SELL: 股数；ADJ: 股数增减
    fee         REAL    NOT NULL DEFAULT 0,   -- 手续费（计入成本）
    trade_time  TEXT    NOT NULL,             -- ISO8601，成交时间
    note        TEXT,
    source      TEXT    NOT NULL DEFAULT 'manual',
    reverses_id INTEGER REFERENCES transactions(id),   -- 冲正：指向被冲正的流水
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_txn_code_time ON transactions(code, trade_time);
CREATE INDEX IF NOT EXISTS idx_txn_all       ON transactions(trade_time, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_txn_reverses_unique
    ON transactions(reverses_id) WHERE reverses_id IS NOT NULL;

-- ---------- 日线与快照 ----------
CREATE TABLE IF NOT EXISTS quotes_daily (
    code       TEXT NOT NULL,
    date       TEXT NOT NULL,                 -- 美股交易日
    open       REAL, high REAL, low REAL, close REAL,
    volume     REAL,
    source     TEXT NOT NULL,                 -- tencent | sina | eastmoney
    is_final   INTEGER NOT NULL DEFAULT 1,    -- 1=已收盘正式值，0=盘中暂定
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (code, date)
);

-- 每日组合快照（收盘后写入，供回溯与曲线）
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    date           TEXT NOT NULL,
    code           TEXT NOT NULL,
    qty            REAL NOT NULL,
    avg_cost       REAL NOT NULL,
    close          REAL,
    market_value   REAL,
    unrealized_pnl REAL,
    PRIMARY KEY (date, code)
);

-- 每日组合汇总（便于画曲线）
CREATE TABLE IF NOT EXISTS portfolio_summary (
    date            TEXT PRIMARY KEY,
    market_value    REAL,
    cost_basis      REAL,
    unrealized_pnl  REAL,
    realized_pnl    REAL,
    day_change_pct  REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- 运行日志（采集/任务/告警依据）----------
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                -- collect_quotes | collect_history | snapshot | notify
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',  -- running | ok | partial | failed
    message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_kind_time ON runs(kind, started_at);

-- 数据源分歧记录（交叉校验 >0.5% 时写入）
CREATE TABLE IF NOT EXISTS source_conflicts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL,
    date      TEXT NOT NULL,
    price_a   REAL, source_a TEXT,
    price_b   REAL, source_b TEXT,
    diff_pct  REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
