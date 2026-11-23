"""把一句话交给 DeepSeek，换回结构化字段。

**只在规则解析拿不准时调用**（金额或类别缺一个），所以绝大多数记账不会走网络：
    规则先跑（毫秒、免费） → 缺字段才问模型（1~2 秒、几分钱）

模型只负责「翻译」，返回的字段还要再校验一遍——金额范围、类别白名单、日期格式。
真正的入库和统计仍然走 SQL，这一层不碰数据库。
"""
import json
import re
from datetime import date

import requests

from config import DEEPSEEK_API_KEY
from parser import CATEGORY_WORDS
from storage import KINDS

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"          # 要换模型改这里，或者在 .env 里加 LLM_MODEL
MAX_AMOUNT_YUAN = 1_000_000

# 最近一次失败的原因，方便在日志/接口里看到，而不是永远只返回 None
LAST_ERROR = ""

SYSTEM_TEMPLATE = """你是记账助手。把用户的一句话解析成 JSON，只输出 JSON，不要任何解释。

字段要求：
- amount: 数字（单位：元），没有提到金额就填 null
- date: 记账日期 YYYY-MM-DD，没有明确日期就填 null
- category: 必须是以下之一：{cats}；拿不准就填 null
- kind: "expense" 或 "income"。花钱、买东西、消费填 expense；
  收到钱、红包、工资、退款、报销、卖东西收钱填 income；拿不准填 null
- title: 一句话概括这件事，不超过 20 字

今天是 {today}。像「昨天」「上周三」这种相对日期，请换算成具体日期。"""


def _categories() -> str:
    return "、".join(CATEGORY_WORDS.keys())


def parse_text(text: str, api_key: str | None = None) -> dict | None:
    """调模型解析一句话。失败一律返回 None，让调用方继续用规则结果。

    失败原因记在 LAST_ERROR 里——静默失败最难查，401 和超时长得一样。
    """
    global LAST_ERROR
    LAST_ERROR = ""

    key = (api_key or DEEPSEEK_API_KEY or "").strip()
    if not key:
        LAST_ERROR = "没有 key"
        return None

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system",
             "content": SYSTEM_TEMPLATE.format(cats=_categories(),
                                               today=date.today().isoformat())},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if resp.status_code != 200:
            LAST_ERROR = f"HTTP {resp.status_code}: {resp.text[:160]}"
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        raw = json.loads(content)
    except Exception as exc:
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None

    return _validate(raw)


def _validate(raw: dict) -> dict:
    """模型给的东西一律当不可信输入处理。"""
    result = {"amount_cents": None, "occurred_at": None, "category": None,
              "kind": None, "title": None}

    amount = raw.get("amount")
    if isinstance(amount, (int, float)):
        cents = round(float(amount) * 100)
        if 0 < cents <= MAX_AMOUNT_YUAN * 100:
            result["amount_cents"] = cents

    day = raw.get("date")
    if isinstance(day, str):
        try:
            result["occurred_at"] = date.fromisoformat(day[:10]).isoformat()
        except ValueError:
            pass

    cat = raw.get("category")
    if isinstance(cat, str) and cat in CATEGORY_WORDS:
        result["category"] = cat

    # 方向只认这两个值，模型说别的就当没说
    kind = raw.get("kind")
    if isinstance(kind, str) and kind.strip().lower() in KINDS:
        result["kind"] = kind.strip().lower()

    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()[:40]

    return result


# ---------------- 查账：判断「这是不是一句在问账的话」 ----------------
# 只让模型吐结构（问什么、哪个区间、什么聚合），数字仍然由 query.py 的 SQL 算。
# 区间必须给**具体日期**：如果只给 this_month 这种枚举，「11月初到12月底」就表达不出来，
# 模型只能挑一个最像的，结果就是答非所问。
QUERY_TEMPLATE = """你是记账助手的查询解析器。先判断用户这句话是不是在问账。

只输出 JSON，且只输出下面这些字段：
- "is_query": true 或 false。寒暄、骂人、闲扯、看不懂的，一律 false
- "metric": "sum"（合计金额）/ "count"（笔数）/ "avg"（平均每笔）/ "balance"（结余＝收入−支出）
- "kind": "expense"（支出）/ "income"（收入）
- "start": 区间开始，"YYYY-MM-DD"，包含这一天
- "end": 区间结束，"YYYY-MM-DD"，包含这一天
- "label": 这个区间的中文叫法，不超过 12 个字，例如 "11月初到12月底"
- "categories": 类别数组，只能从这些里选：{cats}；没提就空数组
- "group_by": "none" / "category"（按类别）/ "month"（按月）

日期必须自己算准，今天是 {today}：
- 「上个月」= 上个月 1 日到上个月最后一天
- 「11月初到12月底」= 2026-11-01 到 2026-12-31
- 没提时间就按「这个月」：本月 1 日到本月最后一天
- 算不准就按「这个月」，宁可范围小一点也不要编日期
- is_query 为 false 时，start 和 end 填空字符串 ""

只输出 JSON，不要解释。"""


def _iso_day(value) -> date | None:
    """严格解析 YYYY-MM-DD。`20261101`、`2026/11/01` 一律不认。

    模型给的日期一律当不可信输入：格式、真实存在（闰年/月天数）、年份范围都查。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    if day.year < 1970 or day.year > 2100:
        return None
    return day


def parse_query(text: str, api_key: str | None = None) -> dict | None:
    """让模型判断这句话是不是问账，并给出结构化条件。

    返回 None 表示调不通（没 key、超时、返回不是 JSON），
    返回 {"is_query": False} 表示模型认为这不是一句问账的话（或给的日期不可用）。
    """
    global LAST_ERROR
    LAST_ERROR = ""

    key = (api_key or DEEPSEEK_API_KEY or "").strip()
    if not key:
        LAST_ERROR = "没有 key"
        return None

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system",
             "content": QUERY_TEMPLATE.format(cats="、".join(CATEGORY_WORDS.keys()),
                                              today=date.today().isoformat())},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if resp.status_code != 200:
            LAST_ERROR = f"HTTP {resp.status_code}: {resp.text[:160]}"
            return None
        raw = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None

    return _validate_query(raw)


def _validate_query(raw: dict) -> dict:
    """模型给的查询结构也当不可信输入：枚举值过白名单，日期必须真实存在。"""
    if not isinstance(raw, dict) or raw.get("is_query") is not True:
        return {"is_query": False}

    start = _iso_day(raw.get("start"))
    end = _iso_day(raw.get("end"))
    # 日期不可用就当「没看懂」，绝不退回「这个月」——那正是答非所问的来源
    if start is None or end is None or start > end:
        return {"is_query": False, "reason": "日期不合法"}
    if (end - start).days > 3660:                 # 超过十年肯定是出错了
        return {"is_query": False, "reason": "区间太长"}

    metric = raw.get("metric")
    if metric not in ("sum", "count", "avg", "balance"):
        metric = "sum"

    kind = raw.get("kind")
    if kind not in KINDS:
        kind = "expense"

    group = raw.get("group_by")
    if group not in ("none", "category", "month"):
        group = "none"

    cats = raw.get("categories")
    if not isinstance(cats, list):
        cats = []
    cats = [c for c in cats if isinstance(c, str) and c in CATEGORY_WORDS]

    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        label = f"{start.isoformat()} ~ {end.isoformat()}"
    label = label.strip()[:12]

    return {"is_query": True, "metric": metric, "kind": kind,
            "start": start.isoformat(), "end": end.isoformat(),
            "label": label, "group_by": group, "categories": cats}
