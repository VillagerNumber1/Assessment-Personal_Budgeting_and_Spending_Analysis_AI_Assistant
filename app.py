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
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import asr
import audio_effectsRecovery as audio_fx
import llm
import parser
import query
import settings as runtime
import storage
from config import DATA_DIR, DEEPSEEK_API_KEY

TEMPLATES = Path(__file__).parent / "templates"
UPLOAD_DIR = DATA_DIR / "uploads"

MAX_AMOUNT_CENTS = 10_000_000     # 超过 10 万要二次确认
HARD_MAX_CENTS = 100_000_000      # 超过 100 万直接拒绝（确认了也不行）
MAX_TEXT_LEN = 200

# 一句话里可能有好几笔，按这些标点拆开
CLAUSE_SPLIT = re.compile(r"[，,。；;、\n]+")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """服务启动时建表。第一次跑会自动创建 data/app.db。"""
    storage.init()
    yield


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
    confirm_large: bool = False        # 用户已经点过「确认」就用 true 重发


class EditTransaction(BaseModel):
    """PATCH 的请求体：编辑框里只有「做了什么」和「金额」。"""
    text: str
    amount_cents: int | None = None
    confirm_large: bool = False


class QueryRequest(BaseModel):
    question: str


class SettingsBody(BaseModel):
    asr_backend: str | None = None
    api_key: str | None = None          # 传空字符串 = 清空；不传 = 不改


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


@app.get("/api/settings")
def read_settings() -> dict:
    """给设置面板用的状态。**只回脱敏后的 key**，完整 key 永远不出后端。"""
    data = runtime.load()
    key = current_key()
    data.pop("api_key", None)
    data["has_key"] = bool(key)
    data["key_hint"] = runtime.mask_key(key)
    return data


@app.post("/api/settings")
def write_settings(body: SettingsBody) -> dict:
    """改设置。改完立刻重置识别器，下一次录音就用新的，不用重启。"""
    if body.asr_backend not in (None, "local", "cloud", "auto"):
        raise HTTPException(status_code=400, detail="asr_backend 只能是 local / cloud / auto")
    if body.api_key is not None and len(body.api_key) > 200:
        raise HTTPException(status_code=400, detail="key 太长了")

    updates: dict = {"asr_backend": body.asr_backend}
    if body.api_key is not None:
        updates["api_key"] = body.api_key.strip()
    runtime.save(updates)
    reset_transcriber()

    data = runtime.load()
    key = current_key()
    data.pop("api_key", None)
    data["has_key"] = bool(key)
    data["key_hint"] = runtime.mask_key(key)
    return data


@app.get("/api/stats")
def get_stats(month: str | None = None) -> dict:
    """分类聚合 + 近 6 个月趋势。month 形如 2026-11，不传就是本月。"""
    return storage.stats(month)


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
    return query.ask(question)


@app.get("/api/summary")
def summary() -> dict:
    """本月总额 + 最近 10 条。全部只读 confirmed 的账。"""
    total = storage.month_total()
    return {
        "month": date.today().strftime("%Y-%m"),
        "total_cents": total,
        "total_yuan": storage.yuan(total),
        "recent": storage.list_recent(10),
    }


@app.get("/api/transactions")
def list_transactions(limit: int = 50) -> list[dict]:
    """流水列表。limit 是网址参数：/api/transactions?limit=5"""
    return storage.list_recent(limit)


def _resolve(text: str, fallback: NewTransaction, budget: list[int]) -> dict:
    """解析一段文字（整句或一个分句），返回统一结构。

    budget 是个只有一项的列表：一次请求里最多问几次模型，
    防止「一句话拆出五段」就调五次接口。
    """
    p = parser.parse(text)
    amount = fallback.amount_cents if fallback.amount_cents is not None else p["amount_cents"]
    category = fallback.category or p["category"]
    occurred = fallback.occurred_at or p["occurred_at"]
    used_llm = False

    if (amount is None or category is None) and budget[0] > 0 and current_key():
        guess = llm.parse_text(text, current_key())
        budget[0] -= 1
        if guess:
            used_llm = True
            amount = amount if amount is not None else guess["amount_cents"]
            category = category or guess["category"]

    return {"text": text, "amount": amount, "occurred": occurred,
            "category": category, "hits": p["hits"], "llm": used_llm}


def _pack(item: dict, new_id: int) -> dict:
    return {
        "id": new_id,
        "parsed": {
            "amount_cents": item["amount"],
            "amount_yuan": storage.yuan(item["amount"]),
            "occurred_at": item["occurred"],
            "category": item["category"],
            "hits": item["hits"],
            "llm": item["llm"],
        },
    }


@app.post("/api/transactions")
def create_transaction(tx: NewTransaction) -> dict:
    """存账。一句话里有多笔消费就按标点拆开，各存一条。

    例：「昨天打车32，今天吃饭15」→ 两条账。
    分句里没有金额的（「和同事AA」）直接跳过，不产生垃圾数据。
    """
    if len(tx.text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"这句话太长了（最多 {MAX_TEXT_LEN} 字）")

    budget = [2]
    clauses = [c.strip() for c in CLAUSE_SPLIT.split(tx.text) if c.strip()]

    # 只有「没显式给金额」时才拆句；给了金额说明调用方自己决定好了一笔
    if tx.amount_cents is None and len(clauses) > 1:
        items = []
        for clause in clauses:
            item = _resolve(clause, tx, budget)
            if item["amount"] is None:
                continue                      # 没金额的分句不记
            check_amount(item["amount"], tx.confirm_large)
            try:
                new_id = storage.add(item["text"], item["amount"],
                                     item["occurred"], item["category"])
            except sqlite3.Error as exc:
                raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc
            items.append(_pack(item, new_id))

        if items:
            return {"ok": True, "multi": True, "items": items}

    # 拆不开，或者拆出来一笔带金额的都没有 —— 按整句存一条
    item = _resolve(tx.text, tx, budget)
    check_amount(item["amount"], tx.confirm_large)
    try:
        new_id = storage.add(item["text"], item["amount"], item["occurred"], item["category"])
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
    if len(text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"太长了（最多 {MAX_TEXT_LEN} 字）")

    old = storage.get(tx_id)
    if old is None:
        raise HTTPException(status_code=404, detail="这条账不存在")

    check_amount(body.amount_cents, body.confirm_large)
    category = parser.parse(text)["category"] or old["category"]

    storage.update(tx_id, text, body.amount_cents, category)
    return {"ok": True, "item": storage.get(tx_id)}


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
