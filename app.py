"""记账服务的后端：一个常驻程序，监听端口，回答浏览器发来的请求。

启动（在项目根目录）：
    .\.venv\Scripts\python.exe -m uvicorn app:app --reload --port 8000

然后浏览器打开：
    http://127.0.0.1:8000/api/summary    看本月总额和最近流水
    http://127.0.0.1:8000/docs           自动生成的接口文档，可以直接点按钮试

这个文件只负责"把网址接到函数上"，真正的活都在 storage.py 里。
"""
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
import parser
import query
import storage
from config import DATA_DIR

TEMPLATES = Path(__file__).parent / "templates"
UPLOAD_DIR = DATA_DIR / "uploads"

MAX_AMOUNT_CENTS = 10_000_000     # 超过 10 万要二次确认
HARD_MAX_CENTS = 100_000_000      # 超过 100 万直接拒绝（确认了也不行）
MAX_TEXT_LEN = 200


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


_transcriber = None


def get_transcriber():
    """第一次用到时再初始化，没配 key 也不会拖垮整个服务启动。"""
    global _transcriber
    if _transcriber is None:
        _transcriber = asr.create_transcriber()
    return _transcriber


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


@app.post("/api/transactions")
def create_transaction(tx: NewTransaction) -> dict:
    """存一条账。没给金额/日期/类别时，用规则从原话里猜。"""
    if len(tx.text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"这句话太长了（最多 {MAX_TEXT_LEN} 字）")

    parsed = parser.parse(tx.text)
    amount = tx.amount_cents if tx.amount_cents is not None else parsed["amount_cents"]
    occurred = tx.occurred_at or parsed["occurred_at"]
    category = tx.category or parsed["category"]

    # 校验层：不管金额是用户传的还是解析出来的，都必须过这一关
    check_amount(amount, tx.confirm_large)

    try:
        new_id = storage.add(tx.text, amount, occurred, category)
    except sqlite3.Error as exc:            # 数据库写不进去，别让服务崩
        raise HTTPException(status_code=500, detail=f"写入失败：{exc}") from exc

    return {
        "ok": True,
        "id": new_id,
        "parsed": {
            "amount_cents": amount,
            "amount_yuan": storage.yuan(amount),
            "occurred_at": occurred,
            "category": category,
            "hits": parsed["hits"],
        },
    }


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
