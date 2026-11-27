r"""记账服务的后端：一个常驻程序，监听端口，回答浏览器发来的请求。

启动（在项目根目录）：
    .\.venv\Scripts\python.exe -m uvicorn app:app --reload --port 8000

然后浏览器打开：
    http://127.0.0.1:8000/api/summary    看本月总额和最近流水
    http://127.0.0.1:8000/docs           自动生成的接口文档，可以直接点按钮试

这个文件只负责"把网址接到函数上"，真正的活都在 storage.py 里。
"""
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import asr
import audio_effectsRecovery as audio_fx
import importer
import llm
import parser
import query
import receipt
import settings as runtime
import storage
from config import DATA_DIR, DEEPSEEK_API_KEY

TEMPLATES = Path(__file__).parent / "templates"
UPLOAD_DIR = DATA_DIR / "uploads"

# 卡片背景图：存在 data/ 里（跟着数据卷走，不进仓库也不进镜像）
BACKGROUND_DIR = DATA_DIR / "backgrounds"
BG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
BG_MAX_BYTES = 8 * 1024 * 1024

# 账单导入：只收文本表格，上限跟背景图一致——200 行的账单通常不到 200 KB。
# 行数上限直接用解析层那个数，别在两处各写一个 5000（迟早会只改一处）
IMPORT_SUFFIXES = {".csv", ".txt"}
IMPORT_MAX_BYTES = 8 * 1024 * 1024
IMPORT_MAX_ROWS = importer.MAX_ROWS

MAX_AMOUNT_CENTS = 10_000_000     # 超过 10 万要二次确认
HARD_MAX_CENTS = 100_000_000      # 超过 100 万直接拒绝（确认了也不行）
MAX_TEXT_LEN = 200
MAX_EDIT_TEXT_LEN = 100           # 编辑「做了什么」的上限：按字符数算，一个汉字算 1

# 一句话里可能有好几笔，按这些标点拆开
CLAUSE_SPLIT = re.compile(r"[，,。；;、\n]+")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """服务启动时建表。第一次跑会自动创建 data/app.db。"""
    storage.init()
    _migrate_legacy_budget()
    yield


def _migrate_legacy_budget() -> None:
    """老版本的「每月默认预算」存在 settings.json 里，搬进 budget_defaults 一次就够。

    不做这一步的话，升级后用户原来设的预算会凭空消失（表里没段、json 里那个字段没人读了）。
    只在「表里还没有任何段」时搬，避免每次启动都盖掉用户后来改的值。
    """
    legacy = runtime.load().get("monthly_budget")
    if not legacy:
        return
    today = date.today().strftime("%Y-%m")
    if storage.get_default_budget(today) is not None:
        return
    try:
        cents = int(round(float(legacy) * 100))
    except (TypeError, ValueError):
        return
    if cents > 0:
        storage.set_default_budget(cents, today)


app = FastAPI(title="记账小助手", version="0.1", lifespan=lifespan)

# 音效是静态文件，直接把 audio_effects/ 挂出去；目录不存在就建一个空的
audio_fx.SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio_effects", StaticFiles(directory=str(audio_fx.SOUNDS_DIR)), name="audio_effects")


class NewTransaction(BaseModel):
    """POST /api/transactions 的请求体。缺字段时 FastAPI 会自动报 422。"""
    text: str
    amount_cents: int | None = None
    occurred_at: str | None = None
    category: str | None = None
    kind: str | None = None            # expense / income，不传就由规则猜
    confirm_large: bool = False        # 用户已经点过「确认」就用 true 重发


class EditTransaction(BaseModel):
    """PATCH 的请求体：编辑框里是「做了什么 / 金额 / 方向 / 什么时候」。"""
    text: str
    amount_cents: int | None = None
    kind: str | None = None            # 传了才改方向，不传保留原来的
    occurred_at: str | None = None     # 2026-11-23 或 2026-11-23 14:30
    confirm_large: bool = False


class QueryRequest(BaseModel):
    question: str


class SettingsBody(BaseModel):
    asr_backend: str | None = None
    api_key: str | None = None          # 传空字符串 = 清空；不传 = 不改
    live2d: bool | None = None
    theme: str | None = None
    monthly_budget: float | None = None  # 每月默认预算（元）。传 0 = 不用预算


class BudgetBody(BaseModel):
    """POST /api/budget 的请求体：给某个月单独设预算。"""
    month: str | None = None            # 不传就是本月
    yuan: float | None = None           # 不传 / 0 = 清除，回到跟随默认


class ImportRow(BaseModel):
    """导入的一行账。前端把预览时那份数据原样传回来，字段名跟预览返回的一致。"""
    text: str
    amount_cents: int
    occurred_at: str
    kind: str | None = None
    category: str | None = None


class ImportBody(BaseModel):
    """POST /api/import/commit 的请求体。"""
    rows: list[ImportRow]


_transcriber = None


def current_key() -> str:
    """设置面板里填的 key 优先，其次是 .env 里的。"""
    return runtime.api_key() or DEEPSEEK_API_KEY


def get_transcriber():
    """第一次用到时再初始化；换后端或换 key 时会被重置，下一次识别就用新的。"""
    global _transcriber
    if _transcriber is None:
        _transcriber = asr.create_transcriber(runtime.asr_backend(), current_key() or None)
    return _transcriber


def reset_transcriber() -> None:
    global _transcriber
    _transcriber = None


def check_occurred_at(value: str | None) -> str | None:
    """校验「记账时间」，接受 `2026-11-23` 或 `2026-11-23 14:30`。

    闰年、月份天数、小时分钟范围**不用手写**：strptime 会把 2026-02-30
    和 25:00 都当成错误抛出来，标准库已经算好了。
    返回归一化后的字符串，空值原样返回。
    """
    if value is None:
        return None
    raw = value.strip().replace("T", " ")
    if not raw:
        return None

    parsed = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise HTTPException(
            status_code=400,
            detail="日期格式不对：写 2026-11-23 或 2026-11-23 14:30")

    # 用户说「年月日大于 1970 就行」，这里照办；上限给一年，防止手滑打成 2999
    if parsed.year < 1970:
        raise HTTPException(status_code=400, detail="日期最早只能到 1970 年")
    if parsed > datetime.now() + timedelta(days=366):
        raise HTTPException(status_code=400, detail="日期太远了（最多往后一年）")

    # 只给了日期就别硬塞一个 00:00，保持「只记到天」的记录干净
    return parsed.strftime("%Y-%m-%d") if len(raw) <= 10 else parsed.strftime("%Y-%m-%d %H:%M")


def check_amount(amount: int | None, confirm_large: bool = False) -> None:
    """金额校验，新增和编辑共用。

    超过 10 万：返回一个带 code 的错误，前端据此弹「二次确认」；
    用户确认后用 confirm_large=true 重发，才会放行。
    超过 100 万：确认了也不行，直接拒绝。
    """
    if amount is None:
        return
    if amount <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于 0（暂不支持退款和收入）")
    if amount > HARD_MAX_CENTS:
        raise HTTPException(
            status_code=400,
            detail=f"金额超过 100 万（{storage.yuan(amount)} 元），确认了也不收")
    if amount > MAX_AMOUNT_CENTS and not confirm_large:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "amount_too_large",
                "message": f"金额较大：{storage.yuan(amount)} 元，需要再确认一次",
                "amount_yuan": storage.yuan(amount),
            })


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """根路径返回那个记账页面。接口说明在 /docs。"""
    return FileResponse(TEMPLATES / "index.html")


@app.get("/api/sounds")
def sound_manifest() -> dict:
    """告诉前端有哪些音效可用。没有的就是 null，前端会跳过播放继续录音。"""
    return audio_fx.manifest()


def _background_file() -> Path | None:
    """当前卡片背景文件；没设过、或者文件被删了，都返回 None。"""
    name = str(runtime.load().get("background") or "")
    if not name:
        return None
    path = BACKGROUND_DIR / name
    return path if path.is_file() else None


def _settings_payload() -> dict:
    """设置接口的返回：key 只给脱敏形式；顺带告诉前端卡片有没有背景图。"""
    data = runtime.load()
    key = current_key()
    data.pop("api_key", None)
    data["has_key"] = bool(key)
    data["key_hint"] = runtime.mask_key(key)
    # 界面上**配过**的 key（不含 .env）。重置按钮只在真的配过时才显示，
    # 否则「重置」按下去什么也没变（还回落到 .env 那份），会让人以为没生效
    data["has_ui_key"] = bool(runtime.api_key())
    data["has_background"] = _background_file() is not None
    # 默认预算的真相在 budget_defaults 表里（分段存），settings.json 里那个字段只是历史残留，
    # 这里用「当前月生效的那个数」盖掉它，前端拿到的永远是现在真正在用的值
    current = storage.get_default_budget(date.today().strftime("%Y-%m"))
    data["monthly_budget"] = (current or 0) / 100
    return data


@app.get("/api/settings")
def read_settings() -> dict:
    """给设置面板用的状态。**只回脱敏后的 key**，完整 key 永远不出后端。"""
    return _settings_payload()


@app.post("/api/settings")
def write_settings(body: SettingsBody) -> dict:
    """改设置。改完立刻重置识别器，下一次录音就用新的，不用重启。"""
    if body.asr_backend not in (None, "local", "cloud", "auto"):
        raise HTTPException(status_code=400, detail="asr_backend 只能是 local / cloud / auto")
    if body.api_key is not None and len(body.api_key) > 200:
        raise HTTPException(status_code=400, detail="key 太长了")
    if body.theme not in (None, "light", "dark", "auto"):
        raise HTTPException(status_code=400, detail="theme 只能是 light / dark / auto")

    updates: dict = {
        "asr_backend": body.asr_backend,
        "live2d": body.live2d,
        "theme": body.theme,
    }
    if body.monthly_budget is not None:
        # 默认预算不再塞进 settings.json：改成写 budget_defaults 的「区间」，
        # 这样它只管「从现在开始」，回看以前的月份还是当时那个数
        today = date.today().strftime("%Y-%m")
        before = storage.get_default_budget(today)
        after = int(round(check_budget_yuan(body.monthly_budget) * 100)) or None
        if after != before:                       # 值没变就别刷历史记录
            storage.set_default_budget(after, today)
            storage.log_budget("default", today, before, after)
    if body.api_key is not None:
        updates["api_key"] = body.api_key.strip()
    runtime.save(updates)
    reset_transcriber()
    return _settings_payload()


@app.get("/api/background")
def get_background() -> FileResponse:
    """当前卡片背景图。没设过就 404，前端据此回退到默认渐变。"""
    path = _background_file()
    if path is None:
        raise HTTPException(status_code=404, detail="还没有设置背景图")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.post("/api/background")
async def upload_background(file: UploadFile = File(...)) -> dict:
    """换一张卡片背景。只留一份，换新的会把旧的删掉。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in BG_SUFFIXES:
        raise HTTPException(status_code=400, detail="只认 png / jpg / webp / gif")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(raw) > BG_MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片太大了，换 8 MB 以内的")

    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    for old in BACKGROUND_DIR.glob("bg.*"):
        old.unlink(missing_ok=True)       # 只留一张，别让旧图堆在数据卷里
    name = "bg" + suffix
    (BACKGROUND_DIR / name).write_bytes(raw)
    runtime.save({"background": name})
    return {"ok": True, "name": name}


@app.delete("/api/background")
def clear_background() -> dict:
    """恢复默认渐变，顺手把文件删掉。"""
    for old in BACKGROUND_DIR.glob("bg.*"):
        old.unlink(missing_ok=True)
    runtime.save({"background": ""})
    return {"ok": True}


@app.get("/api/stats")
def get_stats(month: str | None = None) -> dict:
    """分类聚合 + 近 6 个月趋势。month 形如 2026-11，不传就是本月。"""
    return storage.stats(check_month(month))


@app.post("/api/query")
def ask_query(body: QueryRequest) -> dict:
    """自然语言查账：问题 -> DSL -> SQL -> 一句话。

    返回里带 dsl，前端可以展示「我把它理解成了什么」，不是黑箱。
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能是空的")
    if len(question) > 100:
        raise HTTPException(status_code=400, detail="问题太长了（最多 100 字）")
    # 规则看不懂时，query.ask 会拿这个 key 去问模型「这到底是不是在问账」
    return query.ask(question, api_key=current_key())


def check_month(value: str | None) -> str:
    """校验 YYYY-MM，返回它；不传就是本月。

    光靠正则不够：`2026-13` 也符合 \\d{4}-\\d{2}，月份得单独卡 01~12。
    """
    ym = value or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", ym):
        raise HTTPException(status_code=400, detail="月份格式应该是 2026-11")
    return ym


@app.get("/api/summary")
def summary(month: str | None = None) -> dict:
    """某个月的支出 / 收入 / 结余 / 预算 + 最近 10 条。month 形如 2026-11，不传就是本月。"""
    ym = check_month(month)
    expense = storage.month_total(ym, kind="expense")
    income = storage.month_total(ym, kind="income")
    budget, budget_src = _budget_for(ym)
    return {
        "month": ym,
        # total_cents / total_yuan 保留：老前端和 README 都按「本月支出」在用
        "total_cents": expense,
        "total_yuan": storage.yuan(expense),
        "expense_cents": expense,
        "income_cents": income,
        "balance_cents": income - expense,
        "expense_yuan": storage.yuan(expense),
        "income_yuan": storage.yuan(income),
        "balance_yuan": storage.yuan(income - expense),
        # 预算：None 表示这个月没设（前端显示「未设置」，别拿 0 当预算用）
        "budget_cents": budget,
        "budget_source": budget_src,
        "recent": storage.list_recent(10),
    }


def check_budget_yuan(value: float | None) -> float:
    """预算校验：单位元，0 表示「不用预算」，所以允许；负数和大数挡住。

    预算值本身也要过一遍校验——它也是一笔钱，不能因为来自设置面板就当成可信的。
    """
    if value is None:
        return 0.0
    if value != value:                      # NaN：TypeError 之前先挡住
        raise HTTPException(status_code=400, detail="预算得是一个数")
    if value < 0:
        raise HTTPException(status_code=400, detail="预算不能是负数")
    if value > 1_000_000:
        raise HTTPException(status_code=400, detail="预算最多 100 万元")
    return round(value, 2)


def _budget_for(ym: str) -> tuple[int | None, str | None]:
    """某个月真正生效的预算：(金额分, 来源)。

    来源 custom = 这个月单独设过；default = 当时那段默认预算覆盖到了这个月；
    None = 那个月压根没设过预算。

    注意 default 是**按月份去查当时生效的那一段**，不是拿今天的默认值套上去：
    2026-11 你有 3000、12 月改成 4000，回看 11 月仍然是 3000。
    老月份没有对应的段，就是「没设过」，不会凭空冒出一个今天的数字。
    """
    custom = storage.get_budget(ym)
    if custom is not None:
        return custom, "custom"
    default = storage.get_default_budget(ym)
    if default:
        return default, "default"
    return None, None


@app.post("/api/budget")
def write_budget(body: BudgetBody) -> dict:
    """给某个月单独设预算。yuan 传 0 / null 就是清除，回到「跟随默认」。

    存的是**绝对值**，不是相对默认值的差额：11 月设了 3500，之后把默认值改成 4000，
    11 月还是 3500。历史不该被后来的设置改写。
    """
    ym = check_month(body.month)
    before = storage.get_budget(ym)
    after = int(round(check_budget_yuan(body.yuan) * 100)) or None
    if after != before:                          # 值没变就别刷历史记录
        storage.set_budget(ym, after)
        storage.log_budget("month", ym, before, after)
    budget, src = _budget_for(ym)
    return {"month": ym, "budget_cents": budget, "budget_source": src}


@app.get("/api/transactions")
def list_transactions(limit: int = 10, offset: int = 0) -> dict:
    """流水列表，支持翻页。limit 是每页条数，offset 是跳过多少条。"""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    return {
        "items": storage.list_recent(limit, offset),
        "total": storage.count_transactions(),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/transactions/locate")
def locate_transaction(start: str, end: str) -> dict:
    """「这段账在流水第几页」：返回它前面有多少条。

    查账问的是日期，流水排的是录入顺序，两边对不上——所以由后端数一下位置，
    前端把列表翻到那一页（详见 storage.locate_offset 的注释）。
    """
    lo = check_occurred_at(start)
    hi = check_occurred_at(end)
    if lo is None or hi is None:
        raise HTTPException(status_code=400, detail="start / end 要写 2026-11-25 这种日期")
    offset = storage.locate_offset(lo, query.end_exclusive(hi))
    return {"ok": True, "offset": offset, "month": lo[:7]}


def _model_date(value) -> str | None:
    """模型给的日期：格式不对、或者根本不是个真实日期，就当没给。

    不依赖 llm._validate()——那一层是「第一道」，这里再挡一次，
    免得哪天有人绕过、或者 prompt 改坏，脏日期直接落库。
    """
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _model_time(value) -> str | None:
    """模型给的时刻：只认 HH:MM，且 0~23 / 0~59。"""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _resolve(text: str, fallback: NewTransaction, budget: list[int]) -> dict:
    """解析一段文字（整句或一个分句），返回统一结构。

    budget 是个只有一项的列表：一次请求里最多问几次模型，
    防止「一句话拆出五段」就调五次接口。

    日期/时间的合成规则：
    - 调用方显式给了 occurred_at → 用它，不再问模型
    - 规则读到了日期（date_hit）→ 用规则的
    - 规则只是兜底到今天（date_hit=False）→ 允许模型给一个日期
    时间同理（time_hit）。**模型只能补规则没读到的部分，不能推翻读到的。**
    """
    p = parser.parse(text)
    amount = fallback.amount_cents if fallback.amount_cents is not None else p["amount_cents"]
    category = fallback.category or p["category"]
    kind = fallback.kind if fallback.kind in storage.KINDS else p["kind"]
    used_llm = False

    # 拆成「日期」「时刻」两段，方便只补其中一半
    rule_when = check_occurred_at(fallback.occurred_at) or p["occurred_at"]
    date_part = rule_when[:10]
    time_part = rule_when[11:16] if len(rule_when) > 10 else ""
    date_locked = bool(fallback.occurred_at) or p["date_hit"]
    time_locked = bool(fallback.occurred_at) or p["time_hit"]

    need_model = (amount is None or category is None
                  or not date_locked or not time_locked)
    if need_model and budget[0] > 0 and current_key():
        guess = llm.parse_text(text, current_key())
        budget[0] -= 1
        if guess:
            used_llm = True
            amount = amount if amount is not None else guess["amount_cents"]
            category = category or guess["category"]
            # 模型也可以纠正方向（「收了个红包」规则没词的时候），但不许改金额
            if fallback.kind not in storage.KINDS and guess.get("kind") in storage.KINDS:
                kind = guess["kind"]
            if not date_locked and (got := _model_date(guess.get("date"))):
                date_part = got
            if not time_locked and (got := _model_time(guess.get("time"))):
                time_part = got

    occurred = date_part + (" " + time_part if time_part else "")

    # AA：金额按句子里说的人数平摊。
    # 除得尽就直接改；除不尽时 amount 先填「每人约多少」，并打上 aa_confirm，
    # 由 create() 走「弹编辑框让你确认」那条路——不替你四舍五入。
    aa_count = p["aa_count"]
    aa_confirm = False
    aa_note = ""
    if aa_count and amount is not None:
        total = amount
        if total % aa_count == 0:
            amount = total // aa_count
        else:
            amount = round(total / aa_count)
            aa_confirm = True
            aa_note = (f"{storage.yuan(total)} 元按 {aa_count} 人 AA 除不尽，"
                       f"每人约 {storage.yuan(amount)} 元，核对一下具体金额")

    return {"text": text, "amount": amount, "occurred": occurred,
            "category": category, "kind": kind, "vague": p["vague_date"],
            "hits": p["hits"], "llm": used_llm,
            "aa_count": aa_count, "aa_confirm": aa_confirm, "aa_note": aa_note}


def _pack(item: dict, new_id: int) -> dict:
    return {
        "id": new_id,
        "parsed": {
            "amount_cents": item["amount"],
            "amount_yuan": storage.yuan(item["amount"]),
            "occurred_at": item["occurred"],
            "category": item["category"],
            "kind": item["kind"],
            "hits": item["hits"],
            "llm": item["llm"],
            "aa_count": item.get("aa_count"),
        },
    }


def _aa_draft(item: dict) -> dict:
    """AA 除不尽时的草稿：复用「补一笔账」那个编辑框，金额先填个近似值。"""
    return {"ok": True, "need_amount": True, "draft": {
        "text": item["text"], "occurred": item["occurred"],
        "category": item["category"], "kind": item["kind"], "llm": item["llm"],
        "amount_cents": item["amount"], "note": item["aa_note"]}}


def check_vague_date(item: dict, given: str | None) -> None:
    """「最近」「上周」这种说不清的日期，宁可拦下来问一句。

    默默记成今天等于替用户编了个日期，比报错更糟——和「没看懂就说没看懂」是同一个原则。
    调用方显式给了日期（接口传 occurred_at）就放行。
    """
    if item["vague"] and not given:
        raise HTTPException(
            status_code=400,
            detail=f"「{item['vague']}」听不出具体是哪天。换成「昨天」「前天」这种说法，"
                   f"或者先记一笔、再用「编辑」改日期。")


@app.post("/api/transactions")
def create_transaction(tx: NewTransaction) -> dict:
    """存账。一句话里有多笔消费就按标点拆开，各存一条。

    例：「昨天打车32，今天吃饭15」→ 两条账。
    分句里没有金额的不会凭空造一笔，但**也不会被扔掉**：
    紧挨着有金额那句的，就拼进那一笔的文字里（「及你太美22块，我爱蔡徐坤」
    → 一笔 22 块，文字是原话）。一句金额都没有才报错。
    """
    if len(tx.text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"这句话太长了（最多 {MAX_TEXT_LEN} 字）")

    budget = [2]
    clauses = [c.strip() for c in CLAUSE_SPLIT.split(tx.text) if c.strip()]

    # 只有「没显式给金额」时才拆句；给了金额说明调用方自己决定好了一笔
    if tx.amount_cents is None and len(clauses) > 1:
        # 先把每一句都校验完，再统一写库：不然第二句被拦下时，第一句已经存进去了，
        # 接口返回 400 但数据落了库，用户重试就会多出一笔重复账。
        resolved = []
        loose = []                            # 还没找到归属的「没金额」分句
        for clause in clauses:
            item = _resolve(clause, tx, budget)
            if item["amount"] is None:
                loose.append(clause)          # 先攒着，看能不能挂到某笔上
                continue
            if loose:                         # 挂到这一笔前面：「和同事AA，打车32」
                item["text"] = "，".join(loose + [item["text"]])
                loose = []
            check_vague_date(item, tx.occurred_at)
            check_amount(item["amount"], tx.confirm_large)
            resolved.append(item)

        # 尾巴上还剩下的（「及你太美22块，我爱蔡徐坤」），并进最后一笔
        if loose and resolved:
            merged = resolved[-1]["text"] + "，" + "，".join(loose)
            resolved[-1]["text"] = merged[:MAX_TEXT_LEN]

        if resolved:
            # AA 除不尽：这一批先一条都不写，让用户把那一笔的金额确认了再说。
            # （校验完再统一写库的理由见上面：不能出现「400 但已经存了一半」）
            pending = next((it for it in resolved if it["aa_confirm"]), None)
            if pending is not None:
                return _aa_draft(pending)
            items = []
            for item in resolved:
                try:
                    new_id = storage.add(item["text"], item["amount"],
                                         item["occurred"], item["category"],
                                         kind=item["kind"])
                except sqlite3.Error as exc:
                    raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc
                items.append(_pack(item, new_id))
            return {"ok": True, "multi": True, "items": items}

    # 拆不开，或者拆出来一笔带金额的都没有 —— 按整句存一条
    item = _resolve(tx.text, tx, budget)
    # 先报日期听不懂：这句话可能两样都缺（「最近花了一千多」），日期更好改
    check_vague_date(item, tx.occurred_at)
    # 认不出金额不再报错：回一份草稿，前端弹编辑窗让人补个数。
    # 能读出来的（文字、日期、类别、方向）都先填进去，用户只需要填金额。
    # 为什么不直接 400：那句话是他刚打的，丢掉要重打一遍——留着让他改更省事。
    if item["amount"] is None:
        return {
            "ok": True,
            "need_amount": True,
            "draft": {
                "text": item["text"],
                "occurred": item["occurred"],
                "category": item["category"],
                "kind": item["kind"],
                "llm": item["llm"],
            },
        }
    # AA 除不尽：同样弹编辑框，金额先填每人约多少
    if item["aa_confirm"]:
        return _aa_draft(item)
    check_amount(item["amount"], tx.confirm_large)
    try:
        new_id = storage.add(item["text"], item["amount"], item["occurred"],
                             item["category"], kind=item["kind"])
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc

    return {"ok": True, "id": new_id, "parsed": _pack(item, new_id)["parsed"]}


@app.patch("/api/transactions/{tx_id}")
def edit_transaction(tx_id: int, body: EditTransaction) -> dict:
    """编辑一条账：只改「做了什么」和「金额」。

    类别会跟着新文字重新猜一次；猜不出来就保留原来的。
    updated_at 由服务端写，这就是「修改时间只在编辑后才有」。
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="「做了什么」不能是空的")
    # 编辑框比记账框收得更紧：记账是原话，可以长一点；编辑是在改一句话的标题
    if len(text) > MAX_EDIT_TEXT_LEN:
        raise HTTPException(
            status_code=400, detail=f"太长了（最多 {MAX_EDIT_TEXT_LEN} 字）")

    old = storage.get(tx_id)
    if old is None:
        raise HTTPException(status_code=404, detail="这条账不存在")

    if body.amount_cents is None:
        raise HTTPException(status_code=400, detail="金额不能为空")
    if body.kind is not None and body.kind not in storage.KINDS:
        raise HTTPException(status_code=400, detail="方向只能是 expense（支出）或 income（收入）")
    check_amount(body.amount_cents, body.confirm_large)
    when = check_occurred_at(body.occurred_at) or old["occurred_at"]
    parsed = parser.parse(text)
    category = parsed["category"] or old["category"]
    # 只改金额 / 日期时不动方向；文字变了才重新猜（「午饭」改成「发工资」要变收入）
    if body.kind in storage.KINDS:
        kind = body.kind
    elif text != old["text"]:
        kind = parsed["kind"]
    else:
        kind = old["kind"]

    storage.update(tx_id, text, body.amount_cents, category, kind, when)
    return {"ok": True, "item": storage.get(tx_id)}


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int) -> dict:
    """删除一条账（软删除，可撤销）。"""
    if not storage.delete(tx_id):
        raise HTTPException(status_code=404, detail="这条账不存在，或者已经删了")
    return {"ok": True}


@app.post("/api/transactions/{tx_id}/restore")
def restore_transaction(tx_id: int) -> dict:
    """恢复一条被删的账。"""
    if not storage.restore(tx_id):
        raise HTTPException(status_code=404, detail="这条账不存在，或者本来就没删")
    return {"ok": True}


@app.get("/api/operations")
def list_operations(limit: int = 100) -> list[dict]:
    """操作历史：增 / 改 / 删 / 恢复，最新在上面。"""
    return storage.operations(limit)


@app.post("/api/operations/{op_id}/undo")
def undo_operation(op_id: int) -> dict:
    """撤销一条操作（新增↔删除、编辑↔还原）。"""
    if not storage.undo(op_id):
        raise HTTPException(status_code=404, detail="这条操作不存在，或者已经撤销过")
    return {"ok": True}


@app.delete("/api/operations/{op_id}")
def delete_operation(op_id: int) -> dict:
    """删掉一条历史记录（只清理记录，不改账目）。"""
    if not storage.delete_operation(op_id):
        raise HTTPException(status_code=404, detail="这条记录不存在")
    return {"ok": True}


class QueryLog(BaseModel):
    """POST /api/queries 的请求体：只记问题本身。"""
    question: str


@app.get("/api/queries")
def list_queries(limit: int = 60) -> list[dict]:
    """查账历史：问过些什么，最新在上面。"""
    return storage.queries(max(1, min(limit, 200)))


@app.post("/api/queries")
def log_query(body: QueryLog) -> dict:
    """记一条查账历史。重复问同一句也会记（用户明确要的）。"""
    q = body.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="问题不能是空的")
    if len(q) > 100:
        raise HTTPException(status_code=400, detail="问题太长了（最多 100 字）")
    return {"ok": True, "id": storage.add_query(q)}


@app.delete("/api/queries/{query_id}")
def delete_query(query_id: int) -> dict:
    """删掉一条查账历史。"""
    if not storage.delete_query(query_id):
        raise HTTPException(status_code=404, detail="这条记录不存在")
    return {"ok": True}


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict:
    """浏览器录一段 wav 传上来，转成文字。转完就把音频删掉。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="没收到音频")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="音频太大了（上限 8 MB）")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"asr_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
    path.write_bytes(raw)
    try:
        text = get_transcriber().transcribe(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"识别失败：{exc}") from exc
    finally:
        path.unlink(missing_ok=True)      # 语音是隐私，转完即删

    return {"ok": True, "text": text}


# ---------- 小票 OCR ----------
# 跟导入一样：只读、只回草稿，入库仍要走用户点一次「保存」。

@app.post("/api/receipt")
async def read_receipt(file: UploadFile = File(...)) -> dict:
    """上传一张小票 / 支付截图，读成一条账目草稿。**不写库**。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in receipt.SUFFIX_MIME:
        raise HTTPException(status_code=400, detail="只认 png / jpg / webp / gif 的图片")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="图片是空的")
    if len(raw) > receipt.MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片太大了（上限 8 MB）")

    draft = receipt.read_image(raw, suffix, current_key())
    if draft is None:
        # 读不懂就把原因原样给用户：是没 key、接口挂了、还是图里真没有金额
        raise HTTPException(status_code=400, detail=receipt.LAST_ERROR or "这张图没读懂")

    occurred = draft["date"] or date.today().isoformat()
    if draft["time"]:
        occurred += " " + draft["time"]
    return {
        "ok": True,
        "text": draft["text"],
        "amount_cents": draft["amount_cents"],
        "occurred_at": occurred,
        "category": parser.find_category(draft["text"])[0],
        "kind": "expense",              # 小票默认是花钱；方向要改去编辑框里改文字
        "merchant": draft["merchant"],
        "items": draft["items"],
    }


# ---------- 账单导入 ----------
# 两步走：先 /preview 只解析、不入库，用户看清楚「我读成了什么」；
# 确认之后才 /commit 写库。点一下就直接灌库的话，读错一列得一条条删。

@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...)) -> dict:
    """上传一份 CSV，只解析、不写库。返回全部行（前端只展示前 20 条）。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMPORT_SUFFIXES:
        raise HTTPException(status_code=400, detail="只认 .csv / .txt 文件")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(raw) > IMPORT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="文件太大了（上限 8 MB）")

    try:
        parsed = importer.parse_bytes(raw)
    except importer.ImportParseError as exc:
        # 解析层抛的是给人看的一句话，原样透给前端
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **parsed}


@app.post("/api/import/commit")
def import_commit(body: ImportBody) -> dict:
    """把预览过的那份行写进库。

    前端回传的行**全部重新校验**：预览和入库之间隔着一个可以改的 JSON，
    而且金额、日期、类别本来就该跟手记账过同一套检查。
    """
    if not body.rows:
        raise HTTPException(status_code=400, detail="没有要导入的账目")
    if len(body.rows) > IMPORT_MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"一次最多导 {IMPORT_MAX_ROWS} 行")

    clean: list[dict] = []
    bad: list[dict] = []
    for index, row in enumerate(body.rows, 1):
        text = (row.text or "").strip()[:MAX_TEXT_LEN]
        if not text:
            bad.append({"line": index, "reason": "没有说明文字"})
            continue
        try:
            when = check_occurred_at(row.occurred_at)
            check_amount(row.amount_cents, confirm_large=True)
        except HTTPException as exc:
            bad.append({"line": index, "reason": str(exc.detail)})
            continue
        if when is None:
            bad.append({"line": index, "reason": "没有日期"})
            continue

        kind = row.kind if row.kind in storage.KINDS else parser.parse(text)["kind"]
        # 类别只认白名单里的七种；传了别的值就当没传，按文字重新猜
        category = row.category if row.category in parser.CATEGORY_WORDS else None
        if category is None:
            category = parser.parse(text)["category"]
        clean.append({"text": text, "amount_cents": row.amount_cents,
                      "occurred_at": when, "category": category, "kind": kind})

    # 去重键跟手记账一致：(记账时间, 金额, 文字)。
    # 同一批里出现两次也算重复，所以边查边往集合里塞。
    existing = storage.tx_keys()
    fresh: list[dict] = []
    duplicated = 0
    for item in clean:
        key = (item["occurred_at"], item["amount_cents"], item["text"])
        if key in existing:
            duplicated += 1
            continue
        existing.add(key)
        fresh.append(item)

    try:
        ids = storage.add_many(fresh)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc

    # 导入完把涉及到的月份告诉前端：卡片要跳到看得见这批账的那一个月
    months = sorted({item["occurred_at"][:7] for item in fresh})
    return {
        "ok": True,
        "imported": len(ids),
        "duplicated": duplicated,
        "skipped": len(bad),
        "errors": bad[:20],
        "months": months,
    }
