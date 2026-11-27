r"""账单导入：把一张 CSV 表读成「一条条账目草稿」。

为什么单独一个文件，不塞进 parser.py：
    parser.py 干的是「一句话 -> 字段」，主语是句子，几十个字；
    这里干的是「一张表 -> 一堆行」，主语是表，动辄几百行。
    两边共用的只有「类别关键词」和「金额/日期怎么认」，所以 import parser 就够了。

三条规矩，跟主链路保持一致：
    1. 读不懂就跳过，并在结果里说清第几行、为什么——**绝不猜一个金额**出来
    2. 解析出来的东西一律算**不可信输入**，入库前还要过 app 那层校验
    3. 只认「收/支」明确的流水；「不计收支」（转账、还信用卡、余额宝存取）不记账

支持的三种表头（靠列名猜，不靠文件名叫什么）：
    微信支付账单   交易时间 / 交易类型 / 交易对方 / 商品 / 收/支 / 金额(元) ...
    支付宝账单     交易时间 / 交易分类 / 交易对方 / 商品说明 / 收/支 / 金额 ...
    通用 CSV       时间 / 金额 / 收支 / 备注（自己用 Excel 做的也算）

典型用法：
    parsed = importer.parse_bytes(raw_bytes)
    parsed["rows"]       # [{"text", "amount_cents", "occurred_at", "kind", "category"}]
    parsed["skipped"]    # [{"line": 42, "reason": "..."}]，给用户看的
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

import parser as rules

# 跟 app.MAX_TEXT_LEN 是同一个数：这里先截断，入库前 app 还会再查一遍。
# 不 import app 是为了避免循环依赖（app 要 import 这个文件）。
MAX_TEXT_LEN = 200

# 一次最多读这么多行。再多就不像个人账单了，多半是导错了文件。
MAX_ROWS = 5000

# 扫描表头时最多往后看这么多行：微信账单开头有十几行「昵称/起始时间」的说明文字
HEADER_SCAN_ROWS = 40


class ImportParseError(ValueError):
    """这个文件读不了。消息是给用户看的一句话，别写堆栈。"""


# 列名 -> 可能出现在表头里的叫法。顺序有讲究：
# 「交易创建时间」里含「时间」，所以「交易时间」得排在「时间」前面才能精确命中。
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("交易时间", "交易创建时间", "付款时间", "发生时间", "日期", "时间"),
    "amount": ("金额", "发生额"),
    "direction": ("收/支", "收支", "方向"),
    "counterparty": ("交易对方", "对方", "商户", "商家"),
    "product": ("商品说明", "商品名称", "商品", "摘要", "备注"),
    "category": ("交易分类", "分类", "类别"),
    "status": ("交易状态", "当前状态", "状态"),
    "type": ("交易类型", "类型"),
}

# 日期列能见到的写法。长的排前面，strptime 要求整串匹配，不然会被短的先抢走。
_WHEN_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日",
)

# 支付宝的「交易分类」-> 我们的七类。
# 只做翻译，不扩大类别体系：认不出来的留空，让 parser 的关键词去猜，再不行就是未分类。
_CATEGORY_MAP = {
    "餐饮美食": "餐饮", "餐饮": "餐饮",
    "交通出行": "交通", "交通": "交通",
    "服饰装扮": "购物", "日用百货": "购物", "家居家装": "购物", "数码电器": "购物",
    "美容美发": "购物", "母婴亲子": "购物", "宠物": "购物", "运动户外": "购物", "购物": "购物",
    "文化休闲": "娱乐", "酒店旅游": "娱乐", "娱乐": "娱乐",
    "充值缴费": "居住", "生活服务": "居住", "住房物业": "居住", "居住": "居住",
    "医疗健康": "医疗", "医疗": "医疗",
    "教育培训": "教育", "教育": "教育",
}

# 交易状态里出现这些词的，钱其实没花出去（或者已经退回来了），不记账
_SKIP_STATUS_WORDS = ("关闭", "失败", "退款", "等待付款")

# 「收/支」列里明确不算消费的写法
_SKIP_DIRECTION_WORDS = ("不计", "其他", "中性")

_SOURCE_LABEL = {
    "wechat": "微信账单",
    "alipay": "支付宝账单",
    "generic": "自定义 CSV",
}


def parse_bytes(raw: bytes, filename: str = "") -> dict:
    """整份文件 -> 解析结果。读不了就抛 ImportParseError（消息能直接给用户看）。

    filename 只看后缀：`.xlsx` / `.xlsm` 走 Excel 那条路，其余按 CSV / TXT 解。
    """
    if not raw:
        raise ImportParseError("文件是空的，没东西可导。")

    suffix = Path(filename).suffix.lower()
    sheet = ""
    if suffix in (".xlsx", ".xlsm"):
        rows, sheet = _read_xlsx(raw)
        text, encoding = "", "xlsx"
    else:
        text, encoding = _decode(raw)
        rows = _read_rows(text)
    if not rows:
        raise ImportParseError("这个文件里一行都没有。")

    header_index, mapping = _find_header(rows)
    if header_index is None:
        raise ImportParseError(
            "没认出表头。至少要有一行同时写着「时间」和「金额」，"
            "再带一列「收/支」「备注」或「交易对方」——微信和支付宝导出的 CSV 都符合。")

    items: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple] = set()          # 同一个文件里重复出现的行（有的导出会重复算）
    duplicated = 0

    for index in range(header_index + 1, len(rows)):
        row = rows[index]
        if not any(_cell(row, i) for i in range(len(row))):
            continue                  # 纯空行，不值得报「跳过」
        item, reason = _parse_row(row, mapping)
        if item is None:
            if reason:
                skipped.append({"line": index + 1, "reason": reason})
            continue
        key = (item["occurred_at"], item["amount_cents"], item["text"])
        if key in seen:
            duplicated += 1
            continue
        seen.add(key)
        items.append(item)

    if not items:
        detail = (f"第 {skipped[0]['line']} 行：{skipped[0]['reason']}"
                  if skipped else "一行「支出 / 收入」都没读到")
        raise ImportParseError(f"表头认出来了，但没有一条能记账的流水（{detail}）。")

    truncated = len(items) > MAX_ROWS
    if truncated:
        items = items[:MAX_ROWS]

    source = _detect_source(text, rows[header_index])
    return {
        "source": source,
        "source_label": _SOURCE_LABEL[source],
        "encoding": encoding,
        "sheet": sheet,
        "header_row": header_index + 1,
        "rows": items,
        "total": len(items),
        "skipped": skipped[:20],      # 只把前 20 条原因给界面，别把整页塞满
        "skipped_total": len(skipped),
        "duplicated": duplicated,     # 文件内部重复，去重掉的不算跳过
        "truncated": truncated,
    }


def _decode(raw: bytes) -> tuple[str, str]:
    """字节 -> 文字。微信导出常是 GBK，支付宝带 UTF-8 BOM，Excel 存「Unicode」的是 UTF-16。

    顺序有讲究：UTF-8 能解就用 UTF-8（`utf-8-sig` 会把 BOM 顺手吃掉）；
    解不了再退 GBK/GB18030。反过来的话 GB18030 几乎什么都能解，
    会把一份正常的 UTF-8 解成乱码还不报错。
    """
    if b"\x00" in raw[:200]:          # UTF-16 的 ASCII 字符高位都是 0，先认出来
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ImportParseError(
        "这个文件既不是 UTF-8 也不是 GBK 编码。用 Excel 另存成「CSV UTF-8」再试一次。")


def _read_rows(text: str) -> list[list[str]]:
    """切成一行行的单元格。引号里带逗号、带换行的，csv 模块会自己处理。"""
    try:
        return list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ImportParseError(f"这个 CSV 的行读不出来：{exc}") from exc


def _find_header(rows: list[list[str]]) -> tuple[int | None, dict[str, int]]:
    """在前几十行里找表头：认出的列越多越像，至少要有「时间 + 金额 + 一列说明」。

    为什么要扫而不是直接取第一行：微信账单前面有「微信昵称 / 起始时间 / 导出类型」
    这些说明文字，支付宝前面还有一行虚线标题——固定跳几行，换个版本就错位。
    """
    best: tuple[int, int, dict[str, int]] | None = None   # (命中列数, 行号, 列位置)
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        mapping = _map_header(row)
        if "time" not in mapping or "amount" not in mapping:
            continue
        if not ({"counterparty", "product", "category"} & mapping.keys()):
            continue
        score = len(mapping)
        if best is None or score > best[0]:
            best = (score, index, mapping)
    if best is None:
        return None, {}
    return best[1], best[2]


def _map_header(row: list[str]) -> dict[str, int]:
    """一行单元格 -> {字段: 第几列}。一格只归一个字段。"""
    names = [_header_name(cell) for cell in row]
    mapping: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        for index, name in enumerate(names):
            if not name or index in mapping.values():
                continue
            if any(alias in name for alias in aliases):
                mapping[key] = index
                break
    return mapping


def _header_name(cell: str) -> str:
    """表头去掉空白和 BOM 再比：有的导出会把「金 额」写成带空格的。"""
    return (cell or "").strip().replace("\ufeff", "").replace(" ", "").replace("　", "")


def _parse_row(row: list[str], mapping: dict[str, int]) -> tuple[dict | None, str]:
    """一行 -> 一条草稿。返回 (None, 原因) 表示这行跳过，原因是给用户看的。"""
    status = _cell(row, mapping.get("status"))
    for word in _SKIP_STATUS_WORDS:
        if word in status:
            return None, f"状态是「{status}」，钱没有真的花出去"

    direction = _cell(row, mapping.get("direction"))
    if any(word in direction for word in _SKIP_DIRECTION_WORDS):
        return None, f"「{direction}」不算消费（转账、还款这类）"

    when_raw = _cell(row, mapping.get("time"))
    when = _parse_when(when_raw)
    if when is None:
        return None, f"日期看不懂：{when_raw or '（空）'}"

    amount_raw = _cell(row, mapping.get("amount"))
    amount = _parse_amount(amount_raw)
    if amount is None:
        return None, f"金额看不懂：{amount_raw or '（空）'}"
    if amount == 0:
        return None, "金额是 0，不算一笔账"

    counterparty = _cell(row, mapping.get("counterparty"))
    product = _cell(row, mapping.get("product"))
    category = _map_category(_cell(row, mapping.get("category")))
    text = _compose_text(counterparty, product, category)

    # 负数一般是退款 / 冲正：钱回来了，记成收入，别记成「负的支出」
    kind = _kind_from_direction(direction)
    if amount < 0:
        amount = -amount
        kind = kind or "income"
    if kind is None:
        # 通用 CSV 可能没有「收/支」列，那就按文字里的词猜（工资/红包 → 收入）
        kind = rules.find_kind(text)[0]
    if category is None:
        category = rules.find_category(text)[0]

    return {
        "text": text,
        "amount_cents": amount,
        "occurred_at": when,
        "kind": kind,
        "category": category,
    }, ""


def _cell(row: list[str], index: int | None) -> str:
    """取一格。列数不够（有的行尾巴是空的）就当空字符串，别抛 IndexError。"""
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _parse_when(value: str) -> str | None:
    """各种日期写法 -> 统一的 `2026-11-25` 或 `2026-11-25 12:30`。

    只记到 00:00 的不带时分：账单里 00:00 多半是「没给时间」，不是真在半夜花的。
    """
    text = (value or "").strip().replace("T", " ")
    if not text:
        return None
    for fmt in _WHEN_FORMATS:
        try:
            parsed = datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
        if parsed.hour or parsed.minute:
            return parsed.strftime("%Y-%m-%d %H:%M")
        return parsed.strftime("%Y-%m-%d")
    return None


def _parse_amount(value: str) -> int | None:
    """「¥1,234.56」-> 123456 分。用 Decimal，不走浮点（跟全项目一个规矩）。"""
    text = (value or "").strip()
    for noise in ("¥", "￥", ",", "，", " ", "元"):
        text = text.replace(noise, "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _kind_from_direction(direction: str) -> str | None:
    """「收/支」列 -> expense / income。空着或看不懂就返回 None，交给调用方兜底。"""
    text = (direction or "").strip()
    if text in ("收入", "收", "入账"):
        return "income"
    if text in ("支出", "支", "出账"):
        return "expense"
    return None


def _map_category(value: str) -> str | None:
    """支付宝的「交易分类」翻译成我们那七类。翻译不了就返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    if text in _CATEGORY_MAP:
        return _CATEGORY_MAP[text]
    for key, mapped in _CATEGORY_MAP.items():     # 「餐饮美食-外卖」这种带后缀的
        if key in text:
            return mapped
    return None


def _compose_text(counterparty: str, product: str, category: str | None) -> str:
    """一行账的「做了什么」：商户 + 商品。两个一样就别写两遍。

    都没有（比如自制的 CSV 只写了类别）就退到类别，再不行写「导入的账单」——
    宁可文字朴素，也不要空着，流水列表里空标题比「未分类」还难认。
    """
    parts: list[str] = []
    for value in (counterparty, product):
        value = (value or "").strip()
        if value and value not in parts:
            parts.append(value)
    text = " · ".join(parts) or (category or "导入的账单")
    return text[:MAX_TEXT_LEN]


def _detect_source(text: str, header: list[str]) -> str:
    """猜这是谁家导出的，只为了在界面上写「微信账单 / 支付宝账单」，不影响解析。"""
    head = " ".join(_header_name(cell) for cell in header)
    if "交易对方" in head and ("支付方式" in head or "当前状态" in head):
        return "wechat"
    if "交易分类" in head or "商品说明" in head or "交易订单号" in head:
        return "alipay"
    # 表头被改过的话，再退一步看文件开头的说明文字
    prefix = text[:400]
    if "微信" in prefix:
        return "wechat"
    if "支付宝" in prefix:
        return "alipay"
    return "generic"


# ---------------- Excel（.xlsx） ----------------
# 为什么不用 openpyxl：xlsx 就是一个 zip 包着的 XML，而这里只需要「读单元格」这一件事。
# 自己解不到 150 行，省掉一个依赖（Docker 镜像也能小一点），跟「不引 python-dotenv」是一个思路。
# 只读数值/文本/日期，不碰公式计算——读的是 Excel 存好的缓存值，公式单元格没有缓存值就当空。

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Excel 内置的日期/时间格式编号（14~22 是日期，45~47 是时间）
_BUILTIN_DATE_FORMATS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}


def _local(tag: str) -> str:
    """去掉 XML 命名空间，只留标签名。"""
    return tag.rsplit("}", 1)[-1]


def _read_xlsx(raw: bytes) -> tuple[list[list[str]], str]:
    """xlsx -> 行列表 + 用的工作表名。有多个工作表时挑行数最多的那个。"""
    try:
        book = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ImportParseError(
            "这个 Excel 打不开：可能是老的 .xls 格式，或者文件坏了。"
            "用 Excel 另存为 .xlsx 或 CSV 再试。") from exc

    with book:
        try:
            shared = _xlsx_shared_strings(book)
            date_styles, date1904 = _xlsx_styles(book)
            sheets = _xlsx_sheets(book)
        except (KeyError, ET.ParseError) as exc:
            raise ImportParseError("这个 Excel 结构看不懂，换一份再试。") from exc

        best: tuple[str, list[list[str]]] | None = None
        for name, path in sheets:
            try:
                rows = _xlsx_rows(book, path, shared, date_styles, date1904)
            except (KeyError, ET.ParseError):
                continue
            if best is None or len(rows) > len(best[1]):
                best = (name, rows)
        if best is None:
            raise ImportParseError("这个 Excel 里没有能读的工作表。")
        return best[1], best[0]


def _xlsx_sheets(book: zipfile.ZipFile) -> list[tuple[str, str]]:
    """[(工作表名, 内部 XML 路径)]，按工作簿里的顺序。"""
    names = set(book.namelist())
    workbook = ET.fromstring(book.read("xl/workbook.xml"))

    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for rel in ET.fromstring(book.read("xl/_rels/workbook.xml.rels")):
            rels[rel.get("Id") or ""] = rel.get("Target") or ""

    out: list[tuple[str, str]] = []
    for sheet in workbook.iter():
        if _local(sheet.tag) != "sheet":
            continue
        rid = ""
        for key, value in sheet.attrib.items():
            if _local(key) == "id":
                rid = value
                break
        target = rels.get(rid, "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = "xl/" + target.lstrip("./")
        if path not in names:
            continue
        out.append((sheet.get("name") or f"Sheet{len(out) + 1}", path))
    if not out:                                   # 极简生成器可能没写 rels
        out = [("Sheet1", "xl/worksheets/sheet1.xml")]
    return out


def _xlsx_shared_strings(book: zipfile.ZipFile) -> list[str]:
    """共享字符串表。Excel 把重复出现的文字只存一份，单元格里存下标。"""
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        # 富文本会被拆成多个 <r><t>，拼起来才是完整一句话
        out.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    return out


def _xlsx_styles(book: zipfile.ZipFile) -> tuple[set[int], bool]:
    """哪些单元格样式是「日期」，以及工作簿是不是 1904 日期系统。"""
    date1904 = False
    if "xl/workbook.xml" in book.namelist():
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        for node in workbook.iter():
            if _local(node.tag) == "workbookPr":
                date1904 = (node.get("date1904") or "").lower() in ("1", "true")

    date_styles: set[int] = set()
    if "xl/styles.xml" not in book.namelist():
        return date_styles, date1904

    root = ET.fromstring(book.read("xl/styles.xml"))
    custom: dict[int, str] = {}
    for node in root.iter():
        if _local(node.tag) == "numFmt":
            try:
                custom[int(node.get("numFmtId") or 0)] = node.get("formatCode") or ""
            except ValueError:
                continue

    for node in root.iter():
        if _local(node.tag) != "cellXfs":
            continue
        for index, xf in enumerate(list(node)):
            if _local(xf.tag) != "xf":
                continue
            try:
                fmt = int(xf.get("numFmtId") or 0)
            except ValueError:
                fmt = 0
            code = custom.get(fmt, "")
            if fmt in _BUILTIN_DATE_FORMATS or (code and _looks_like_date(code)):
                date_styles.add(index)
        break
    return date_styles, date1904


def _looks_like_date(code: str) -> bool:
    """格式串里有没有年月日/时分。先去掉引号里的字面量和 [...] 里的条件再判断。"""
    text = re.sub(r'"[^"]*"', "", code)
    text = re.sub(r"\[[^\]]*\]", "", text).lower()
    return any(ch in text for ch in "yd") or ("h" in text and "m" in text)


def _serial_to_datetime(serial: float, date1904: bool) -> datetime | None:
    """Excel 的日期就是一个数：从 1899-12-30（1904 系统从 1904-01-01）起算的天数。"""
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    try:
        return base + timedelta(days=serial)
    except (OverflowError, ValueError):
        return None


def _xlsx_rows(book: zipfile.ZipFile, path: str,
               shared: list[str], date_styles: set[int], date1904: bool) -> list[list[str]]:
    root = ET.fromstring(book.read(path))
    collected: list[tuple[int, dict[int, str]]] = []

    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        try:
            row_index = int(row.get("r") or len(collected) + 1)
        except ValueError:
            row_index = len(collected) + 1
        cells: dict[int, str] = {}
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            ref = cell.get("r") or ""
            column = _column_index(ref) if ref else len(cells)
            cells[column] = _xlsx_cell(cell, shared, date_styles, date1904)
        if cells:
            collected.append((row_index, cells))

    collected.sort(key=lambda pair: pair[0])
    rows: list[list[str]] = []
    for _, cells in collected:
        width = max(cells) + 1
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _column_index(ref: str) -> int:
    """单元格坐标 A1 -> 0，B1 -> 1，AA1 -> 26。"""
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return max(0, index - 1)


def _xlsx_cell(cell, shared: list[str], date_styles: set[int], date1904: bool) -> str:
    kind = cell.get("t") or ""

    def child(tag: str):
        return next((node for node in cell if _local(node.tag) == tag), None)

    if kind == "inlineStr":
        node = child("is")
        return "".join(t.text or "" for t in node.iter() if _local(t.tag) == "t") if node is not None else ""

    value = child("v")
    if value is None or value.text is None:
        return ""
    raw = value.text

    if kind == "s":                       # 共享字符串下标
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":                       # 布尔
        return "TRUE" if raw.strip() in ("1", "true", "TRUE") else "FALSE"
    if kind in ("str", "e"):              # 公式结果 / 错误值
        return raw

    try:
        number = float(raw)
    except ValueError:
        return raw

    try:
        style = int(cell.get("s") or -1)
    except ValueError:
        style = -1
    if style in date_styles:
        moment = _serial_to_datetime(number, date1904)
        if moment is not None:
            if moment.hour or moment.minute:
                return moment.strftime("%Y-%m-%d %H:%M")
            return moment.strftime("%Y-%m-%d")

    return str(int(number)) if number == int(number) else str(number)
