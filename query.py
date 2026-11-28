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
from datetime import date, datetime, timedelta

import storage
from parser import CATEGORY_WORDS, find_time

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


def _one_day(year: int, month: int, day: int) -> date | None:
    """拼一个日期，拼不出来（2 月 30 日）就返回 None。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _day_of_month(q: str) -> int | None:
    """句子里写的是「25 日 / 25 号」的那一天。"""
    m = re.search(r"(?<!\d)(\d{1,2})\s*[日号]", q)
    return int(m.group(1)) if m else None


def parse_time(q: str, today: date) -> tuple[date, date, str]:
    """认出时间范围。认不出就默认「这个月」。"""
    if "上个月" in q or "上月" in q:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        # 「上个月 25 号」比「上个月」更具体：能定到天就定到天
        one = _one_day(y, m, _day_of_month(q) or 0)
        if one:
            return one, one, f"上月{one.day}日"
        start, end = _month_range(y, m)
        return start, end, "上个月"
    if "本月" in q or "这个月" in q or "当月" in q:
        one = _one_day(today.year, today.month, _day_of_month(q) or 0)
        if one:
            return one, one, f"{one.day}日"
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

    # 「11月25日 / 11月25号」：年月日都写了，年份按今年算
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        one = _one_day(today.year, month, day)
        if one:
            return one, one, f"{month}月{day}日"
        # 写了个不存在的日期（2 月 30 日）：不能退到「当月 30 号」去编一个日期，
        # 退回「这个月」，至少不会答成另一个日子
        start, end = _month_range(today.year, today.month)
        return start, end, "这个月"

    # 只写了「25日 / 25号」：按**当年当月**算（用户 2026-11-26 定的规矩）
    m = re.search(r"(?<!\d)(\d{1,2})\s*[日号]", q)
    if m:
        day = int(m.group(1))
        try:
            one = date(today.year, today.month, day)
        except ValueError:
            one = None
        if one:
            return one, one, f"{day}日"

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
    # 「还剩多少」「结余」问的是收入减支出，不是支出合计 —— 这条要排在最前面
    if any(w in q for w in ("剩下", "剩余", "还剩", "结余", "余额", "剩多少", "超支")):
        return "balance"
    if "平均" in q:
        return "avg"
    if any(w in q for w in ("几笔", "几次", "多少笔", "笔数", "多少次")):
        return "count"
    return "sum"


def parse_kind(q: str) -> str:
    """问的是收入还是支出。默认按支出算——「花了多少」永远默认支出。"""
    if any(w in q for w in ("收入", "进账", "赚了", "赚了多少", "收了", "到账",
                             "工资", "红包", "退款", "报销", "奖金")):
        return "income"
    return "expense"


def parse_group(q: str) -> str:
    if any(w in q for w in ("哪类", "哪个类", "哪一类", "什么类别", "分类", "类别")):
        return "category"
    if any(w in q for w in ("每月", "按月", "每个月")):
        return "month"
    return "none"


# 「21点」问的是几点到几点：用户 2026-11-24 定为「21点到23点」，即 2 小时窗口
HOUR_WINDOW = 2


def _hour_range(day: date, hour: int) -> dict:
    """把「某天的某个整点」变成一个 [HH:00, HH+2:00) 的区间。"""
    start = datetime(day.year, day.month, day.day, hour, 0)
    end = start + timedelta(hours=HOUR_WINDOW)
    # 跨零点时写清楚「次日」，不然 22:00~00:00 看着像零长度
    tail = f"次日 {end:%H:%M}" if end.date() != start.date() else f"{end:%H:%M}"
    return {
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%Y-%m-%d %H:%M"),
        "label": f"{day.isoformat()} {hour:02d}:00~{tail} ",   # 末尾留空格，拼「一共」时不断开
    }


def parse_question(q: str, today: date | None = None) -> dict:
    """一句话 -> DSL。

    除了「哪个月 / 哪天」，还认「几点」：句子里有 `21点` 这类说法、
    而且时间范围正好是某一天，就收窄成 2 小时窗口。
    """
    today = today or date.today()
    start, end, label = parse_time(q, today)

    base = {
        "question": q,
        "metric": parse_metric(q),
        "group_by": parse_group(q),
        "kind": parse_kind(q),
        "categories": parse_category(q),
    }

    hm = find_time(q)
    if hm and start == end:                 # 同一天 + 有钟点，才收窄
        return {**base, **_hour_range(start, hm[0])}

    return {**base, "start": start.isoformat(), "end": end.isoformat(), "label": label}


def end_exclusive(end: str) -> str:
    """区间右边界，**不含**。

    只给到天的（`2026-11-23`）→ 次日 00:00，这样当天带时分的账也能被包含；
    已经给了时分的（`2026-11-23 23:00`）→ 原样用。
    用 `<` 而不是 `between`，否则「21点~23点」会把 23:00 那笔也算进来。
    """
    if len(end) > 10:
        return end
    return (date.fromisoformat(end) + timedelta(days=1)).isoformat() + " 00:00"


def run(dsl: dict) -> list[dict]:
    """按 DSL 查库。SQL 全部由代码拼，参数绑定，用户输入不进 SQL。"""
    lo, hi = dsl["start"], end_exclusive(dsl["end"])

    # 结余 = 收入 − 支出，两种方向都要算，所以不套用 kind 过滤
    if dsl["metric"] == "balance":
        sql = ("select coalesce(sum(case when kind = 'income' then amount_cents"
               " else -amount_cents end), 0) as value, count(*) as count"
               " from transactions where status = 'confirmed' and deleted_at is null"
               " and occurred_at >= ? and occurred_at < ?")
        return storage.query(sql, (lo, hi))

    # deleted_at is null 这条不能少：删掉的账还留在表里，漏了就会算进查账结果
    where = ["status = 'confirmed'", "deleted_at is null", "kind = ?",
             "occurred_at >= ?", "occurred_at < ?"]
    params: list = [dsl.get("kind", "expense"), lo, hi]

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
    income = dsl.get("kind") == "income"
    what = "收入" if income else ""

    if not rows:
        return f"{label}没有{what}记录"

    # 结余：正数说「还剩」，负数说「超支」，别只丢一个负号给用户猜
    if dsl["metric"] == "balance":
        if not rows[0]["count"]:
            return f"{label}没有记录"
        value = rows[0]["value"] or 0
        if value >= 0:
            return f"{label}还剩下 {y(value)} 元（收入 − 支出）"
        return f"{label}超支了 {y(-value)} 元（收入 − 支出）"

    if dsl["group_by"] == "category":
        top = rows[0]
        return (f"{label}花得最多的是「{top['key']}」，"
                f"{y(top['value'])} 元，共 {top['count']} 笔")

    if dsl["group_by"] == "month":
        parts = [f"{r['key']}：{y(r['value'])} 元" for r in rows]
        return f"{label}" + "，".join(parts)

    row = rows[0]
    if not row["count"]:
        return f"{label}没有{what}记录"
    if dsl["metric"] == "count":
        return f"{label}{what}共 {row['count']} 笔"

    cat = "、".join(dsl["categories"])
    value = row["value"] or 0
    if dsl["metric"] == "avg":
        who = f"{cat}的" if cat else ""
        return f"{label}{who}平均每笔 {y(round(value))} 元，共 {row['count']} 笔"
    if cat:
        return f"{label}{cat}{what}一共 {y(value)} 元，共 {row['count']} 笔"
    return f"{label}{what}一共 {y(value)} 元，共 {row['count']} 笔"


def looks_like_query(q: str) -> bool:
    """规则能不能看出「这是一句在问账的话」。

    只看疑问/聚合词，不看「今天」「外卖」这种名词——光命中时间或类别不算问账，
    否则「今天天气不错」「帮我订个外卖」都会被答成一句数字。
    认不出来交给模型判，模型也判不了就老实说没看懂。
    """
    return any(w in q for w in ("多少", "几笔", "几次", "平均", "结余", "余额",
                                 "剩下", "还剩", "收入", "支出", "花销",
                                 "统计", "一共", "哪类", "哪个", "最贵", "最高"))


# 规则认识的时间说法 / 认识但容易答歪的（周、季度、最近…）
# 注意别把「近」放进来：近 7 天由正则单独处理，「最近」规则是算不出来的
KNOWN_TIME_WORDS = ("上个月", "上月", "本月", "这个月", "当月", "去年", "今年",
                    "今天", "昨天", "前天")
VAGUE_TIME_WORDS = ("周", "星期", "季度", "半年", "最近", "这几天", "过去",
                    "年初", "年底", "月初", "月底")


def time_is_understood(q: str) -> bool:
    """时间说法规则到底认不认得。

    「上周花了多少」里规则只认得「多少」，时间会默默兜到「这个月」——
    这种宁可不答，交给模型去算真实区间。
    """
    if re.search(r"近\s*\d+\s*天", q) or re.search(r"\d{1,2}\s*月份?", q):
        return True
    if any(w in q for w in KNOWN_TIME_WORDS):
        return True
    # 完全没提时间：默认「这个月」是可以接受的
    if not any(w in q for w in VAGUE_TIME_WORDS):
        return True
    return False


def dsl_from_llm(raw: dict, today: date) -> dict | None:
    """把模型给的结构拼成 DSL。模型只能出结构，数字仍然由 SQL 算。

    区间**直接用模型给的 start/end**：以前让模型从 this_month 这类枚举里选，
    「11月初到12月底」这种区间就表达不出来，只能挑个最像的，结果答非所问。
    """
    try:
        start = date.fromisoformat(raw["start"])
        end = date.fromisoformat(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end < start:
        return None

    metric = raw.get("metric")
    if metric not in AGG_SQL and metric != "balance":
        metric = "sum"
    kind = raw.get("kind")
    if kind not in ("expense", "income"):
        kind = "expense"
    group = raw.get("group_by")
    if group not in ("none", "category", "month"):
        group = "none"
    cats = [c for c in (raw.get("categories") or []) if c in CATEGORY_WORDS]

    return {
        "question": "",
        "metric": metric,
        "group_by": group,
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": raw.get("label") or f"{start.isoformat()} ~ {end.isoformat()}",
        "categories": cats,
    }


def _time_note(dsl: dict) -> str:
    """钟点查询的免责提示。

    「昨天21点」这种窗口只能命中带时分的账；同一天里只写到天的账没法判断
    属不属于这个钟点，所以不进结果——但必须说一声，不然用户会以为这就是全部。
    """
    if len(dsl.get("start", "")) <= 10:
        return ""                      # 不是钟点查询，没什么可提示的
    day = dsl["start"][:10]
    n = storage.count_dateless(day)
    if not n:
        return ""
    return f"提示：{day} 还有 {n} 笔只记了日期、没记时间，没算进这个钟点范围，结果可能不准"


def ask(q: str, today: date | None = None, api_key: str | None = None) -> dict:
    """完整流程：问题 -> DSL -> 查询 -> 回答。返回给前端展示。

    返回里的 ok=False 表示「这句话没看懂」，前端应该照实说，不要显示数字。
    note 是一句补充说明（比如「这天有几笔没记时间」），前端显示成小字。
    """
    today = today or date.today()

    # 规则认得「问什么」，但时间说法它没见过（上周 / 最近 / 年底）→ 也别硬答
    if looks_like_query(q) and time_is_understood(q):
        dsl = parse_question(q, today)
        rows = run(dsl)
        return {"ok": True, "source": "rules", "question": q, "dsl": dsl,
                "rows": rows, "answer": to_sentence(dsl, rows),
                "note": _time_note(dsl)}

    # 规则看不出来：问模型「这到底是不是在问账」
    import llm                                  # 延迟 import，避免 query <-> llm 相互牵扯
    guess = llm.parse_query(q, api_key)
    if guess and guess.get("is_query"):
        dsl = dsl_from_llm(guess, today)
        if dsl:
            dsl["question"] = q
            rows = run(dsl)
            return {"ok": True, "source": "llm", "question": q, "dsl": dsl,
                    "rows": rows, "answer": to_sentence(dsl, rows),
                    "note": _time_note(dsl)}

    return {"ok": False, "source": "none", "question": q, "dsl": None, "rows": [],
            "answer": "没看懂这句话。可以问「这个月花了多少」「上个月吃饭多少钱」这种。",
            "note": ""}
