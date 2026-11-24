"""SQLite 存储层：建表、写入、查询。

对外只暴露四个函数，界面那边不用会 SQL：
    init()            建表（程序启动时调一次，重复调用没关系）
    add(...)          存一条，返回 id
    list_recent()     最近的流水
    month_total()     某个月的总额（单位：分）

数据库就是一个文件：data/app.db。复制它 = 备份，删掉它 = 重来。
"""
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from config import DATA_DIR

# 默认用 data/app.db；测试时可以用环境变量指到别处，别弄脏真数据
DB_PATH = Path(os.environ.get("BILL_DB", str(DATA_DIR / "app.db")))

# 账目方向：只有这两种。金额永远存正数，方向靠这个字段区分
KINDS = ("expense", "income")
KIND_LABEL = {"expense": "支出", "income": "收入"}

SCHEMA = """
create table if not exists transactions (
    id          integer primary key autoincrement,
    text        text    not null,              -- 用户原话
    amount_cents integer,                      -- 金额（分），整数避免浮点误差
    occurred_at text    not null,              -- 记账日期 YYYY-MM-DD
    category    text,                          -- 类别，可空
    kind        text    not null default 'expense',  -- expense 支出 / income 收入
    status      text    not null default 'confirmed',  -- draft / confirmed
    created_at  text    not null default (datetime('now', 'localtime')),
    updated_at  text,                                   -- 只在用户编辑过之后才有值
    deleted_at  text                                    -- 软删除：有值就代表已删
);

create index if not exists idx_tx_date on transactions(occurred_at);
create index if not exists idx_tx_category on transactions(category);

-- 操作历史：增 / 改 / 删 / 恢复，全都留痕，并且可以撤销
create table if not exists operations (
    id          integer primary key autoincrement,
    kind        text    not null,        -- create / edit / delete / restore
    tx_id       integer not null,
    before_json text,                    -- 操作前的快照
    after_json  text,                    -- 操作后的快照
    created_at  text    not null default (datetime('now', 'localtime')),
    undone      integer not null default 0
);

create index if not exists idx_op_tx on operations(tx_id);
create index if not exists idx_op_time on operations(created_at);

-- 查账历史：用户问过什么。只记问题，不记答案（答案每次现算，免得拿旧数字）
create table if not exists queries (
    id          integer primary key autoincrement,
    question    text    not null,
    created_at  text    not null default (datetime('now', 'localtime'))
);

create index if not exists idx_q_time on queries(created_at);

-- 预算：只存「单独设过」的月份。
-- 没记录的月份跟随设置里的「每月默认预算」，所以改默认值会连带影响所有没单独设过的月份。
-- 一旦某个月单独设过，它就固定下来，之后改默认值不会动它——历史不能被未来的设置改写。
create table if not exists budgets (
    month        text    primary key,     -- 2026-11
    amount_cents integer not null,        -- 本月总预算（分）
    updated_at   text    not null default (datetime('now', 'localtime'))
);
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
    if "deleted_at" not in cols:
        con.execute("alter table transactions add column deleted_at text")
    if "kind" not in cols:
        # 老库全是记账（支出），默认值正好把历史数据都标成 expense
        con.execute(
            "alter table transactions add column kind text not null default 'expense'")


def _snapshot(con: sqlite3.Connection, tx_id: int) -> dict | None:
    """取一条账的完整快照，用于操作留痕和撤销。"""
    row = con.execute("select * from transactions where id = ?", (tx_id,)).fetchone()
    return dict(row) if row else None


def _log(con: sqlite3.Connection, kind: str, tx_id: int,
         before: dict | None, after: dict | None) -> None:
    con.execute(
        "insert into operations (kind, tx_id, before_json, after_json)"
        " values (?, ?, ?, ?)",
        (kind, tx_id,
         json.dumps(before, ensure_ascii=False) if before else None,
         json.dumps(after, ensure_ascii=False) if after else None),
    )


def init() -> None:
    """建表 + 补列。用 create table if not exists，所以重复调用是安全的。"""
    con = _connect()
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        con.commit()
    finally:
        con.close()
    prune_operations(66)          # 只留最近 66 天的操作记录


def add(text: str,
        amount_cents: int | None = None,
        occurred_at: str | None = None,
        category: str | None = None,
        status: str = "confirmed",
        kind: str = "expense") -> int:
    """存一条账，返回它的 id。日期不传就记今天；kind 不传就是支出。"""
    con = _connect()
    try:
        cur = con.execute(
            "insert into transactions"
            " (text, amount_cents, occurred_at, category, kind, status)"
            " values (?, ?, ?, ?, ?, ?)",
            (text, amount_cents, occurred_at or date.today().isoformat(),
             category, kind if kind in KINDS else "expense", status),
        )
        new_id = int(cur.lastrowid)
        _log(con, "create", new_id, None, _snapshot(con, new_id))
        con.commit()
        return new_id
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
           category: str | None = None, kind: str | None = None,
           occurred_at: str | None = None) -> bool:
    """改一条账（做了什么 + 金额 + 方向 + 时间）。

    kind 传 None 表示「不改方向」，保留原来的；occurred_at 同理。
    updated_at 由服务端写，用户改过才有值——这就是「修改时间只在编辑后出现」。
    返回 False 表示这条不存在。
    """
    con = _connect()
    try:
        old = con.execute("select * from transactions where id = ?", (tx_id,)).fetchone()
        if old is None:
            return False
        new_kind = kind if kind in KINDS else old["kind"]
        new_when = occurred_at or old["occurred_at"]
        con.execute(
            "update transactions set text = ?, amount_cents = ?, category = ?, kind = ?,"
            " occurred_at = ?, updated_at = datetime('now', 'localtime') where id = ?",
            (text, amount_cents, category, new_kind, new_when, tx_id),
        )
        # 改之前和改之后都留一份，撤销时能精确还原
        _log(con, "edit", tx_id, dict(old), _snapshot(con, tx_id))
        con.commit()
        return True
    finally:
        con.close()


def delete(tx_id: int) -> bool:
    """软删除：只打时间戳，数据还在，随时能撤销。"""
    con = _connect()
    try:
        old = _snapshot(con, tx_id)
        if old is None or old.get("deleted_at"):
            return False
        con.execute(
            "update transactions set deleted_at = datetime('now', 'localtime') where id = ?",
            (tx_id,))
        _log(con, "delete", tx_id, old, None)
        con.commit()
        return True
    finally:
        con.close()


def restore(tx_id: int) -> bool:
    """撤销删除。"""
    con = _connect()
    try:
        old = _snapshot(con, tx_id)
        if old is None or not old.get("deleted_at"):
            return False
        con.execute("update transactions set deleted_at = null where id = ?", (tx_id,))
        _log(con, "restore", tx_id, None, _snapshot(con, tx_id))
        con.commit()
        return True
    finally:
        con.close()


def operations(limit: int = 100) -> list[dict]:
    """全局操作历史，新的在上面，带上当时的文字方便一眼看懂。"""
    con = _connect()
    try:
        rows = con.execute(
            "select o.*, t.text as tx_text, t.deleted_at as tx_deleted"
            " from operations o left join transactions t on t.id = o.tx_id"
            " order by o.id desc limit ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def undo(op_id: int) -> bool:
    """撤销一条操作。只允许撤销还没撤销过的。"""
    con = _connect()
    try:
        op = con.execute("select * from operations where id = ?", (op_id,)).fetchone()
        if op is None or op["undone"]:
            return False

        before = json.loads(op["before_json"]) if op["before_json"] else None
        kind, tx_id = op["kind"], op["tx_id"]

        if kind in ("create", "restore"):
            con.execute(
                "update transactions set deleted_at = datetime('now','localtime') where id = ?",
                (tx_id,))
        elif kind == "delete":
            con.execute("update transactions set deleted_at = null where id = ?", (tx_id,))
        elif kind == "edit" and before:
            con.execute(
                "update transactions set text = ?, amount_cents = ?, category = ?,"
                " updated_at = datetime('now','localtime') where id = ?",
                (before.get("text"), before.get("amount_cents"),
                 before.get("category"), tx_id))

        con.execute("update operations set undone = 1 where id = ?", (op_id,))
        con.commit()
        return True
    finally:
        con.close()


def delete_operation(op_id: int) -> bool:
    """删掉一条历史记录本身。只清理记录，不回滚账目。"""
    con = _connect()
    try:
        cur = con.execute("delete from operations where id = ?", (op_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def prune_operations(days: int = 66) -> int:
    """只保留最近 N 天的操作记录，再老的清掉（题目要求 66 天）。"""
    con = _connect()
    try:
        cur = con.execute(
            "delete from operations where created_at < datetime('now','localtime', ?)",
            (f"-{days} days",))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def list_recent(limit: int = 50, offset: int = 0) -> list[dict]:
    """最近的流水（不含已删除），新的在上面。每条带上它的修改记录。"""
    con = _connect()
    try:
        rows = [dict(r) for r in con.execute(
            "select * from transactions where deleted_at is null"
            " order by id desc limit ? offset ?", (limit, offset)
        ).fetchall()]
        for row in rows:
            row["logs"] = _edit_logs(con, row["id"])
        return rows
    finally:
        con.close()


def count_transactions() -> int:
    """一共多少条（不含已删除），给翻页用。"""
    con = _connect()
    try:
        return int(con.execute(
            "select count(*) from transactions where deleted_at is null").fetchone()[0])
    finally:
        con.close()


def _edit_logs(con: sqlite3.Connection, tx_id: int) -> list[dict]:
    """把 operations 里的 edit 记录转成前端认识的形状。"""
    rows = con.execute(
        "select * from operations where tx_id = ? and kind = 'edit' and undone = 0"
        " order by id desc", (tx_id,)).fetchall()
    logs = []
    for r in rows:
        before = json.loads(r["before_json"]) if r["before_json"] else {}
        after = json.loads(r["after_json"]) if r["after_json"] else {}
        logs.append({
            "changed_at": r["created_at"],
            "old_text": before.get("text"),
            "old_amount_cents": before.get("amount_cents"),
            "old_occurred_at": before.get("occurred_at"),
            "old_kind": before.get("kind"),
            "new_text": after.get("text"),
            "new_amount_cents": after.get("amount_cents"),
            "new_occurred_at": after.get("occurred_at"),
            "new_kind": after.get("kind"),
        })
    return logs


def query(sql: str, params: tuple = ()) -> list[dict]:
    """执行一条查询并返回结果。

    给 query.py 用：SQL 由代码拼好、参数全部绑定，用户输入永远不进 SQL 字符串。
    """
    con = _connect()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def add_query(question: str) -> int:
    """记一条查账历史。只留问题，不存答案。"""
    con = _connect()
    try:
        cur = con.execute("insert into queries (question) values (?)", (question,))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def queries(limit: int = 60) -> list[dict]:
    """最近的查账历史，新的在上面。"""
    con = _connect()
    try:
        rows = con.execute(
            "select * from queries order by id desc limit ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_query(query_id: int) -> bool:
    con = _connect()
    try:
        cur = con.execute("delete from queries where id = ?", (query_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def count_dateless(day: str) -> int:
    """某一天里「只记了日期、没记时间」的账有多少笔。

    钟点查询只能用得上带时分的账，这种账没法判断属不属于那个钟点，
    所以不进结果，但要告诉用户一声。
    """
    con = _connect()
    try:
        return int(con.execute(
            "select count(*) from transactions"
            " where status = 'confirmed' and deleted_at is null and occurred_at = ?",
            (day,)).fetchone()[0])
    finally:
        con.close()


def stats(year_month: str | None = None) -> dict:
    """某个月的分类聚合 + 近 6 个月趋势 + 支出 / 收入 / 结余。

    分类横条和趋势只算**支出**：那是在回答「钱花在哪」，收入没有「花在哪」这回事。
    """
    ym = year_month or date.today().strftime("%Y-%m")
    con = _connect()
    try:
        cats = con.execute(
            "select coalesce(category, '未分类') as category,"
            " sum(amount_cents) as total_cents, count(*) as count"
            " from transactions where status = 'confirmed' and deleted_at is null"
            " and kind = 'expense' and occurred_at like ?"
            " group by category order by total_cents desc",
            (f"{ym}%",),
        ).fetchall()
        sums = con.execute(
            "select kind, coalesce(sum(amount_cents), 0) as total_cents,"
            " count(*) as count from transactions"
            " where status = 'confirmed' and deleted_at is null and occurred_at like ?"
            " group by kind", (f"{ym}%",)).fetchall()
        trend = con.execute(
            "select substr(occurred_at, 1, 7) as month,"
            " sum(case when kind = 'expense' then amount_cents else 0 end) as total_cents,"
            " sum(case when kind = 'expense' then amount_cents else 0 end) as expense_cents,"
            " sum(case when kind = 'income' then amount_cents else 0 end) as income_cents"
            " from transactions where status = 'confirmed' and deleted_at is null"
            " group by month order by month desc limit 6"
        ).fetchall()

        by_kind = {r["kind"]: r["total_cents"] for r in sums}
        expense = by_kind.get("expense", 0)
        income = by_kind.get("income", 0)
        return {
            "month": ym,
            # total_cents 保留给老代码：它就是「本月支出」
            "total_cents": expense,
            "expense_cents": expense,
            "income_cents": income,
            "balance_cents": income - expense,
            "by_category": [dict(r) for r in cats],
            "trend": [dict(r) for r in reversed(trend)],
        }
    finally:
        con.close()


def month_total(year_month: str | None = None, kind: str = "expense") -> int:
    """某个月的总额（分），按方向算。不传就是本月支出。只统计 confirmed。"""
    ym = year_month or date.today().strftime("%Y-%m")
    con = _connect()
    try:
        row = con.execute(
            "select coalesce(sum(amount_cents), 0) as total from transactions"
            " where status = 'confirmed' and deleted_at is null"
            " and kind = ? and occurred_at like ?",
            (kind if kind in KINDS else "expense", f"{ym}%"),
        ).fetchone()
        return int(row["total"])
    finally:
        con.close()


def get_budget(year_month: str) -> int | None:
    """某个月单独设过的预算（分）。没设过返回 None，由调用方去用默认值。"""
    con = _connect()
    try:
        row = con.execute(
            "select amount_cents from budgets where month = ?", (year_month,)).fetchone()
        return int(row["amount_cents"]) if row else None
    finally:
        con.close()


def set_budget(year_month: str, amount_cents: int | None) -> None:
    """设某个月的预算。传 None 或 0 就是删掉，让它回到「跟随默认值」。"""
    con = _connect()
    try:
        if not amount_cents:
            con.execute("delete from budgets where month = ?", (year_month,))
        else:
            con.execute(
                "insert into budgets (month, amount_cents, updated_at)"
                " values (?, ?, datetime('now', 'localtime'))"
                " on conflict(month) do update set"
                " amount_cents = excluded.amount_cents, updated_at = excluded.updated_at",
                (year_month, int(amount_cents)),
            )
        con.commit()
    finally:
        con.close()


def yuan(cents: int | None) -> str:
    """分 -> 给人看的「12.34」。"""
    if cents is None:
        return "—"
    return f"{cents / 100:.2f}"
