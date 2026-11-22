"""录音音效：找文件、报清单。

定位：**支线功能**。有音效就播，没有就静音直接继续——绝不因为缺文件卡住录音。

四个时机：
    start     点 🎤 之后、开麦之前播（播完才开始采集，避免被录进去）
    stop      停止录音之后播
    success   识别出文字、并且成功入库之后播
    fail      没识别到文字，或入库失败时播

换音效：把同名文件丢进 audio_effects/ 即可，支持 .ogg / .wav / .mp3。
注意扩展名要和真实格式一致——浏览器按扩展名给 Content-Type，
MP3 存成 .ogg 有可能播不出来（这个坑踩过一次）。

为什么单独一个文件：
浏览器不知道服务端有没有音效，所以先由这里探一遍，再把可用地址发给前端；
探不到就返回 None，前端跳过播放。这样「缺文件」不会变成一个错误。
"""
from pathlib import Path

from config import BASE_DIR

SOUNDS_DIR = BASE_DIR / "audio_effects"
NAMES = ("start", "stop", "success", "fail")
EXTS = (".ogg", ".wav", ".mp3")
URL_PREFIX = "/audio_effects"


def find(name: str) -> Path | None:
    """找一个可用的音效文件，找不到返回 None。空文件也算没有。"""
    for ext in EXTS:
        path = SOUNDS_DIR / f"{name}{ext}"
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def manifest() -> dict[str, str | None]:
    """给前端的清单：{start: '/audio_effects/start.ogg', stop: None, ...}"""
    result: dict[str, str | None] = {}
    for name in NAMES:
        path = find(name)
        result[name] = f"{URL_PREFIX}/{path.name}" if path else None
    return result
