r"""小票 / 截图 OCR：一张图 -> 一条账目草稿（**不直接入库**）。

跟 lib/ 那一套的分工：
    llm.py      把「一句话」翻译成字段
    receipt.py  把「一张图」里的字读成字段
    两者都只负责「读」，读出来的金额仍然要过 app 那层校验，入库前还要用户点一次确认。

用的是 DeepSeek 的多模态接口（同一个 key、同一个端点，`deepseek-flash` 支持图片输入）。
图片走 base64 内联，不落盘：小票上有商户、卡号后四位，没必要留副本。

三条规矩，跟主链路一致：
    1. 读不出金额就说读不出，**绝不按商品单价自己算一个数**
    2. 「实付」和「原价」同时出现时取实付，取不到就老实报没读懂
    3. 模型返回的字段一律当不可信输入，日期/时刻/金额都要过白名单和范围检查
"""
import base64
import json
import re
from datetime import date

import requests

from config import DEEPSEEK_API_KEY
from llm import ENDPOINT, MODEL

# 跟背景图那条一致：8 MB 对一张小票照片绰绰有余
MAX_BYTES = 8 * 1024 * 1024
SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# 编辑框「做了什么」上限是 100 字，这里也按这个截，免得送进去还要被砍
MAX_TEXT_LEN = 100

LAST_ERROR = ""

PROMPT = """你在读一张消费小票或支付截图（便利店小票、外卖订单、微信/支付宝的账单详情）。

只输出一个 JSON 对象，不要任何解释。字段：
- "unreadable": true / false。图里不是小票或账单、或者金额被挡住/糊得看不清，就填 true
- "amount": 实付金额（元，数字）。取「实付 / 合计 / 支付金额 / 已付款」那一行；
  **不是**原价、不是优惠金额、不是找零、不是余额
- "date": 消费日期 "YYYY-MM-DD"，看不到就 null
- "time": 消费时刻 "HH:MM"（24 小时制），看不到就 null
- "merchant": 商户名，尽量短（例如「全家便利店」「美团外卖」），看不到就 null
- "items": 买的主要东西，一句话，不超过 20 字，看不到就 null

规矩：
- 金额必须是图里读到的，不许按商品单价自己加起来算
- 同时有「原价」和「实付」时取实付
- 图里的字可能是竖排、歪的、带水印的，按你看到的写
今天是 {today}。"""


def read_image(raw: bytes, suffix: str, api_key: str | None = None) -> dict | None:
    """读一张图，返回草稿。读不懂返回 None，原因在 LAST_ERROR 里。

    返回的草稿形如：
        {"amount_cents": 1450, "date": "2026-11-20", "time": "18:32",
         "merchant": "全家便利店", "items": "矿泉水、面包", "text": "全家便利店 · 矿泉水、面包"}
    """
    global LAST_ERROR
    LAST_ERROR = ""

    key = (api_key or DEEPSEEK_API_KEY or "").strip()
    if not key:
        LAST_ERROR = "没有配置 API Key，读不了图"
        return None

    mime = SUFFIX_MIME.get((suffix or "").lower())
    if not mime:
        LAST_ERROR = "只认 png / jpg / webp / gif 的图片"
        return None
    if not raw:
        LAST_ERROR = "图片是空的"
        return None
    if len(raw) > MAX_BYTES:
        LAST_ERROR = "图片太大了（上限 8 MB）"
        return None

    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT.format(today=date.today().isoformat())},
                # detail=high：小票金额的字很小，缩到 512 就看不清了
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
                               "detail": "high"}},
            ],
        }],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 400,
    }
    try:
        resp = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,                       # 读图比读字慢，给足时间
        )
        if resp.status_code != 200:
            body = resp.text
            # 接口的单边上限是 8192px：整页账单截图（一万多像素高）会撞在这
            if "too large" in body or "must each be at most" in body:
                LAST_ERROR = "这张图太大或太长，接口读不了。单张小票可以，整页账单请导 CSV"
            else:
                LAST_ERROR = f"视觉接口 HTTP {resp.status_code}：{body[:120]}"
            return None
        raw_json = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return None

    if not isinstance(raw_json, dict):
        LAST_ERROR = "模型返回的不是一个 JSON 对象"
        return None
    if raw_json.get("unreadable") is True:
        reason = str(raw_json.get("reason") or "").strip()
        LAST_ERROR = "这张图没读懂" + (f"（{reason}）" if reason else "") + "，手动记一笔吧"
        return None

    draft = _validate(raw_json)
    if draft["amount_cents"] is None:
        # 金额是这一条的唯一硬门槛：没有它，后面填什么都是猜
        LAST_ERROR = "图里没找到能确认的金额，手动记一笔吧"
        return None
    return draft


def _validate(raw: dict) -> dict:
    """模型给的字段一律当不可信输入：金额范围、日期真实存在、时刻不越界。"""
    result = {"amount_cents": None, "date": None, "time": None,
              "merchant": None, "items": None, "text": ""}

    amount = raw.get("amount")
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        cents = round(float(amount) * 100)
        if 0 < cents <= 1_000_000 * 100:          # 跟 llm 那边同一个上限
            result["amount_cents"] = cents

    day = raw.get("date")
    if isinstance(day, str):
        try:
            parsed = date.fromisoformat(day.strip()[:10])
        except ValueError:
            parsed = None
        if parsed and 1970 <= parsed.year <= 2100:
            result["date"] = parsed.isoformat()

    clock = raw.get("time")
    if isinstance(clock, str):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", clock.strip())
        if m and 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59:
            result["time"] = f"{int(m.group(1)):02d}:{m.group(2)}"

    for key in ("merchant", "items"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:40]

    result["text"] = _compose_text(result["merchant"], result["items"])
    return result


def _compose_text(merchant: str | None, items: str | None) -> str:
    """商户 · 买了什么。两个都读不到就退到「小票消费」，别给个空标题。"""
    parts: list[str] = []
    for value in (merchant, items):
        value = (value or "").strip()
        if value and value not in parts:
            parts.append(value)
    return (" · ".join(parts) or "小票消费")[:MAX_TEXT_LEN]
