"""把一句话交给 DeepSeek，换回结构化字段。

**只在规则解析拿不准时调用**（金额或类别缺一个），所以绝大多数记账不会走网络：
    规则先跑（毫秒、免费） → 缺字段才问模型（1~2 秒、几分钱）

模型只负责「翻译」，返回的字段还要再校验一遍——金额范围、类别白名单、日期格式。
真正的入库和统计仍然走 SQL，这一层不碰数据库。
"""
import json
from datetime import date

import requests

from config import DEEPSEEK_API_KEY
from parser import CATEGORY_WORDS

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"          # 要换模型改这里，或者在 .env 里加 LLM_MODEL
MAX_AMOUNT_YUAN = 1_000_000

# 最近一次失败的原因，方便在日志/接口里看到，而不是永远只返回 None
LAST_ERROR = ""

SYSTEM_TEMPLATE = """你是记账助手。把用户的一句话解析成 JSON，只输出 JSON，不要任何解释。

字段要求：
- amount: 数字（单位：元），没有提到金额就填 null
- date: 消费日期 YYYY-MM-DD，没有明确日期就填 null
- category: 必须是以下之一：{cats}；拿不准就填 null
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
    result = {"amount_cents": None, "occurred_at": None, "category": None, "title": None}

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

    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()[:40]

    return result
