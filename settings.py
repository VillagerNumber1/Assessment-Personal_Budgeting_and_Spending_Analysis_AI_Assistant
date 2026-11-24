"""运行时设置：存在 `data/settings.json`，改完立刻生效，不用重启。

和 `.env` 的分工：
    `.env`       启动配置 + 密钥（不进仓库，也不进镜像）
    settings.json 用户在界面上改的东西（跟着 data/ 卷走）

优先级：settings.json > .env > 代码默认值。
"""
import json
from pathlib import Path

from config import ASR_BACKEND, DATA_DIR

PATH = DATA_DIR / "settings.json"
DEFAULTS = {
    "asr_backend": ASR_BACKEND,
    "live2d": True,          # 看板娘默认开（具体渲染见 HANDOFF_LIVE2D.md）
    "theme": "auto",         # light / dark / auto
    "monthly_budget": 0,     # 每月默认预算（元）。0 = 没设，卡片上不显示预算
}


def load() -> dict:
    """读设置。文件不存在或坏了就退回默认值，绝不因为设置文件崩服务。"""
    data = dict(DEFAULTS)
    if PATH.exists():
        try:
            data.update(json.loads(PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return data


def save(updates: dict) -> dict:
    """改几个键，其余保持不变。"""
    data = load()
    for key, value in updates.items():
        if value is not None:
            data[key] = value
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def asr_backend() -> str:
    """语音识别走本地还是云端。"""
    return str(load().get("asr_backend") or "auto")


def api_key() -> str:
    """界面上填的 key。空字符串表示没填（调用方再回退到 .env）。"""
    return str(load().get("api_key") or "").strip()


def monthly_budget() -> int:
    """每月默认预算，单位**分**。0 表示没设。

    json 里存的是元（给人看），这里统一换成和账目一样的「分」，免得两套单位打架。
    文件被手改坏了也不抛异常，退回 0 就行——预算坏了不该拖垮整个服务。
    """
    try:
        return max(0, int(round(float(load().get("monthly_budget") or 0) * 100)))
    except (TypeError, ValueError):
        return 0


def mask_key(key: str) -> str:
    """给界面看的脱敏形式：sk-1****abcd。永远不要把完整 key 发回前端。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]
