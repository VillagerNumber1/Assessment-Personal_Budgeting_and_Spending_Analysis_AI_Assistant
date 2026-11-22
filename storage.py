"""SQLite 存储层：建表、写入、查询。

对外只暴露四个函数，界面那边不用会 SQL：
    init()            建表（程序启动时调一次，重复调用没关系）
    add(...)          存一条，返回 id
    list_recent()     最近的流水
    month_total()     某个月的总额（单位：分）

数据库就是一个文件：data/app.db。复制它 = 备份，删掉它 = 重来。
"""
import os
import sqlite3
from datetime import date
from pathlib import Path

from config import DATA_DIR

# 默认用 data/app.db；测试时可以用环境变量指到别处，别弄脏真数据
DB_PATH = Path(os.environ.get("BILL_DB", str(DATA_DIR / "app.db")))

SCHEMA = """
create table if not exists transactions (
    id          integer primary key autoincrement,
    text        text    not null,              -- 用户原话
    amount_cents integer,                      -- 金额（分），整数避免浮点误差
    occurred_at text    not null,              -- 消费日期 YYYY-MM-DD
    category    text,                          -- 类别，可空
    status      text    not null default 'confirmed',  -- draft / confirmed
    created_at  text    not null default (datetime('now', 'localtime')),
    updated_at  text                                    -- 只在用户编辑过之后才有值
);

create index if not exists idx_tx_date on transactions(occurred_at);
create index if not exists idx_tx_category on transactions(category);

-- 编辑记录：只追加，不改不删，给用户看「这笔账变过什么」
create table if not exists transaction_logs (
    id               integer primary key autoincrement,
    tx_id            integer not null,
    old_text         text,
    old_amount_cents integer,
    new_text         text,
    new_amount_cents integer,
    changed_at       text    not null default (datetime('now', 'localtime'))
);

create index if not exists idx_log_tx on transaction_logs(tx_id);
"""


def _connect() -> sqlite3.Connection:
    """每次开一个连接，用完就关。桌面单用户场景，连接池是多余的。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row          # 让查询结果能按列名取值
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """老库缺列时补上。开发期改表结构的土办法，够用。"""
    cols = {row["name"] for row in con.execute("pragma table_info(transactions)")}
    if "updated_at" not in cols:
        con.execute("alter table transactions add column updated_at text")


def init() -> None:
    """建表 + 补列。用 create table if not exists，所以重复调用是安全的。"""
    con = _connect()
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        con.commit()
    finally:
        con.close()


def add(text: str,
        amount_cents: int | None = None,
        occurred_at: str | None = None,
        category: str | None = None,
        status: str = "confirmed") -> int:
    """存一条账，返回它的 id。日期不传就记今天。"""
    con = _connect()
    try:
        cur = con.execute(
            "insert into transactions (text, amount_cents, occurred_at, category, status)"
            " values (?, ?, ?, ?, ?)",
            (text, amount_cents, occurred_at or date.today().isoformat(), category, status),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def get(tx_id: int) -> dict | None:
    """按 id 取一条。"""
    con = _connect()
    try:
        row = con.execute("select * from transactions where id = ?", (tx_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def update(tx_id: int, text: str, amount_cents: int | None,
           category: str | None = None) -> bool:
    """改一条账（做了什么 + 金额）。

    updated_at 由服务端写，用户改过才有值——这就是「修改时间只在编辑后出现」。
    返回 False 表示这条不存在。
    """
    con = _connect()
    try:
        old = con.execute("select * from transactions where id = ?", (tx_id,)).fetchone()
        if old is None:
            return False
        con.execute(
            "update transactions set text = ?, amount_cents = ?, category = ?,"
            " updated_at = datetime('now', 'localtime') where id = ?",
            (text, amount_cents, category, tx_id),
        )
        # 改之前的样子记一笔，只追加不改，这就是「编辑记录」
        con.execute(
            "insert into transaction_logs"
            " (tx_id, old_text, old_amount_cents, new_text, new_amount_cents)"
            " values (?, ?, ?, ?, ?)",
            (tx_id, old["text"], old["amount_cents"], text, amount_cents),
        )
        con.commit()
        return True
    finally:
        con.close()


def list_recent(limit: int = 50) -> list[dict]:
    """最近的流水，新的在上面。每条带上它的编辑记录。"""
    con = _connect()
    try:
        rows = [dict(r) for r in con.execute(
            "select * from transactions order by id desc limit ?", (limit,)
        ).fetchall()]
        for row in rows:
            row["logs"] = [dict(x) for x in con.execute(
                "select * from transaction_logs where tx_id = ? order by id desc",
                (row["id"],),
            ).fetchall()]
        return rows
    finally:
        con.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """执行一条查询并返回结果。

    给 query.py 用：SQL 由代码拼好、参数全部绑定，用户输入永远不进 SQL 字符串。
    """
    con = _connect()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def stats(year_month: str | None = None) -> dict:
    """分类聚合 + 近 6 个月趋势，都只算 confirmed。"""
    ym = year_month or date.today().strftime("%Y-%m")
    con = _connect()
    try:
        cats = con.execute(
            "select coalesce(category, '未分类') as category,"
            " sum(amount_cents) as total_cents, count(*) as count"
            " from transactions where status = 'confirmed' and occurred_at like ?"
            " group by category order by total_cents desc",
            (f"{ym}%",),
        ).fetchall()
        trend = con.execute(
            "select substr(occurred_at, 1, 7) as month, sum(amount_cents) as total_cents"
            " from transactions where status = 'confirmed'"
            " group by month order by month desc limit 6"
        ).fetchall()
        return {
            "month": ym,
            "total_cents": sum(r["total_cents"] or 0 for r in cats),
            "by_category": [dict(r) for r in cats],
            "trend": [dict(r) for r in reversed(trend)],
        }
    finally:
        con.close()


def month_total(year_month: str | None = None) -> int:
    """某个月的支出总额（分）。不传就是本月。只统计 confirmed。"""
    ym = year_month or date.today().strftime("%Y-%m")
    con = _connect()
    try:
        row = con.execute(
            "select coalesce(sum(amount_cents), 0) as total from transactions"
            " where status = 'confirmed' and occurred_at like ?",
            (f"{ym}%",),
        ).fetchone()
        return int(row["total"])
    finally:
        con.close()


def yuan(cents: int | None) -> str:
    """分 -> 给人看的「12.34」。"""
    if cents is None:
        return "—"
    return f"{cents / 100:.2f}"
