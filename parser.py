"""把一句中文记账拆成字段：规则版，不联网、不花 token。

输入：「昨天打车32」「午饭25，和同事AA」「32块5的奶茶」
输出：
    amount_cents  金额（分），拿不准就是 None
    occurred_at   日期 YYYY-MM-DD
    category      类别，拿不准就是 None
    hits          命中了什么（调试 / 界面提示用）

规则版覆盖不了的（「三十二块」「上周三」），以后交给模型；现在先不接。
"""
import re
from datetime import date, timedelta

# 类别 -> 触发词，命中第一个就定
CATEGORY_WORDS: dict[str, list[str]] = {
    "餐饮": ["饭", "餐", "外卖", "奶茶", "咖啡", "火锅", "烧烤", "早点", "夜宵", "食堂"],
    "交通": ["打车", "地铁", "公交", "滴滴", "加油", "停车", "高铁", "火车", "机票"],
    "购物": ["淘宝", "京东", "超市", "衣服", "鞋", "买了", "网购"],
    "娱乐": ["电影", "游戏", "ktv", "旅游", "演出", "门票"],
    "居住": ["房租", "水电", "物业", "燃气"],
    "医疗": ["药", "医院", "挂号", "体检"],
    "教育": ["书", "课", "学费", "培训"],
}

RELATIVE_DAYS = {"今天": 0, "昨天": 1, "前天": 2, "大前天": 3}

# 「最近」「上周」这种听得懂但算不出具体哪天的说法：宁可拦下来问一句，也别默默记成今天
VAGUE_DATE_WORDS = ("最近", "前几天", "前阵子", "前些天", "这几天", "这段时",
                    "上周", "上上周", "上周末", "月初", "月底", "年中", "年底")

# 收入触发词：命中就是「钱进来了」
# 没命中一律当支出——记账习惯就是记花了多少，默认成收入反而更容易出错
INCOME_WORDS = ["工资", "红包", "收入", "进账", "退款", "报销", "奖金", "利息",
                "分红", "卖了", "收到", "收钱", "转入", "存入", "中奖", "补贴",
                "稿费", "外快", "返现", "退回", "赚了", "赚到", "挣了", "挣到"]

# 「3个人吃饭一共120」里的 3 不是金额：数字后面跟这些量词就跳过
COUNTERS = "个人位次张杯份碗瓶"

# 量级词：1亿 = 100000000
MAGNITUDE = {"百": 100, "千": 1_000, "万": 10_000, "亿": 100_000_000}

CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1_000}


def cn_to_int(s: str) -> int:
    """中文数字转整数：三十二 -> 32，一百零五 -> 105，一万五 -> 10005。"""
    total = 0        # 万 / 亿 以上的累计
    section = 0      # 当前节（万以下）
    num = 0
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        elif ch in CN_UNIT:
            section += (num or 1) * CN_UNIT[ch]      # 「十二」= 12，不是 02
            num = 0
        elif ch == "万":
            section += num
            total += section * 10_000
            section = 0
            num = 0
        elif ch == "亿":
            section += num
            total = (total + section) * 100_000_000
            section = 0
            num = 0
    return total + section + num


def find_amount(text: str) -> tuple[int | None, str]:
    """找金额，返回 (分, 命中的原文)。

    覆盖四种写法：
        32块5       口语的「三十二块五」
        1亿 / 1.5万  阿拉伯数字 + 量级
        三十二块     中文数字 + 块/元
        32          普通数字（跳过「3个人」这类量词）
    """
    # ① 「32块5」：块后面的单个数字是角
    m = re.search(r"(\d+)\s*块\s*(\d)(?!\d)", text)
    if m:
        return int(m.group(1)) * 100 + int(m.group(2)) * 10, m.group(0)

    # ② 阿拉伯数字 + 量级：1亿 / 1.5万 / 3千
    m = re.search(r"(\d+(?:\.\d{1,2})?)\s*(亿|万|千|百)", text)
    if m:
        cents = round(float(m.group(1)) * MAGNITUDE[m.group(2)] * 100)
        return cents, m.group(0)

    # ③ 中文数字 + 块/元：三十二块
    m = re.search(r"([零一二两三四五六七八九十百千万亿]+)\s*(?:块|元)", text)
    if m:
        return cn_to_int(m.group(1)) * 100, m.group(0)

    # ④ 普通阿拉伯数字，跳过「3个人」这种量词
    for m in re.finditer(r"(\d+(?:\.\d{1,2})?)", text):
        tail = text[m.end():m.end() + 1]
        if tail and tail in COUNTERS:
            continue
        cents = round(float(m.group(1)) * 100)
        # 前面紧挨着负号的记成负数（退款/冲正），收不收由上层决定
        if m.start() > 0 and text[m.start() - 1] in "-−":
            cents = -cents
        return cents, m.group(0)
    return None, ""


def find_date(text: str, today: date) -> date:
    # 按词长倒序匹配：「大前天」里有「前天」，不先匹配长的就会少算一天
    for word, offset in sorted(RELATIVE_DAYS.items(), key=lambda kv: -len(kv[0])):
        if word in text:
            return today - timedelta(days=offset)
    return today


def find_vague_date(text: str) -> str:
    """听起来像日期、但规则定不出具体是哪天的词。

    命中就返回那个词，让上层决定要不要拦——「最近」如果默默记成今天，
    等于替用户编了个日期，比报错更糟。
    """
    for word in VAGUE_DATE_WORDS:
        if word in text:
            return word
    return ""


def find_category(text: str) -> tuple[str | None, str]:
    for category, words in CATEGORY_WORDS.items():
        for word in words:
            if word in text:
                return category, word
    return None, ""


def find_kind(text: str) -> tuple[str, str]:
    """判断这笔记的是支出还是收入，返回 (kind, 命中的词)。"""
    for word in INCOME_WORDS:
        if word in text:
            return "income", word
    return "expense", ""


def parse(text: str, today: date | None = None) -> dict:
    """一句话 -> 一条草稿。"""
    today = today or date.today()
    amount, amount_hit = find_amount(text)
    category, category_hit = find_category(text)
    kind, kind_hit = find_kind(text)
    return {
        "amount_cents": amount,
        "occurred_at": find_date(text, today).isoformat(),
        "category": category,
        "kind": kind,
        "vague_date": find_vague_date(text),
        "hits": {"amount": amount_hit, "category": category_hit, "kind": kind_hit},
    }
