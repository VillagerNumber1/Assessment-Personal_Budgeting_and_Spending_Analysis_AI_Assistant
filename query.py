"""自然语言查账：中文问题 -> 查询条件(DSL) -> SQL -> 数字 -> 一句话。

这一层**只做翻译，不编数字**：
    问题  ──规则解析──▶ DSL ──代码拼 SQL──▶ 数据库算 ──模板──▶ 一句话

DSL 长这样：
    {"metric": "sum", "group_by": "category",
     "start": "2026-10-01", "end": "2026-10-31", "label": "上个月",
     "categories": ["餐饮"]}

现在纯规则，能覆盖常见问法；覆盖不了的以后交给模型出 DSL（模型也只出 DSL，
SQL 仍然由这里拼，白名单 + 参数绑定，不让模型碰 SQL）。
"""
import re
from datetime import date, timedelta

import storage
from parser import CATEGORY_WORDS

# 聚合方式 -> SQL 片段
AGG_SQL = {
    "sum": "sum(amount_cents)",
    "count": "count(*)",
    "avg": "avg(amount_cents)",
}


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def parse_time(q: str, today: date) -> tuple[date, date, str]:
    """认出时间范围。认不出就默认「这个月」。"""
    if "上个月" in q or "上月" in q:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start, end = _month_range(y, m)
        return start, end, "上个月"
    if "本月" in q or "这个月" in q or "当月" in q:
        start, end = _month_range(today.year, today.month)
        return start, end, "这个月"
    if "去年" in q:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "去年"
    if "今年" in q:
        return date(today.year, 1, 1), today, "今年"

    m = re.search(r"近\s*(\d+)\s*天", q)
    if m:
        n = max(1, int(m.group(1)))
        return today - timedelta(days=n - 1), today, f"近{n}天"

    for word, offset in (("今天", 0), ("昨天", 1), ("前天", 2)):
        if word in q:
            day = today - timedelta(days=offset)
            return day, day, word

    m = re.search(r"(\d{1,2})\s*月份?", q)
    if m:
        month = int(m.group(1))
        year = today.year if month <= today.month else today.year - 1
        start, end = _month_range(year, month)
        return start, end, f"{month}月"

    start, end = _month_range(today.year, today.month)
    return start, end, "这个月"


def parse_category(q: str) -> list[str]:
    """认出类别。命中类别名或任一关键词都算。"""
    hits: list[str] = []
    for category, words in CATEGORY_WORDS.items():
        if category in q or any(w in q for w in words):
            hits.append(category)
    return hits


def parse_metric(q: str) -> str:
    if "平均" in q:
        return "avg"
    if any(w in q for w in ("几笔", "几次", "多少笔", "笔数", "多少次")):
        return "count"
    return "sum"


def parse_group(q: str) -> str:
    if any(w in q for w in ("哪类", "哪个类", "哪一类", "什么类别", "分类", "类别")):
        return "category"
    if any(w in q for w in ("每月", "按月", "每个月")):
        return "month"
    return "none"


def parse_question(q: str, today: date | None = None) -> dict:
    """一句话 -> DSL。"""
    today = today or date.today()
    start, end, label = parse_time(q, today)
    return {
        "question": q,
        "metric": parse_metric(q),
        "group_by": parse_group(q),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "categories": parse_category(q),
    }


def run(dsl: dict) -> list[dict]:
    """按 DSL 查库。SQL 全部由代码拼，参数绑定，用户输入不进 SQL。"""
    where = ["status = 'confirmed'", "occurred_at between ? and ?"]
    params: list = [dsl["start"], dsl["end"]]

    if dsl["categories"]:
        marks = ",".join("?" * len(dsl["categories"]))
        where.append(f"category in ({marks})")
        params.extend(dsl["categories"])

    agg = AGG_SQL[dsl["metric"]]
    where_sql = " and ".join(where)

    if dsl["group_by"] == "category":
        sql = (f"select coalesce(category, '未分类') as key, {agg} as value,"
               f" count(*) as count from transactions where {where_sql}"
               f" group by key order by value desc")
    elif dsl["group_by"] == "month":
        sql = (f"select substr(occurred_at, 1, 7) as key, {agg} as value,"
               f" count(*) as count from transactions where {where_sql}"
               f" group by key order by key")
    else:
        sql = (f"select {agg} as value, count(*) as count"
               f" from transactions where {where_sql}")

    return storage.query(sql, tuple(params))


def to_sentence(dsl: dict, rows: list[dict]) -> str:
    """把结果说成人话。数字全部来自 SQL，这里只负责串句子。"""
    y = storage.yuan
    label = dsl["label"]

    if not rows:
        return f"{label}没有记录"

    if dsl["group_by"] == "category":
        top = rows[0]
        return (f"{label}花得最多的是「{top['key']}」，"
                f"{y(top['value'])} 元，共 {top['count']} 笔")

    if dsl["group_by"] == "month":
        parts = [f"{r['key']}：{y(r['value'])} 元" for r in rows]
        return f"{label}" + "，".join(parts)

    row = rows[0]
    if not row["count"]:
        return f"{label}没有记录"
    if dsl["metric"] == "count":
        return f"{label}共 {row['count']} 笔"

    cat = "、".join(dsl["categories"])
    value = row["value"] or 0
    if dsl["metric"] == "avg":
        who = f"{cat}的" if cat else ""
        return f"{label}{who}平均每笔 {y(round(value))} 元，共 {row['count']} 笔"
    if cat:
        return f"{label}{cat}一共 {y(value)} 元，共 {row['count']} 笔"
    return f"{label}一共 {y(value)} 元，共 {row['count']} 笔"


def ask(q: str, today: date | None = None) -> dict:
    """完整流程：问题 -> DSL -> 查询 -> 回答。返回给前端展示。"""
    dsl = parse_question(q, today)
    rows = run(dsl)
    return {"question": q, "dsl": dsl, "rows": rows, "answer": to_sentence(dsl, rows)}
